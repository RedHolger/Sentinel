# ADI App

Application layer, recording ingestion adapter, and offline CLI assistant for ADI edge deployments.

---

## 1. Recording Ingestion Adapters (`load_vibration_csv` & `parse_vibration_csv_bytes`)

Stage 03 introduces a strict CSV-plus-metadata ingestion adapter that loads local comma-delimited vibration files and explicit metadata dictionaries into the canonical `VibrationRecordingV1` contract (`adi-common`), which subsequently feeds the `adi-runtime` feature extraction pipeline.

Stage 06 extracts `parse_vibration_csv_bytes` as the shared single-read byte snapshotting core, allowing scenario evaluation to hash and parse the identical memory byte snapshot without reopening files.

### API Usage

```python
from pathlib import Path
from adi_app import load_vibration_csv, parse_vibration_csv_bytes

metadata = {
    "schema_version": "1.0.0",
    "recording_id": "rec-2026-001",
    "machine_id": "pump-west-04",
    "session_id": "sess-shift1-002",
    "source_id": "raw-sensor-adxl355-capture-001",
    "provenance": "recorded",          # "synthetic" or "recorded"
    "sample_rate_hz": 4000.0,           # positive finite float/int
    "acceleration_unit": "g",           # "g" or "m/s^2"
}

# 1. Load and validate recording from filesystem path
recording = load_vibration_csv(
    csv_path="data/sensor_capture.csv",
    metadata=metadata,
    expected_sha256="4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a"  # optional
)

# 2. Alternatively, parse from an in-memory byte snapshot
raw_bytes = Path("data/sensor_capture.csv").read_bytes()
recording = parse_vibration_csv_bytes(
    raw_bytes=raw_bytes,
    metadata=metadata,
    source_path_context="data/sensor_capture.csv",
    expected_sha256="4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a"
)

print(f"Loaded {len(recording['samples'])} samples across channels {recording['channels']}")
print(f"Computed source SHA-256: {recording['source_sha256']}")
```

---

## 2. CSV Formatting & Parser Rules

The CSV parser enforces deterministic, zero-inference parsing via `csv.reader(..., strict=True)`:

1. **Header Row:**
   - Must appear on row 1.
   - Header tokens must be an ordered unique subset of `x,y,z` (1 to 3 channels).
   - Examples: `x`, `x,y`, `x,y,z`, `y,x`, `z,y,x`.
   - Disallowed: Duplicate channels (e.g. `x,x`), unknown channels (e.g. `time`, `accel`), uppercase letters (`X`), embedded newlines, and leading/trailing whitespace around column names.
2. **Data Rows & Quoting:**
   - At least one data row is required.
   - Every data row must have exactly the same number of columns as the header. Ragged rows and extra/missing columns are rejected.
   - Quoting is supported for cells (e.g. `"1.23"`). Unterminated quotes (`"1`), malformed closing quotes (`"1"2`), and embedded physical newlines inside quoted numeric cells (`"1\n2"`) are strictly rejected.
3. **Explicit Numeric Grammar:**
   - After trimming only ASCII spaces (`' '`) and tabs (`'\t'`), each numeric cell must strictly conform to:
     - Optional `+` or `-` sign.
     - Decimal mantissa: `12`, `12.`, `12.34`, or `.34`.
     - Optional exponent: `e` or `E` with optional sign and at least one ASCII digit (e.g. `1.23e-4`, `-4.5E+02`).
   - Matched against ASCII `[0-9]` digits only.
   - Disallowed: Python-specific numeric syntax like underscores (`1_000`), non-ASCII Unicode digits (e.g. Arabic-Indic `١٢٣`), embedded spaces (`1 2`), hex (`0x10`), `NaN`, `Infinity`, `null`, `None`, and booleans.
   - **Binary64 Rounding & Underflow:** Values are converted using standard IEEE 754 binary64 float semantics. Ordinary binary64 rounding applies. Numbers with extreme negative exponents undergo gradual underflow or flush-to-zero per IEEE 754. Out-of-domain magnitudes exceeding `sys.float_info.max` ($\approx 1.7976931348623157 \times 10^{308}$) are rejected.
