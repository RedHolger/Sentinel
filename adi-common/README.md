# ADI Common

Canonical versioned data contracts, JSON schemas, entity models, and semantic validation utilities for the ADI Edge Portfolio.

## Current State (Stage 01 — Sensor Contracts)

Package version: `0.1.0`.

Implemented and verified:
- **V1 Vibration Recording Contract (`vibration_recording.schema.v1.json`)**: Multi-axis vibration schema and validator enforcing rectangular `[sample, channel]` layout, positive sample rate in finite binary64 domain, explicit units (`g`, `m/s^2`), and exact 64-hex raw source SHA-256 byte integrity.
- **V1 Anomaly Decision Contract (`anomaly_decision.schema.v1.json`)**: Detector decision schema and validator enforcing outcome classifications (`NORMAL`, `ANOMALY`, `INSUFFICIENT_EVIDENCE`), `HOST_CPU` execution constraint, finite binary64 domain for scores/thresholds, score/threshold consistency (`score >= threshold -> ANOMALY`), and null-score requirements for insufficient evidence.
- **Packaged Resources**: Schemas packaged in `adi_common.schemas` and loaded via standard `importlib.resources`.
- **Pure Validation Engine**: Deterministic validators with zero side-effects (no file IO, no network calls, input immutability) using required `jsonschema` library with fail-closed dependency enforcement.

Older generic assistant schemas (`evidence_claim`, `hardware_status`, catalog `decision`, `benchmark_result`) remain planned/unimplemented references (see [CONTRACTS.md](file:///Users/manuvashistha/Developer/ADI/adi-common/CONTRACTS.md)).

## Contents

- `src/adi_common/schemas/`: Packaged JSON Schemas (`vibration_recording.schema.v1.json`, `anomaly_decision.schema.v1.json`).
- `src/adi_common/validators.py`: Semantic validation engine and packaged schema loader.
- `schemas/`: Repository schema mirror.
- `tests/test_contracts.py`: Comprehensive test suite (44 unit tests covering positive cases, failure modes, exact SHA-256 validation, finite binary64 domain, boundary conditions, immutability, and dependency fail-closed enforcement).

## Usage Example

```python
from adi_common import (
    ContractValidationError,
    load_schema,
    validate_anomaly_decision,
    validate_vibration_recording,
)

# Validate vibration recording payload
recording = {
    "schema_version": "1.0.0",
    "recording_id": "rec-001",
    "machine_id": "pump-west",
    "session_id": "sess-01",
    "provenance": "recorded",
    "sample_rate_hz": 4000.0,
    "acceleration_unit": "g",
    "channels": ["x", "y", "z"],
    "samples": [[0.1, 0.0, 9.8]],
    "source_id": "sensor-adxl355",
    "source_sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
}
validated_rec = validate_vibration_recording(recording)

# Validate anomaly decision payload
decision = {
    "schema_version": "1.0.0",
    "decision_id": "dec-001",
    "recording_ref": "rec-001",
    "detector_id": "energy-detector",
    "detector_version": "0.1.0",
    "outcome": "NORMAL",
    "execution_environment": "HOST_CPU",
    "quality_status": "PASS",
    "reason_codes": [],
    "score": 0.42,
    "threshold": 0.85,
}
validated_dec = validate_anomaly_decision(decision)
```
