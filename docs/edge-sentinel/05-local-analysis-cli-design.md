# Stage 05 Design Specification: Local Analysis CLI & Pipeline Integration

**Stage ID:** `05-local-analysis-cli`  
**Target Package:** `adi-app` (CLI command & pipeline orchestration), integrating with `adi-runtime` (threshold detector) and `adi-common` (contracts)  
**Supervisor:** Codex  
**Prerequisite:** Accepted Stage 04 (`reviews/edge-sentinel/04-threshold-reference-repair-02.md`)  

---

## 1. Overview & Architectural Role

Stage 05 exposes the accepted local CSV ingestion (`adi-app.ingestion.load_vibration_csv`) and deterministic RMS threshold reference detector (`adi-runtime.threshold.evaluate_rms_threshold`) as an installed, runnable command:

```text
adi-portfolio analyze --csv PATH --metadata PATH --config PATH [--expected-sha256 HEX]
```

```
┌──────────────┐   ┌───────────────────┐   ┌───────────────────┐
│ CSV File     │   │ Metadata JSON     │   │ Configuration JSON│
│ (.csv path)  │   │ (.json path)      │   │ (.json path)      │
└──────┬───────┘   └─────────┬─────────┘   └─────────┬─────────┘
       │                     │                       │
       ▼                     ▼                       ▼
┌──────────────────────────────────────────────────────────────┐
│ Strict File & JSON Ingestion                                 │
│ - Validate file existence & regular file status              │
│ - Strict UTF-8 JSON: reject duplicate keys, NaN/Inf constants│
│ - Reject non-object top-level JSON structures                │
│ - Actionable path context on any read or parse failure       │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ Ingestion Adapter (adi_app.load_vibration_csv)               │
│ - Single-read SHA-256 calculation over raw bytes             │
│ - Optional expected_sha256 verification (exit 2 on mismatch) │
│ - Strict CSV parsing (delimiters, bare CR, finite numbers)   │
│ - Contract validation against VibrationRecordingV1           │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ Anomaly Reference Detector (adi_runtime.evaluate_rms_threshold)│
│ - Configuration validation against canonical schema          │
│ - Two-level abstention (recording envelope vs saturation)    │
│ - Max-RMS threshold scoring per complete window              │
│ - Contract validation against AnomalyDecisionV1              │
│ - Deterministic cryptographic decision IDs                   │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ Deterministic CLI Output Formatter                           │
│ - Combine detector envelope and reconciled decision counts   │
│ - Distinguish recording-level abstention from window counts  │
│ - allow_nan=False, sort_keys=True, indent=2, ensure_ascii=True│
│ - Zero timestamps or random UUIDs (identical input -> bytes) │
│ - Exit 0 on completed analysis (normal, anomaly, abstention) │
│ - Exit 2 on expected user/file/schema/data errors            │
└──────────────────────────────────────────────────────────────┘
```

### Invariant & Boundary Commitments:
1. **Uncalibrated Engineering Reference Scope:** The command provides uncalibrated baseline host reference processing. It does not perform machine learning, statistical threshold calibration, frequency-domain FFT, or whole-machine health diagnosis. It will never output an accuracy percentage or health verdict.
2. **Reuse Existing Engines:** Do not duplicate CSV parsing, metadata validation, or threshold evaluation logic. The CLI acts strictly as an adapter and orchestrator.
3. **Packaging & Dependencies:** Declare `adi-runtime>=0.1.0` in `adi-app/pyproject.toml`. No third-party dependencies beyond existing ones (`jsonschema`, `numpy`).
4. **Scaffold Preservation:** Preserve existing scaffold behaviors for `demo` (exit 0), `benchmark` (exit 1 JSON), and `evaluate` (exit 1 JSON).
5. **Read-Only Operation:** The command reads local files and writes only to stdout/stderr. It modifies no input files and writes no intermediate artifacts.

---

## 2. Command Line Interface Specification