4. **Whitespace & Line Ending Policies:**
   - **Line Endings:** Standard POSIX LF (`\n`) and Windows CRLF (`\r\n`) line endings are supported. Bare CR (`\r` without `\n`) line endings are strictly rejected with `IngestionParseError`.
   - **Blank Lines:** Blank or whitespace-only lines (leading, intermediate, or trailing) are strictly rejected. No row is ever silently skipped.
   - **Cell Whitespace:** Only ASCII horizontal whitespace (spaces and tabs) around numeric values in cells is trimmed before numeric conversion. Non-ASCII whitespace (such as `\u00a0`) is not stripped and is rejected by the numeric grammar.
   - **Empty / Whitespace-Only Cells:** A cell with zero characters or only whitespace is strictly rejected as an empty cell. Missing values are never filled or imputed.
5. **Encoding & BOM Handling:**
   - Files must be UTF-8 encoded.
   - Optional UTF-8 Byte Order Mark (BOM: `\xef\xbb\xbf`) is detected and stripped from text during decoding.
   - Non-UTF-8 bytes immediately raise an `IngestionParseError`.

---

## 3. Byte Hashing & Integrity Semantics

- **Single Read Snapshot:** The source file is read into memory exactly once as raw bytes.
- **SHA-256 Computation:** The SHA-256 digest (`source_sha256`) is computed directly over the raw disk byte snapshot before any UTF-8 decoding, BOM removal, or newline normalization.
- **Different Bytes $\implies$ Different Digests:** Files with identical numerical data formatted differently (e.g. LF vs CRLF, UTF-8 BOM vs standard UTF-8, `1.0` vs `1.00`) produce distinct `source_sha256` digests, ensuring complete byte-level auditability.
- **Optional Expected Digest:** Callers may supply `expected_sha256`. The adapter validates its 64-hex syntax and asserts it matches the computed digest before producing a recording.

> [!IMPORTANT]
> A matching `source_sha256` proves only that the raw bytes read from disk match the digest. It does **not** prove data authenticity, sensor physical calibration, metadata truthfulness, or diagnostic suitability.

---

## 4. Error Handling Hierarchy

All ingestion errors inherit from `RecordingIngestionError`:

```
RecordingIngestionError
 ├── IngestionFileNotFoundError (raised when CSV path does not exist or is not a file)
 ├── IngestionMetadataError (raised for missing/unknown metadata keys or domain violations)
 ├── IngestionParseError (raised for CSV syntax, ragged rows, empty cells, non-finite values)
 └── IngestionDigestMismatchError (raised for expected_sha256 mismatch or invalid hash syntax)
```

`IngestionParseError` reports `filename`, `row` (1-based physical line when available from parser), and `column` (1-based when known), preserving underlying parser exceptions (`csv.Error`) via Python exception chaining (`__cause__`).

Metadata integer inputs (e.g. `sample_rate_hz = 10**400`) are magnitude-checked before conversion, raising `IngestionMetadataError` instead of leaking `OverflowError`. Parser failures (e.g. fields exceeding `csv.field_size_limit()`) raise `IngestionParseError` rather than bare `csv.Error`.

---

## 5. Scope Boundaries & Limitations

- **Local Files Only:** The adapter accepts local filesystem paths only. URLs, streaming sockets, and remote storage downloads are not supported.
- **Strict Zero-Inference:** Units, sample rate, machine identity, and provenance are never guessed or inferred from filenames or column names.
- **No Signal Processing or Quality Classification:** This module only ingests and validates CSV recordings into contract structures; anomaly detection, FFT spectral analysis, filter pipelines, and hardware execution are not included in this stage.

---

## 6. Installed CLI Commands

