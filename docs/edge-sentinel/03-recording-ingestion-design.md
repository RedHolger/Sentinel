# Stage 03 Design Specification: Recording Ingestion

**Stage ID:** `03-recording-ingestion`  
**Target Package:** `adi-app` (ingestion adapter), leveraging `adi-common` (validation) and feeding `adi-runtime` (feature extraction)  
**Supervisor:** Codex  
**Prerequisite:** Accepted Stage 02 (`reviews/edge-sentinel/02-window-features-repair-02.md`)

---

## 1. Overview & Architectural Role

The recording ingestion adapter provides a strict, deterministic boundary for loading local CSV vibration data and caller-supplied metadata into the accepted `VibrationRecordingV1` contract (`adi-common`).

```
Local CSV File (on disk) + Metadata Dict (caller)
                      │
                      ▼
       [ Read Raw Bytes Once & Hash ] ──> SHA-256 Digest (Exact bytes snapshot)
                      │
                      ▼
            [ Strict CSV Parsing ] ─────> Header subset (x,y,z), 2D Rectangular Matrix
                      │
                      ▼
       [ Contract Semantic Validation ] ─> validate_vibration_recording()
                      │
                      ▼
          VibrationRecordingV1 Dict ────> Feeds adi-runtime extract_window_features()
```

### Key Invariant Guarantees:
1. **Single Read & Byte-Level SHA-256 Hashing:** Raw bytes are read exactly once from disk. SHA-256 is computed directly over this byte snapshot before any UTF-8 decoding, BOM removal, or newline normalization.
2. **Hash Semantics & Security Boundary:** A matching SHA-256 digest establishes raw byte integrity only. It does **not** prove authenticity, provenance veracity, truthful metadata, or diagnostic fitness.
3. **Strict Zero-Inference Policy:** No metadata (units, sampling rate, machine ID, provenance) is ever inferred from filenames or sample data. Missing or unrecognized metadata fields fail closed.
4. **Strict CSV Parsing & No Silent Repair:** Missing cells, empty rows, ragged columns, non-finite values (NaN/Infinity), extra channels, and unrecognized tokens immediately raise parse errors reporting filename, 1-based row, and 1-based column where available. No observation is ever dropped or imputed.
5. **Caller & Source Immutability:** Neither the caller's metadata dictionary nor the source file on disk is modified. No amplitude scaling, unit conversion, or resampling is performed.

---

## 2. Public API Specification

The adapter will be located in `adi_app.ingestion` and re-exported from `adi_app`:

```python
def load_vibration_csv(
    csv_path: str | Path,
    metadata: dict[str, Any],
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Load, verify, and validate a local vibration CSV file into a VibrationRecordingV1 payload.

    Args:
        csv_path: Path to the local CSV file on disk.
        metadata: Explicit metadata dictionary matching all required contract attributes.
        expected_sha256: Optional 64-character hexadecimal SHA-256 digest. If supplied,
            it is checked for valid syntax and matched against computed source bytes.

    Returns:
        dict[str, Any]: Canonical VibrationRecordingV1 dictionary.

    Raises:
        IngestionFileNotFoundError: If csv_path does not exist or is not a regular file.
        IngestionMetadataError: If metadata is incomplete, has extra fields, or violates rules.
        IngestionParseError: If CSV decoding, structure, or cell content violates parser rules.
        IngestionDigestMismatchError: If expected_sha256 syntax is invalid or mismatches computed digest.
        RecordingIngestionError: Base class for all ingestion exceptions.
    """
```

### Exception Hierarchy:
```
Exception
 └── RecordingIngestionError
      ├── IngestionFileNotFoundError (also inherits FileNotFoundError)
      ├── IngestionMetadataError (also inherits ValueError)
      ├── IngestionParseError (also inherits ValueError, records filename, row, column)
      └── IngestionDigestMismatchError (also inherits ValueError)
```

---

## 3. Metadata Format & Validation Invariants

The `metadata` dictionary must provide exactly the following 8 keys:

| Field | Type | Domain / Format Constraints |
|---|---|---|
| `schema_version` | `str` | Must be strictly `"1.0.0"`. |
| `recording_id` | `str` | Nonempty string identifying the recording. |
| `machine_id` | `str` | Nonempty string identifying the physical asset. |
| `session_id` | `str` | Nonempty string identifying the measurement session. |
| `source_id` | `str` | Nonempty string identifying the data capture source/device. |
| `provenance` | `str` | Must be strictly `"synthetic"` or `"recorded"`. |
| `sample_rate_hz` | `int` \| `float` | Strictly positive finite number ($> 0$), within IEEE 754 binary64 range. Booleans rejected. |
| `acceleration_unit` | `str` | Must be strictly `"g"` or `"m/s^2"`. |

### Boundary Rules:
- Missing any required field $\implies$ `IngestionMetadataError`.
- Any extra / undeclared field $\implies$ `IngestionMetadataError`.
- Non-dictionary input $\implies$ `IngestionMetadataError`.
- No inference from filename or data values $\implies$ caller must be explicit.

---

## 4. CSV Parser & Formatting Rules

### Encoding:
- Input bytes must be valid UTF-8.
- Optional UTF-8 Byte Order Mark (BOM: `\xef\xbb\xbf`) is recognized and stripped during decoding.
- Non-UTF-8 bytes (e.g. invalid UTF-8 sequences, UTF-16) raise `IngestionParseError`.
- The raw byte snapshot hashed for `source_sha256` includes the BOM bytes if present on disk.

