# ADI Data Contracts & Schema Invariants

Versioned contract specifications for the ADI Edge Sentinel portfolio.

---

## 1. Implemented Edge Sentinel Contracts (Stage 01)

These contracts are implemented, packaged as JSON schemas in `adi_common.schemas`, and enforced via semantic validators in `adi_common.validators`.

### A. V1 Vibration Recording Contract (`vibration_recording.schema.v1.json`)
Specifies the canonical exchange format for sampled multi-axis vibration sensor data.

- **Schema Version:** Strictly `"1.0.0"`.
- **Identifiers:** Nonempty strings for `recording_id`, `machine_id`, `session_id`, and `source_id`.
- **Provenance:** Whitelisted origin classification: `"synthetic"` or `"recorded"`.
- **Sample Rate:** `sample_rate_hz` must be a strictly positive, finite numeric value (`> 0`) representable within the IEEE 754 binary64 domain ($[-1.7976931348623157 \times 10^{308}, 1.7976931348623157 \times 10^{308}]$). Booleans, NaNs, Infinities, and oversized integers (e.g. $\pm 10^{400}$) are rejected with field-specific `ContractValidationError` without leaking `OverflowError`.
- **Physical Unit:** `acceleration_unit` must be explicitly either `"g"` or `"m/s^2"`. No implicit unit conversions or silent coercion.
- **Channels:** Ordered list of unique channels drawn from `["x", "y", "z"]` (length 1 to 3). Duplicate channels are forbidden.
- **Sample Matrix:** Nonempty 2D array with layout `[sample, channel]`.
  - Matrix must be strictly rectangular (ragged rows rejected).
  - Row length must exactly match `len(channels)`.
  - Every sample element must be a finite numeric value within the IEEE 754 binary64 domain; booleans, NaNs, Infinities, and oversized integers (e.g. $\pm 10^{400}$) are rejected with element-specific `ContractValidationError` (e.g. `samples[0][0]`).
- **Source Byte Integrity:** `source_sha256` must be an exact 64-character hexadecimal digest (`^[a-fA-F0-9]{64}$`, `minLength: 64`, `maxLength: 64`). Any leading/trailing whitespace, newlines, non-hex characters, or 63/65-character strings are strictly rejected.
  > [!IMPORTANT]
  > Matching `source_sha256` establishes raw byte-level integrity of the source payload. It does **not** establish authenticity, provenance veracity, or correctness of associated metadata.
- **Strict Boundaries:** `additionalProperties: false`. Any undeclared fields are rejected.

### B. V1 Anomaly Decision Contract (`anomaly_decision.schema.v1.json`)
Specifies the deterministic output format for detector anomaly assessments.

- **Schema Version:** Strictly `"1.0.0"`.
- **Identifiers:** Nonempty strings for `decision_id`, `recording_ref`, `detector_id`, and `detector_version`.
- **Outcomes:** Mutually exclusive classifications: `"NORMAL"`, `"ANOMALY"`, `"INSUFFICIENT_EVIDENCE"`.
- **Execution Environment:** Restricted strictly to `"HOST_CPU"`. Embedded silicon accelerators (e.g. MAX78000, SHARC) are rejected until hardware measurement pipelines are verified.
- **Quality Status:** Must be one of `"PASS"`, `"FAIL"`, `"INDETERMINATE"`.
- **Invariants for `NORMAL` and `ANOMALY`:**
  - `quality_status` must be `"PASS"`.
  - `score` and `threshold` must be finite numbers within the IEEE 754 binary64 domain ($[-1.7976931348623157 \times 10^{308}, 1.7976931348623157 \times 10^{308}]$). Booleans, NaNs, Infinities, oversized integers (e.g. $\pm 10^{400}$), and nulls are rejected with field-specific `ContractValidationError`.
  - **Decision Convention:**
    - `score >= threshold` $\implies$ `outcome = "ANOMALY"`.
    - `score < threshold` $\implies$ `outcome = "NORMAL"`.
    - Boundary condition: exact equality `score == threshold` evaluates to `ANOMALY`.
    - Contradictory outcome/score combinations fail closed with `ContractValidationError`.
- **Invariants for `INSUFFICIENT_EVIDENCE`:**
  - `score` must be `null` (`None`).
  - `threshold` must be `null` (`None`).
  - `reason_codes` must contain at least one explanatory string code (e.g. `SENSOR_CLIPPING_DETECTED`).
  > [!NOTE]
  > An outcome of `INSUFFICIENT_EVIDENCE` indicates unprocessable data or degraded quality; it must never be construed as an assertion of machine health or a completed diagnostic evaluation.
- **Strict Boundaries:** `additionalProperties: false`. Any undeclared fields are rejected.

---

## 2. Planned Generic Assistant Schemas (Unimplemented / Historical Reference)

The following schemas belong to the older generic assistant roadmap and are **unimplemented** at this stage:

1. `hardware_status.schema.json`:
   - Categorizes execution provider, hardware target (e.g. host CPU, MAX78000/MAX78002 CNN accelerator, SHARC DSP), and status (`measured`, `modelled`, `unverified`).
   - Cross-field invariant: `npu_status="measured"` requires `device_verified=true` and verified device logs.
   - *Status:* Unimplemented draft.

2. `evidence_claim.schema.json`:
   - Structured facts with exact units, provenance source ID, version, and verification timestamp.
   - *Status:* Unimplemented draft.

3. `decision.schema.json` (Generic Assistant):
   - Deterministic actions (`RECOMMEND`, `DO_NOT_RECOMMEND`, `ABSTAIN`) tied to catalog citations.
   - *Status:* Unimplemented draft. Replaced in Edge Sentinel by the concrete `anomaly_decision.schema.v1.json`.

4. `benchmark_result.schema.json`:
   - Standard benchmark payload reporting model revision, quantization format, precision, context length, sample timings, and explicit hardware classification.
   - *Status:* Unimplemented draft. Placeholder CLI currently returns `status: NOT_IMPLEMENTED`.