```sh
adi-portfolio demo        # Run baseline CLI scaffold (exits 0)
adi-portfolio benchmark   # Returns NOT_IMPLEMENTED (exits 1)
adi-portfolio evaluate    # Returns NOT_IMPLEMENTED without --suite (exits 1)
adi-portfolio evaluate --suite PATH # Stage 06 scenario evaluation (exits 0, 1, or 2)
adi-portfolio analyze --csv PATH --metadata PATH --config PATH [--expected-sha256 HEX] # Stage 05 (exits 0 or 2)
```

### Stage 05 Analysis CLI Workflow

The `analyze` command executes the full local pipeline: CSV ingestion (`load_vibration_csv`) through RMS threshold anomaly evaluation (`evaluate_rms_threshold`), outputting a single versioned deterministic JSON document.

#### Example Invocation:

```sh
.release/venv/bin/adi-portfolio analyze \
  --csv data/vibration.csv \
  --metadata data/metadata.json \
  --config data/config.json \
  --expected-sha256 3e72ef8fd0f9d6310e86c26f1d5885d5a576901a1ce5272519406af4f0e8b0d1
```

#### Example Files:

**`data/vibration.csv`:**
```csv
x
0.8
1.1
0.9
1.0
1.2
```

**`data/metadata.json`:**
```json
{
  "schema_version": "1.0.0",
  "recording_id": "rec_demo_01",
  "machine_id": "pump_west_01",
  "session_id": "sess_shift_01",
  "source_id": "sensor_front",
  "provenance": "recorded",
  "sample_rate_hz": 1000.0,
  "acceleration_unit": "g"
}
```

**`data/config.json`:**
```json
{
  "config_id": "cfg_demo_01",
  "config_version": "1.0.0",
  "window_size": 5,
  "hop_size": 5,
  "expected_sample_rate_hz": 1000.0,
  "expected_acceleration_unit": "g",
  "expected_channels": ["x"],
  "full_scale": 16.0,
  "threshold": 1.5
}
```

#### Output Structure & Exit Codes:
- **Exit Code 0:** Successful analysis execution (command completed). Applies to `NORMAL`, `ANOMALY`, and `INSUFFICIENT_EVIDENCE` (abstention) outcomes. Emits deterministic JSON to `stdout` with `allow_nan=False`, sorted keys, and 2-space indentation.
- **Exit Code 2:** Expected user, argument, file, syntax, schema, or digest errors (e.g. missing files, malformed JSON, duplicate keys, nonstandard constants like `NaN`/`Infinity`, CSV parse errors, envelope mismatch before execution, or `--expected-sha256` mismatch). Emits concise diagnostic to `stderr` and zero bytes to `stdout`.
- **Exit Code 1:** Unhandled internal exceptions.

> [!NOTE]
> Successful execution with exit code 0 denotes successful command processing, not a healthy machine. The pipeline provides uncalibrated baseline reference behavior only; it does not fit thresholds, verify machine health, or execute on physical silicon.

---

## 7. Synthetic Scenario Evaluation CLI (`adi-portfolio evaluate --suite PATH`)

Stage 06 implements deterministic behavioral regression evaluation for explicitly synthetic scenarios via `adi-portfolio evaluate --suite PATH`.

### Workflow & Invariants

1. **Explicit Synthetic Expectations:**
   - Compares end-to-end pipeline results against independently authored synthetic expectations in a suite manifest.
   - Expectations are never calculated from the detector under test.
   - Enforces loaded recording provenance is strictly `"synthetic"` (recordings with `"recorded"` provenance are rejected as `ERROR`).