### Delimiters & Structure:
- Standard comma-delimited format (`,`).
- Header row is required and must appear on row 1.
- Data rows must contain at least one observation row (row count $\ge 1$).
- Every data row must have exactly the same number of columns as the header. Ragged rows or rows with extra/fewer columns raise `IngestionParseError`.

### Channels (Header Row):
- Header tokens must be an ordered unique subset of `["x", "y", "z"]` (length 1, 2, or 3).
- Allowed permutations: `["x"]`, `["y"]`, `["z"]`, `["x", "y"]`, `["y", "x"]`, `["x", "z"]`, `["z", "x"]`, `["y", "z"]`, `["z", "y"]`, `["x", "y", "z"]`, `["x", "z", "y"]`, etc.
- Case-sensitive: uppercase `X`, `Y`, `Z` or other names (e.g. `time`, `accel`) are rejected.
- Duplicate channels (e.g. `x,x` or `x,y,x`) are rejected.
- Header tokens must not contain leading/trailing whitespace.

### Whitespace & Blank-Line Policy:
- **Blank lines:** Empty lines or lines containing only whitespace (whether leading, intermediate, or trailing) are strictly forbidden and raise `IngestionParseError`. No row is ever silently dropped.
- **Cell whitespace:** Leading and trailing ASCII whitespace (spaces, tabs) around numeric tokens in data cells are trimmed before numeric conversion (allowing standard formats like `1.0, 2.0`).
- **Empty / whitespace-only cells:** A cell with zero characters or only whitespace is strictly rejected as an empty cell. Missing values are never filled or imputed.
- **Internal whitespace:** Numbers containing internal spaces (e.g. `1 . 2` or `1 2`) are rejected as invalid numeric syntax.

### Numeric Cell Domain & Syntax:
- Cells must parse as finite decimal numbers representable in IEEE 754 binary64 (range $[-1.7976931348623157 \times 10^{308}, 1.7976931348623157 \times 10^{308}]$).
- Standard decimal and scientific notation are allowed (e.g. `0.012`, `-3.45`, `1.5e-3`, `+2.0E+02`).
- Non-finite tokens (`NaN`, `+NaN`, `-NaN`, `Inf`, `+Inf`, `-Inf`, `Infinity`) are strictly rejected.
- Non-numeric syntax (e.g. hexadecimal `0x10`, comma-grouped `1,000`, alphabetic `abc`, `null`, `None`, booleans) is strictly rejected.

---

## 5. Test Matrix & Coverage Plan

All tests will use temporary files (`tempfile.TemporaryDirectory`) with synthetic fixtures.

| Test Category | Target Scenarios | Verification |
|---|---|---|
| **Valid CSV Formats** | 1-channel (`x`), 2-channel (`x,y`), 3-channel (`x,y,z`) | Valid recording returned, samples match, schema matches. |
| **Channel Reordering** | Permutations `y,x` and `z,y,x` | Channels match header order, column values correctly preserved per channel. |
| **Scientific Notation** | Values like `1.23e-4`, `-4.56E+02`, `1e5` | Accurate floating-point parsing into sample matrix. |
| **Whitespace Policy** | Cell whitespace trimming (`1.0, 2.0`), blank line rejection, whitespace-only cell rejection | Proper trimming on valid cells; explicit `IngestionParseError` on blank lines / empty cells. |
| **BOM & CRLF Handling** | UTF-8 with BOM (`\xef\xbb\xbf`), CRLF (`\r\n`), LF (`\n`) | Decodes accurately; raw byte hash differs between BOM and non-BOM / CRLF and LF. |
| **Byte Integrity Hashing** | Exact SHA-256 vs independent `hashlib.sha256(raw_bytes)` | Byte-for-byte exact match. |
| **Equivalent Data / Distinct Hashes** | Same numbers formatted differently (`1.0` vs `1.00`, CRLF vs LF) | Provenance and source_sha256 differ while numerical data is identical. |
| **Expected Digest** | Matching expected hash vs mismatching hash vs malformed hash syntax | Match passes; mismatch raises `IngestionDigestMismatchError`; malformed syntax raises `IngestionDigestMismatchError`. |
| **Malformed CSVs** | Empty file, no header, header-only (no data rows), ragged row, extra columns, empty cells, duplicate channels, invalid channels, NaN/Inf, non-UTF8 bytes | Each category raises `IngestionParseError` with filename, row, column where applicable. |
| **Missing Files & Metadata Errors** | Missing file, directory path, missing metadata fields, unknown metadata fields, bad sample rate, bad unit | Raises `IngestionFileNotFoundError` or `IngestionMetadataError`. |
| **Immutability** | Caller metadata dict and source file on disk | Metadata dict unchanged; disk file content unchanged. |
| **Downstream Feature Pipeline** | Pass loaded recording into `extract_window_features` | Computes accurate mean, RMS, peak_abs matching analytical expectations. |

---

## 6. Installed Release Verification Extension

The relocated release verification script (`scripts/verify_portfolio_release.py`) will be extended to test installed package behavior after relocated source trees are removed:
1. Valid temporary CSV ingestion probe via installed `adi_app.load_vibration_csv` into `adi_runtime.extract_window_features`.
2. Rejection probe via installed `adi_app.load_vibration_csv` verifying that malformed CSV rows raise `RecordingIngestionError`.
