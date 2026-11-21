# ADI Runtime

Execution runtime, signal-processing reference pipelines, quantization harnesses, and hardware-target staging for the ADI Edge Portfolio.

## Current State

Stage 04 implementation (`v0.1.0`). Provides:
1. Deterministic host reference for vibration recording window extraction and per-channel time-domain feature computation in `adi_runtime.features`.
2. Deterministic host reference for RMS threshold anomaly detection with two-level abstention in `adi_runtime.threshold`.

> [!NOTE]
> Pinned neural reference models, memory residency derivations, quantization harnesses, and physical hardware deployment bridges (MAX78000 / ADSP-SC589) are planned for later stages and are currently unverified scaffolds. Successful feature or threshold evaluation provides uncalibrated engineering reference behavior only; it does not verify sensor calibration, learned anomaly detection accuracy, machine health, or embedded silicon execution.

---

## Public API & Validation Policy

The runtime exports the following functions, classes, and constants from `adi_runtime`:

```python
from adi_runtime import (
    # Feature extraction (Stage 02)
    extract_window_features,
    compute_channel_metrics,
    stable_mean,
    stable_rms,
    stable_peak_abs,
    WindowFeatureError,
    # Threshold reference detector (Stage 04)
    evaluate_rms_threshold,
    validate_threshold_configuration,
    ConfigurationError,
    ThresholdReferenceError,
    DETECTOR_ID,
    DETECTOR_VERSION,
    EVALUATION_SCOPE,
    REASON_SAMPLE_RATE_MISMATCH,
    REASON_UNIT_MISMATCH,
    REASON_CHANNELS_MISMATCH,
    REASON_INSUFFICIENT_LENGTH,
    REASON_SENSOR_SATURATION_POSSIBLE,
)
```

### `evaluate_rms_threshold(recording: dict, configuration: dict) -> dict`

Evaluates a validated vibration recording against an explicitly configured RMS threshold reference with two-level abstention.

- **Input Validation:**
  - Validates `recording` against `adi_common.validate_vibration_recording`. Fails closed with `ContractValidationError` if invalid.
  - Validates `configuration` against `validate_threshold_configuration`. Fails closed with `ConfigurationError` if invalid.
- **Two-Level Abstention Architecture:**
  1. **Recording-Level Operational Envelope Mismatch:** If the recording's `sample_rate_hz`, `acceleration_unit`, ordered `channels`, or minimum length fails to match configured parameters, evaluation produces a recording-level `INSUFFICIENT_EVIDENCE` decision (`quality_status: FAIL`, reason codes: `SAMPLE_RATE_MISMATCH`, `UNIT_MISMATCH`, `CHANNELS_MISMATCH`, `INSUFFICIENT_RECORDING_LENGTH`). Window boundaries are set to `[]` (no false window boundaries are invented).
  2. **Window-Level Possible Saturation:** For each extracted complete window, every channel sample is inspected. If any $|x| \ge \text{full\_scale}$, that window produces `INSUFFICIENT_EVIDENCE` (`quality_status: INDETERMINATE`, reason code: `SENSOR_SATURATION_POSSIBLE`, score/threshold: null). This flags potential sensor saturation without claiming proven hardware clipping.
  3. **Max-RMS Threshold Scoring:** For non-saturated windows, the anomaly score is the maximum per-channel RMS in that window. If `score >= threshold`, outcome is `ANOMALY`; otherwise `NORMAL` (`quality_status: PASS`). Zero signal is not automatically invalid.
- **Traceable Result Envelope & Deterministic Decision Identity:**
  - Returns `recording_id`, `source_sha256`, `provenance`, `configuration_id`, deep copy of `configuration`, `detector_id` (`"threshold_reference"`), `detector_version` (`"1.0.0"`), `evaluation_scope` (`"uncalibrated_engineering_reference"`), `window_boundaries`, `decisions`, and `trailing_samples`.
  - Every decision is validated against `adi_common.validate_anomaly_decision`.
  - **Cryptographic Decision Identity Binding:** Decision IDs are derived deterministically by computing SHA-256 digests over canonical JSON representations of the evaluated recording content/metadata (including `source_sha256`), configuration parameters (including `threshold`, `config_version`), detector identity, decision scope, and window boundaries (`dec_v1_w{idx}_{start}_{end}_{hash}` and `dec_v1_envelope_{hash}`). Changing the threshold, configuration version, sample content, or source digest strictly produces distinct decision IDs.
  - **Lossless Numeric Identity Policy & Collision Resistance:**
    - Canonical serialization losslessly preserves validated numeric types: Python `int` serializes as a standard JSON integer (`1`) and Python `float` serializes as a standard JSON float (`1.0`), preserving distinguishable representation types and arbitrary-precision integer representations without loss.
    - Eliminates information collapse for sample integers exceeding $2^{53}$: distinct integers (e.g. $2^{53}+3$ producing `NORMAL` and $2^{53}+4$ producing `INSUFFICIENT_EVIDENCE`) retain their exact integer values in canonical JSON instead of rounding to identical IEEE 754 floats (`9007199254740996.0`), strictly producing distinct decision IDs.
    - All canonical JSON serialization enforces `allow_nan=False`, strictly forbidding `NaN` or `Infinity` tokens.
    - Decision IDs incorporate a 32-hex character SHA-256 digest truncation (a 128-bit identifier). In accordance with standard cryptographic principles, a 128-bit identifier yields roughly a 64-bit generic birthday collision bound (~$2^{64}$ operations), providing robust cryptographic collision resistance against accidental collisions across telemetry streams while maintaining concise identifier length. Terminology: identifiers are cryptographically *collision-resistant*, not "collision-free".
  - Never aggregates windows into a whole-machine health verdict.
  - Preserves input immutability: neither recording nor configuration is mutated.