### Syntax
```text
adi-portfolio analyze --csv <PATH> --metadata <PATH> --config <PATH> [--expected-sha256 <HEX>]
```

### Options
- `--csv PATH`: Required path to a local vibration CSV file.
- `--metadata PATH`: Required path to a local UTF-8 JSON file containing recording metadata adhering to `REQUIRED_METADATA_FIELDS`.
- `--config PATH`: Required path to a local UTF-8 JSON file containing detector configuration parameters.
- `--expected-sha256 HEX`: Optional 64-character hexadecimal SHA-256 hash expected to match the raw bytes of the CSV file.
- `-h`, `--help`: Standard help message.

---

## 3. Strict File Reading & JSON Parsing Rules

To satisfy supervisor requirements for strict JSON parsing and actionable path context:
1. **File Read Validation:**
   - Verify that paths exist and refer to regular files (`path.is_file()`).
   - If a file does not exist or cannot be read, report a concise error on stderr naming the exact path and exit with code `2`.
2. **Strict JSON Ingestion (`_load_strict_json_object(path)`):**
   - Read file bytes as UTF-8.
   - Use `json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constants)`.
   - **Reject duplicate keys:** If any object contains duplicate keys (e.g. `{"k": 1, "k": 2}`), reject with a clear error.
   - **Reject nonstandard constants:** Forbid `NaN`, `Infinity`, `-Infinity`. Python's default parser accepts these; our custom `parse_constant` callback raises `ValueError`.
   - **Reject non-object top-level:** If the parsed JSON is a list, number, string, boolean, or null, reject with an error requiring a top-level JSON object.
   - **Actionable Path Context:** Every error message includes the file path: `f"Error reading JSON from '{path}': {details}"`.

---

## 4. Exit Code Specification

| Condition | Exit Code | Stdout | Stderr | What It Signifies |
|---|:---:|---|---|---|
| **Analysis Successful** | `0` | Exactly one deterministic JSON document | Empty | Command completed processing (recording processed, features extracted, decisions evaluated). Applies to `NORMAL`, `ANOMALY`, and `INSUFFICIENT_EVIDENCE` (abstention). Denotes command success, not machinery health. |
| **Expected Error** | `2` | Empty (no success JSON) | Concise diagnostic message | Invalid arguments, missing files, unreadable files, malformed JSON, duplicate JSON keys, nonstandard JSON constants, non-object JSON, CSV parse errors, metadata validation failures, contract validation failures, configuration schema failures, or SHA-256 mismatch. |
| **Scaffold: demo** | `0` | Baseline scaffold text | Empty | Preserved Stage 00 scaffold behavior. |
| **Scaffold: benchmark** | `1` | Parseable JSON (`status: NOT_IMPLEMENTED`) | Empty | Preserved Stage 00 scaffold behavior. |
| **Scaffold: evaluate** | `1` | Parseable JSON (`status: NOT_IMPLEMENTED`) | Empty | Preserved Stage 00 scaffold behavior. |
| **Unexpected Internal Error** | `1` | Empty or stack trace | Exception diagnostic | Unhandled internal bug; never swallowed or relabeled as abstention. |

---

## 5. Output Schema & Summary Reconciliation

On exit 0, stdout contains a single JSON object formatted with `indent=2`, `sort_keys=True`, and `allow_nan=False`:

```json
{
  "command": "analyze",
  "configuration": {
    "config_id": "cfg_ref_001",
    "config_version": "1.0.0",
    "expected_acceleration_unit": "g",
    "expected_channels": ["x"],
    "expected_sample_rate_hz": 1000.0,
    "full_scale": 10.0,
    "hop_size": 10,
    "threshold": 2.0,
    "window_size": 10
  },
  "configuration_id": "cfg_ref_001",
  "decisions": [
    {
      "decision_id": "dec_v1_w0_0_10_...",
      "detector_id": "threshold_reference",
      "detector_version": "1.0.0",
      "execution_environment": "HOST_CPU",
      "outcome": "NORMAL",
      "quality_status": "PASS",
      "reason_codes": [],
      "recording_ref": "rec_001",
      "schema_version": "1.0.0",
      "score": 1.0,
      "threshold": 2.0
    }
  ],
  "detector_id": "threshold_reference",
  "detector_version": "1.0.0",
  "evaluation_scope": "uncalibrated_engineering_reference",
  "execution_environment": "HOST_CPU",
  "provenance": "recorded",
  "recording_id": "rec_001",
  "schema_version": "1.0.0",
  "source_sha256": "...",
  "status": "COMPLETED",
  "summary": {
    "anomaly_count": 0,
    "insufficient_evidence_count": 0,
    "normal_count": 1,
    "recording_level_abstention": false,
    "total_decisions": 1,
    "window_level_counts": {
      "anomaly": 0,
      "insufficient_evidence": 0,
      "normal": 1
    }
  },
  "trailing_samples": 0,
  "window_boundaries": [
    {
      "end_sample": 10,
      "start_sample": 0,
      "window_index": 0
    }
  ]
}
```

### Reconciliation Rules:
1. `summary.total_decisions == len(decisions)`
2. `summary.total_decisions == summary.normal_count + summary.anomaly_count + summary.insufficient_evidence_count`
3. If `status == "INSUFFICIENT_EVIDENCE"` and `window_boundaries == []`:
   - `summary.recording_level_abstention = true`
   - `summary.insufficient_evidence_count = 1`
   - `summary.window_level_counts.insufficient_evidence = 0`
4. If `status == "COMPLETED"`:
   - `summary.recording_level_abstention = false`
   - `summary.window_level_counts.normal == summary.normal_count`
   - `summary.window_level_counts.anomaly == summary.anomaly_count`
   - `summary.window_level_counts.insufficient_evidence == summary.insufficient_evidence_count`

---

## 6. Collision Resistance Documentation Clarification

Per supervisor review direction in `reviews/edge-sentinel/04-threshold-reference-repair-02.md`:
- Truncating SHA-256 to 32 hexadecimal characters provides a 128-bit identifier.
- Generic collision resistance against the birthday paradox provides approximately $2^{64}$ operations (64 bits of generic collision bound), while preimage resistance remains 128 bits.
- Documentation in `adi-runtime/README.md` and `README.md` will explicitly clarify:
  *"Decision IDs incorporate a 32-hex character SHA-256 digest truncation (128-bit identifier) yielding approximately 64-bit generic birthday collision resistance, providing robust cryptographic resistance against accidental collisions across telemetry streams."*

---

## 7. Verification & Acceptance Plan

1. **Unit & Subprocess Test Suite:**
   - Create `adi-app/tests/test_cli_analyze.py`.
   - Run tests executing `adi-portfolio` via subprocess and testing `main()` in-process.
   - Comprehensive test coverage for all success, abstention, and exit 2 error cases.
2. **Packaging & Dependency Checks:**
   - Update `adi-app/pyproject.toml` to declare `adi-runtime>=0.1.0`.
   - Rebuild wheels via `make bootstrap-portfolio`.
   - Run `uv pip check --python .release/venv/bin/python`.
3. **Offline Relocated Release Verification:**
   - Update `scripts/verify_portfolio_release.py` to invoke `adi-portfolio analyze` in the clean relocated virtual environment after source trees are deleted.
   - Test normal, anomaly, saturation, recording-level mismatch, and error exit 2.
4. **Handoff Artifacts:**
   - `handoff/edge-sentinel/05-local-analysis-cli.md`
   - `handoff/edge-sentinel/05-local-analysis-cli/COMMANDS.md`
   - `handoff/edge-sentinel/05-local-analysis-cli/logs/*`
   - `handoff/edge-sentinel/05-local-analysis-cli/manifest.json`
