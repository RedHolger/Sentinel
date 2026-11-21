# Stage 04 Design Specification: RMS Threshold Reference with Abstention

**Stage ID:** `04-threshold-reference`  
**Target Package:** `adi-runtime` (reference threshold detector), integrating with `adi-common` (contracts & validators)  
**Supervisor:** Codex  
**Prerequisite:** Accepted Stage 03 (`reviews/edge-sentinel/03-recording-ingestion-repair-01.md`)  

---

## 1. Overview & Architectural Role

The RMS threshold reference provides an uncalibrated, deterministic host-side baseline anomaly detector that bridges validated vibration recordings (`VibrationRecordingV1`) and window features to standard anomaly decisions (`AnomalyDecisionV1`).

```
VibrationRecordingV1 + Detector Configuration
                     │
                     ▼
  [ Validate Recording & Configuration Invariants ]
                     │
     ┌───────────────┴────────────────┐
     │ Incompatible Operating Env     │ Fully Compatible Envelope
     ▼                                ▼
[ Recording-Level Abstention ]   [ Complete Window Extraction ]
- Sample rate mismatch           - Window size & hop stride
- Unit mismatch                  - Trailing sample accounting
- Channel mismatch/reorder                   │
- Insufficient sample length                 ▼
  (window_boundaries = [])       [ Inspect Each Window for Saturation ]
                                 - abs(sample) >= full_scale?
                                      ├── YES ──> [ Window Abstention ]
                                      │           - quality_status: INDETERMINATE
                                      │           - reason: SENSOR_SATURATION_POSSIBLE
                                      │           - score/threshold: null
                                      │
                                      └── NO ───> [ Max-RMS Threshold Scoring ]
                                                  - score = max(channel_rms)
                                                  - score >= threshold: ANOMALY
                                                  - score < threshold: NORMAL
                                                  - quality_status: PASS
                                                  │
                                                  ▼
                                      [ Traceable Result Envelope ]
                                      - Recording identity & source SHA-256
                                      - Configuration copy & detector version
                                      - Deterministic window decision IDs
                                      - Explicit uncalibrated reference scope
```

### Key Invariant Guarantees:
1. **Uncalibrated Engineering Reference:** This module provides baseline reference behavior only. It does **not** perform machine learning, statistical calibration, threshold fitting, frequency-domain analysis, or machine health diagnosis.
2. **Explicit Two-Level Abstention Architecture:**
   - **Recording-Level Envelope Mismatch:** When a recording violates the detector's configured operating envelope (sample rate, unit, channel order/presence, or length), it produces a recording-level `INSUFFICIENT_EVIDENCE` decision with explicit reason codes without inventing false window boundaries.
   - **Window-Level Saturation Flagging:** When any sample in an individual window satisfies $|x| \ge \text{full\_scale}$, that window produces `INSUFFICIENT_EVIDENCE` (`quality_status: INDETERMINATE`, reason: `SENSOR_SATURATION_POSSIBLE`). This flags potential sensor saturation without claiming proven hardware clipping.
3. **Max-RMS Scoring Rule:** For non-saturated windows, the anomaly score is strictly the maximum RMS value computed across all configured channels in that window. `score >= threshold` produces `ANOMALY`; `score < threshold` produces `NORMAL`.
4. **Deterministic Decision Identity:** Decision IDs are derived deterministically from the recording ID, configuration ID, window index, and sample offsets—never wall-clock timestamps or random UUIDs.
5. **No Whole-Machine Aggregation:** The envelope reports individual window boundaries and decisions. It never aggregates windows into a whole-machine health diagnosis.
6. **Caller Input Immutability:** Neither the recording dictionary nor the configuration dictionary is mutated in any way.

---

## 2. Public API Specification

The detector reference will be implemented in `adi_runtime.threshold` and exported from `adi_runtime`:

```python
def validate_threshold_configuration(config: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a detector configuration dictionary against contract rules."""

def evaluate_rms_threshold(
    recording: dict[str, Any],
    configuration: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate a vibration recording against an RMS threshold configuration."""
```

### Exception Class Hierarchy:
```
ThresholdReferenceError (Base exception, inherits Exception)
 ├── ConfigurationError (Invalid schema, unknown keys, invalid domains, non-finite values; inherits ValueError)
 └── EvaluationError (Runtime evaluation failure; inherits RuntimeError)
```

Contract violations on `recording` continue to raise `ContractValidationError` from `adi-common`.

---

## 3. Configuration Schema & Validation Rules

A valid configuration dictionary must provide exactly the following parameters (aliases are normalized):

| Canonical Field | Type | Domain / Constraints | Description |
|---|---|---|---|
| `config_id` | `str` | Non-empty, non-whitespace string | Unique identifier for this configuration |
| `config_version` | `str` | Non-empty, non-whitespace string | Version string of the configuration |
| `window_size` | `int` | Strictly positive integer ($> 0$), non-boolean | Length of each complete window in samples |
| `hop_size` | `int` | Strictly positive integer ($> 0$), $\le \text{window\_size}$, non-boolean | Step size between window start indices |
| `expected_sample_rate_hz` | `float` / `int` | Finite binary64 number ($> 0$), non-boolean | Expected sensor sampling rate in Hz |
| `expected_acceleration_unit` | `str` | Exactly `'g'` or `'m/s^2'` | Expected physical acceleration unit |
| `expected_channels` | `list[str]` | Non-empty unique subset of `['x', 'y', 'z']` (length 1–3) | Expected channel names in required order |
| `full_scale` | `float` / `int` | Finite binary64 number ($> 0$), non-boolean | Sensor full-scale magnitude (in configured unit) |
| `threshold` | `float` / `int` | Finite binary64 number ($\ge 0$), non-boolean | Decision threshold (in configured unit) |