### `validate_threshold_configuration(config: dict) -> dict`

Validates detector configuration parameters against strictly canonical keys:
- `config_id` (`str`): Non-empty string.
- `config_version` (`str`): Non-empty string.
- `window_size` (`int`): Positive integer (`> 0`, non-boolean).
- `hop_size` (`int`): Positive integer (`> 0`, `hop_size <= window_size`, non-boolean).
- `expected_sample_rate_hz` (`float` / `int`): Finite positive number within binary64 range.
- `expected_acceleration_unit` (`str`): Strictly `'g'` or `'m/s^2'`.
- `expected_channels` (`list[str]`): Non-empty ordered subset of `['x', 'y', 'z']` (length 1–3, unique).
- `full_scale` (`float` / `int`): Finite positive magnitude ($> 0$).
- `threshold` (`float` / `int`): Finite non-negative threshold ($\ge 0$).
- **Strict Canonical Key Policy:** Only documented canonical keys are accepted. Unrequested aliases (e.g., `rms_threshold`, `sample_rate_hz`, `channels`) are rejected as unknown fields to eliminate alias-bypass vulnerabilities. Booleans are rejected for all numeric parameters.
- Rejects missing keys, unknown keys, booleans masquerading as numbers, non-finite values, and oversized integers ($\pm 10^{400}$) without leaking `OverflowError`.

---

## Equality Semantics & Operational Envelope Policies

1. **Sample Rate Equality:**
   - Evaluated using exact IEEE 754 binary64 numeric equality (`recording_rate == configured_rate`).
   - No tolerance or approximation is applied; prevents inadvertent spectral distortion or unverified resampling.
2. **Acceleration Unit Equality:**
   - Evaluated using exact string comparison (`'g'` or `'m/s^2'`).
   - No automatic gravity scaling ($9.80665\,\text{m/s}^2$) or unit conversion is performed.
3. **Channel Ordering Equality:**
   - Evaluated using exact list equality (`recording_channels == configured_channels`).
   - The detector does not reorder, permute, or drop channels.
4. **Quality Status Semantics:**
   - `PASS`: Used for `NORMAL` and `ANOMALY` decisions within valid sensor operating ranges.
   - `FAIL`: Used for recording-level `INSUFFICIENT_EVIDENCE` decisions failing envelope prerequisites.
   - `INDETERMINATE`: Used for window-level `INSUFFICIENT_EVIDENCE` decisions where $|x| \ge \text{full\_scale}$ indicates potential saturation.

---

## Window Feature Extraction (Stage 02 Reference)

### `extract_window_features(recording: dict, window_size: int, hop_size: int) -> dict`

Segments a validated vibration recording payload into complete, fixed-size windows and computes per-channel summary statistics.

- **Windowing Policy:** Emits complete windows of length `window_size` at offsets `0, hop_size, 2 * hop_size, ...`. Windows are only emitted when `start + window_size <= sample_count`. Trailing samples are explicitly accounted for.
- **Output:** Returns a dictionary adhering to `schema_version: "1.0.0"` with execution environment `"HOST_CPU"`. Every emitted feature metric is guaranteed to be a finite float.

### Helper Routines:
- `compute_channel_metrics(samples: Sequence[float]) -> dict[str, float]`: Computes `mean`, `rms`, and `peak_abs`.
- `stable_mean(values: Sequence[float]) -> float`: Exact rational arithmetic mean via `fractions.Fraction`.
- `stable_rms(values: Sequence[float]) -> float`: Scaled Euclidean 2-norm (Blue's algorithm) avoiding squaring overflow.
- `stable_peak_abs(values: Sequence[float]) -> float`: Maximum absolute amplitude.
- `WindowFeatureError`: Exception raised on invalid windowing parameters or non-finite values.

---

## Dependency & Release Verification Policy

- **Declared Dependencies:** `adi-runtime` declares `dependencies = ["adi-common>=0.1.0", "numpy>=1.24.0"]`.
- **Release Verification Gate:** `scripts/verify_portfolio_release.py` strictly requires standard dependency-resolved installation and a clean `uv pip check`. If required compatible wheels are unavailable offline, release verification halts with exit code 1 and reports status `BLOCKED`. Workarounds such as `--no-deps` are strictly forbidden for release gate passage.