2. **Strict Manifest Validation Before Execution:**
   - Requires nonempty string paths for `csv_path`, `metadata_path`, and `config_path`. Null values or non-string types raise `ScenarioSuiteError` (exit 2).
   - Rejects boolean values in integer indices and boundaries (`window_index`, `start_sample`, `end_sample`).
   - Validates ordered, unique window indices (`0, 1, 2, ...`) and progressing boundaries (`end_sample > start_sample`).
   - Enforces scope-exclusive structures:
     - `scope: "recording_envelope"` requires an abstention outcome (`INSUFFICIENT_EVIDENCE`), quality status (`FAIL` or `INDETERMINATE`), and a nonempty list of reason codes; strictly forbids `windows`.
     - `scope: "window"` requires a nonempty `windows` list; strictly forbids envelope-level fields (`outcome`, `quality_status`, `reason_codes`).
     - Rejects empty or non-string reason codes and contradictory outcome/quality combinations (e.g. `NORMAL` with `FAIL` or `INSUFFICIENT_EVIDENCE` with `PASS`).
3. **Relative Path Resolution:**
   - All relative file paths (`csv_path`, `metadata_path`, `config_path`) in the suite manifest are resolved relative to the suite manifest file's directory, ensuring deterministic results regardless of the process working directory (`cwd`).
4. **Single-Read Snapshot & Source Hashing:**
   - Reads each input file (suite manifest, CSV, metadata, config) into memory exactly once as raw bytes.
   - The identical byte snapshot is hashed and parsed using `parse_vibration_csv_bytes`, guaranteeing that computed digests and analyzed samples correspond to the exact same snapshot.
5. **Honest Accounting & Auditability:**
   - Case outcomes are categorized as `MATCH`, `MISMATCH`, or `ERROR`.
   - File read, JSON syntax, digest mismatch, metadata, or configuration failures are recorded as `ERROR` and are never dropped from denominators.
   - Independent cases continue evaluating after an error in a preceding case.
   - Report includes both `expected_decisions_summary` (including expectations of ERROR cases) and `actual_decisions_summary` (where ERROR cases invent no decisions).
   - Reconciles totals and breakdowns by scope (`recording_envelope` vs `window`) and outcome (`NORMAL`, `ANOMALY`, `INSUFFICIENT_EVIDENCE`).
   - Case reports include `expected_result` and `actual_window_boundaries` for full auditability.
6. **Deterministic JSON Output:**
   - Emits a single versioned JSON report to `stdout` (`allow_nan=False`, sorted keys, 2-space indentation). No timestamps or non-deterministic identifiers are emitted.

### Suite Manifest Schema

```json
{
  "schema_version": "1.0.0",
  "suite_id": "suite_synthetic_regression_01",
  "evaluation_scope": "synthetic_behavioral_regression",
  "description": "Comprehensive synthetic behavioral regression suite",
  "cases": [
    {
      "case_id": "case_01_normal_window",
      "description": "Single-channel normal window (RMS=1.0 < 2.0)",
      "csv_path": "normal_5samples.csv",
      "metadata_path": "metadata_1000hz.json",
      "config_path": "config_1000hz_fs10_th2.json",
      "expected_csv_sha256": "f136415649a2d511037c028ca5eb57bde4cc9257c94e2c87247dcab92b140e6c",
      "expected_result": {
        "scope": "window",
        "windows": [
          {
            "window_index": 0,
            "start_sample": 0,
            "end_sample": 5,
            "outcome": "NORMAL",
            "quality_status": "PASS",
            "reason_codes": []
          }
        ]
      }
    }
  ]
}
```

### Exit Codes:
- **Exit Code 0:** All test cases in the suite `MATCH` (`status: "PASS"`).
- **Exit Code 1:** One or more cases `MISMATCH` or `ERROR` (`status: "FAIL"`).
- **Exit Code 2:** Malformed suite manifest, missing file, syntax error, duplicate keys, nonstandard JSON constants (`NaN`/`Infinity`), empty suite, or duplicate case IDs (concise stderr diagnostic, empty stdout).

> [!IMPORTANT]
> The scenario evaluator establishes behavioral agreement with declared synthetic expectations only. It does **not** evaluate diagnostic accuracy, precision, recall, statistical confidence intervals, or general machine health. An all-abstention suite produces `MATCH` when abstention is expected, not diagnostic perfection.