### Rejection Policies:
- Missing required fields: raises `ConfigurationError`.
- Unknown or extra fields: raises `ConfigurationError`.
- Booleans passed for numeric parameters: raises `ConfigurationError`.
- Non-finite numbers (`NaN`, `Infinity`): raises `ConfigurationError`.
- Oversized integers exceeding finite IEEE 754 binary64 range ($\pm 10^{400}$): raises `ConfigurationError` without leaking `OverflowError`.
- Invalid window parameters (`window_size <= 0`, `hop_size <= 0`, `hop_size > window_size`): raises `ConfigurationError`.
- Duplicate or unknown channel names: raises `ConfigurationError`.
- Non-positive full-scale ($\le 0$) or negative threshold ($< 0$): raises `ConfigurationError`.

---

## 4. Operational Invariant & Equality Semantics

### Sample Rate Equality Semantics:
- Evaluated via exact IEEE 754 binary64 floating-point equality: `recording["sample_rate_hz"] == expected_sample_rate_hz`.
- No rate tolerance or approximation is assumed. If rates mismatch, the detector abstains with reason code `SAMPLE_RATE_MISMATCH`.

### Unit Equality Semantics:
- Evaluated via string equality: `recording["acceleration_unit"] == expected_acceleration_unit`.
- No automatic gravity scaling ($9.80665\,\text{m/s}^2$) or conversion is performed. If units mismatch, the detector abstains with reason code `UNIT_MISMATCH`.

### Channel Order Semantics:
- Evaluated via ordered list comparison: `recording["channels"] == expected_channels`.
- The detector never reorders, drops, or permutes channels. If channel sets or ordering differ, the detector abstains with reason code `CHANNELS_MISMATCH`.

### Minimum Length Semantics:
- Evaluated via `len(recording["samples"]) >= window_size`.
- If fewer samples than `window_size` exist, the detector abstains with reason code `INSUFFICIENT_RECORDING_LENGTH`. No windows are invented.

### Quality Status Meanings:
- `PASS`: Applied to `NORMAL` and `ANOMALY` decisions when input data is within full-scale limits and matches the detector's operational envelope.
- `FAIL`: Applied to recording-level `INSUFFICIENT_EVIDENCE` decisions when the recording fails to satisfy the detector's operational envelope prerequisites.
- `INDETERMINATE`: Applied to window-level `INSUFFICIENT_EVIDENCE` decisions when $|x| \ge \text{full\_scale}$ indicates possible sensor saturation, but physical clipping cannot be conclusively proven without analog telemetry.

---

## 5. Result Envelope Format

```python
{
    "schema_version": "1.0.0",
    "status": "COMPLETED" | "INSUFFICIENT_EVIDENCE",
    "recording_id": str,
    "source_sha256": str,
    "provenance": str,
    "configuration_id": str,
    "configuration": dict[str, Any],
    "detector_id": "threshold_reference",
    "detector_version": "1.0.0",
    "evaluation_scope": "uncalibrated_engineering_reference",
    "window_boundaries": [
        {"window_index": int, "start_sample": int, "end_sample": int},
        ...
    ],
    "decisions": [
        # List of AnomalyDecisionV1 payloads validated by validate_anomaly_decision
    ],
    "trailing_samples": int,
    "execution_environment": "HOST_CPU",
}
```

---

## 6. Test Suite Strategy

Unit tests in `adi-runtime/tests/test_threshold.py` will cover:
1. Below threshold $\to$ `NORMAL` with `quality_status: PASS`.
2. Above threshold $\to$ `ANOMALY` with `quality_status: PASS`.
3. Equal to threshold $\to$ `ANOMALY` with `quality_status: PASS`.
4. Max-RMS multi-channel scoring: channel ordering permutations proving max-RMS dominates.
5. Positive full-scale equality and exceedance $\to$ `INSUFFICIENT_EVIDENCE`.
6. Negative full-scale equality and exceedance $\to$ `INSUFFICIENT_EVIDENCE`.
7. Saturated and valid windows occurring in the same recording.
8. Every operating-envelope mismatch (sample rate, unit, channels, channel order, insufficient length, multiple simultaneous mismatches).
9. Zero-signal recording (valid, not automatically rejected).
10. Trailing samples accounting across varying hops and window sizes.
11. Repeatability (deterministic decision IDs and identical decision payloads).
12. Input immutability (recording and configuration untouched).
13. Invalid configuration rejections (booleans, non-finite values, oversized integers, unknown keys, invalid window constraints).
14. End-to-end integration: CSV file on disk $\to$ `load_vibration_csv` $\to$ `extract_window_features` $\to$ `evaluate_rms_threshold`.
15. Validation of every returned decision using `validate_anomaly_decision`.
