# Stage 12: Evidence Validator and Deterministic Measurement Admission Gate

**Document ID:** `docs/edge-sentinel/12-evidence-validator-admission-gate.md`  
**Stage:** 12 — Evidence Validator and Deterministic Measurement Admission Gate  
**Author:** Edge Sentinel Research, Systems & Data Engineering  
**Workspace:** `/Users/manuvashistha/Developer/ADI`  
**Supervisor:** Manu Vashisth  
**Prerequisite:** Stage 11 Final Cross-Stage Contract Reconciliation Accepted (`handoff/edge-sentinel/11-final-contract-integration-closeout.md`)  
**Archived Prompt:** `docs/edge-sentinel/prompts/12-evidence-validator-admission-gate.md`  
**Scope:** Production Evidence Validation & Deterministic Measurement Admission Gate Engine (Fictional Test Fixtures Only; Real-Data Ingestion & Operational Admission Strictly Disabled)  
**Status:** IMPLEMENTED AND INDEPENDENTLY VERIFIED — READY FOR SUPERVISOR REVIEW  

---

## 1. Executive Summary & Architectural Scope

Stage 11 established the formal evidence model, calibration mathematics, source provenance hierarchy, and admission protocol required before vibration recordings may enter the Edge Sentinel pipeline. Stage 12 transitions that accepted scientific design into a production-grade, deterministic evidence validation and measurement admission decision engine implemented in `adi-common` and exposed via `adi-portfolio`.

### 1.1 Inviolable Boundaries & Safety Guarantees
In strict accordance with project safety guidelines:
1. **Decision Engine Implementation Only:** Stage 12 implements the formal validation and evaluation engine. It does **not** grant authorization to process real vibration recordings.
2. **Zero Real-Data Ingestion:** No external datasets (such as CWRU, IMS, or NASA bearing repositories) were downloaded, cached, or processed. All development, unit tests, and CLI demonstrations use strictly fictional test fixtures and bounded synthetic arrays.
3. **Operational Admission Disabled:** Operational real-data admission remains strictly disabled at the architectural level. Calling `evaluate_measurement_admission` with `operational_mode=True` immediately raises `OperationalAdmissionDisabledError`. All fictional simulations output `evaluation_mode: "FICTIONAL_TEST_ONLY"` and `operational_execution_authorized: false`.
4. **Stage 10 Inspection Contract Immutability:** Stage 10 source inspection contracts (`ExploratorySourceInspectionV1`), structural preflight invariants, and report schemas remain 100% untouched. Stage 10 reports continue to record `measurement_readiness = "BLOCKED_UNKNOWN_METADATA"` and `detector_admission_eligible = false`.
5. **No Threshold or Anomaly Inference:** No anomaly detection models, threshold fitting, neural inference, or hardware performance claims were made or executed in Stage 12.

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                STAGE 12 MEASUREMENT ADMISSION ARCHITECTURE                              │
│                                                                                                         │
│   STAGE 10: SOURCE INSPECTION               STAGE 12: CALIBRATION EVIDENCE                              │
│   ┌──────────────────────────────────┐      ┌───────────────────────────────────────────────────────┐   │
│   │ ExploratorySourceInspectionV1    │      │ CalibrationMeasurementEvidenceV1                      │   │
│   │ - raw_byte_sha256 (64 hex)       │      │ - target_source_sha256, target_variable_name          │   │
│   │ - container_facts (format, shape)│      │ - sensor_identity (sensitivity, full scale, units)    │   │
│   │ - structural_status              │      │ - acquisition_signal_interpretation (sample rate)     │   │
│   │ - measurement_readiness: BLOCKED │      │ - dataset_legal_provenance (operational grants)       │   │
│   └──────────────────────────────────┘      │ - experiment_specimen_identity (specimen, labels)     │   │
│                    │                        └───────────────────────────────────────────────────────┘   │
│                    │                                                    │                               │
│                    ▼                                                    ▼                               │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │               STAGE 12 DETERMINISTIC MEASUREMENT ADMISSION GATE ENGINE                          │   │
│   │                                                                                                 │   │
│   │   [Gate 1: Structural Format]        --> Rejects malformed container                            │   │
│   │   [Gate 2: Signal Integrity]         --> Evaluates nonfinite values, rail saturation, clipping  │   │
│   │   [Gate 3: Usage Permissions]        --> Authenticates legal grant for intended operational use │   │
│   │   [Gate 4: Source Provenance]        --> Verifies 3-way SHA-256, variable name, sample count    │   │
│   │   [Gate 5: Physical Metadata]        --> Verifies sampling rate, units, full scale, sensitivity │   │
│   │   [Gate 6: Conflicting Evidence]     --> Detects irreconcilable primary source discrepancies    │   │
│   │   [Gate 7: Calibration Validity]     --> Verifies dimensional consistency & transfer function   │   │
│   │   [Gate 8: Measurement Admission]    --> Issues hypothetical admission in FICTIONAL_TEST_ONLY   │   │
│   │                                                                                                 │   │
│   │   [Decoupled Diagnostic Gate]        --> Evaluates specimen distinctness & label authority      │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                    │                                                    │
│                                                    ▼                                                    │
│   ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐   │
│   │ MeasurementAdmissionDecisionV1                                                                  │   │
│   │ - measurement_admission_status: ADMITTED_FOR_MEASUREMENT | BLOCKED_*                            │   │
│   │ - precedence_gate_reached: STRUCTURAL_FORMAT .. ADMISSION                                       │   │
│   │ - evaluation_mode: "FICTIONAL_TEST_ONLY"                                                        │   │
│   │ - operational_execution_authorized: false                                                       │   │
│   │ - diagnostic_evaluation_eligibility: { eligible: bool, status: ... }                            │   │
│   └─────────────────────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Canonical Production Schemas (Draft 2020-12)

Stage 12 establishes two canonical Draft 2020-12 production schemas packaged directly inside `adi-common`:

### 2.1 `CalibrationMeasurementEvidenceV1`
- **Location:** `adi-common/src/adi_common/schemas/calibration_measurement_evidence.schema.v1.json`
- **$id:** `https://analog.com/schemas/adi-edge/calibration_measurement_evidence.schema.v1.json`
- **Draft Standard:** `https://json-schema.org/draft/2020-12/schema`
- **Architecture:** Contains 22 rigorous reusable `$defs`. Enforces mutual exclusion across the five documented evidence states:
  - `DOCUMENTED_KNOWN`: Requires Tier 1 or 2 evidence reference, non-null resolved value, and positive confidence assessment.
  - `DERIVED_KNOWN`: Requires mathematical derivation statement and primary reference.
  - `UNKNOWN_UNSTATED`: Requires explicit evidence gap statement, forbids resolved values, and forbids evidence references.
  - `CONFLICTING_EVIDENCE`: Requires $\ge 2$ conflicting primary references, forbids resolved values, and sets confidence to `CONFLICTING`.
  - `NOT_APPLICABLE`: Forbids resolved values and requires applicability justification.
- **Physical Constraints:** Strictly enforces strictly positive values ($> 0$) for physical parameters such as sensitivity, sampling frequency, and full-scale limits, while permitting non-negative values ($\ge 0$) for rotational speed (static tests) and motor load.
- **Evidence-Backed Booleans:** Enforces that boolean flags (commercial grant, redistribution grant, specimen distinctness) are wrapped in evidence state objects rather than bare unverified booleans.

### 2.2 `MeasurementAdmissionDecisionV1`
- **Location:** `adi-common/src/adi_common/schemas/measurement_admission_decision.schema.v1.json`
- **$id:** `https://analog.com/schemas/adi-edge/measurement_admission_decision.schema.v1.json`
- **Draft Standard:** `https://json-schema.org/draft/2020-12/schema`
- **Invariants:**
  - `measurement_admission_status`: Strictly constrained to the 8 documented outcomes:
    `ADMITTED_FOR_MEASUREMENT`, `BLOCKED_UNSUPPORTED_FORMAT`, `BLOCKED_SIGNAL_INTEGRITY`, `BLOCKED_USAGE_RESTRICTIONS`, `BLOCKED_PROVENANCE`, `BLOCKED_UNKNOWN_METADATA`, `BLOCKED_CONFLICTING_EVIDENCE`, `BLOCKED_CALIBRATION`.
  - `precedence_gate_reached`: One of `STRUCTURAL_FORMAT`, `SIGNAL_INTEGRITY`, `USAGE_PERMISSIONS`, `SOURCE_PROVENANCE`, `PHYSICAL_METADATA`, `CONFLICTING_EVIDENCE`, `CALIBRATION_VALIDITY`, `ADMISSION`.
  - `evaluation_mode`: Strictly `"FICTIONAL_TEST_ONLY"`.
  - `operational_execution_authorized`: Invariant boolean `false`.
  - `diagnostic_evaluation_eligibility`: Independent nested object with status `ELIGIBLE_FOR_DIAGNOSTIC_EVALUATION`, `BLOCKED_AMBIGUOUS_SPECIMEN`, `BLOCKED_UNVERIFIED_FAULT_LABEL`, `BLOCKED_PARTITION_LEAKAGE`, or `PRECONDITION_FAILED`.

---

## 3. Evidence Registry & Anti-Fabrication Boundary

A critical vulnerability identified in Stage 11 review is that **a populated evidence-reference JSON object is not proof of authenticity**. An adversary or naive user can supply a syntactically valid JSON record claiming `status: "DOCUMENTED_KNOWN"`, `evidence_level: 1`, and `resolved_value: true` referencing a non-existent or unverified paper.

To prevent fabricated evidence admission, Stage 12 implements `EvidenceRegistry`:
- **Location:** `adi_common.admission.EvidenceRegistry`
- **Default Posture (Fail Closed):** `EvidenceRegistry.create_empty()` contains zero trusted documents. Any evidence reference evaluated against the default registry is treated as unauthenticated, causing immediate admission blocking at the corresponding precedence gate (`BLOCKED_USAGE_RESTRICTIONS` for legal grants, `BLOCKED_UNKNOWN_METADATA` for sensor specs, `BLOCKED_AMBIGUOUS_SPECIMEN` for teardown logs).
- **Authentication Model:** References are verified against registered document identities:
  ```python
  is_auth = registry.is_authenticated(evidence_reference, required_scope)
  ```
  Verification requires matching title, locator URI, minimum authority level, and accepted domain scopes (e.g., `TRANSDUCER_SPEC`, `LEGAL_GRANT`, `TEARDOWN_LOG`).
- **Fictional Test Registry:** `EvidenceRegistry.create_fictional_test_registry()` provides authenticated authorities specifically for unit testing and CLI demonstration fixtures.

---

## 4. Signal Integrity Verification Engine

Stage 12 implements pure numerical signal integrity analysis adhering to the Stage 11 provisional policy:
- **Location:** `adi_common.admission.analyze_signal_integrity`
- **Provisional Policy Rules:**
  1. **Non-finite Values:** Any NaN or Infinity sample immediately triggers `NONFINITE_VALUES_PRESENT` and blocks admission (`BLOCKED_SIGNAL_INTEGRITY`).
  2. **Contiguous Flat-Top Rail Saturation:** A run of $\ge 3$ consecutive samples within the rail threshold ($99.9\%$ of full scale) triggers `SUSTAINED_RAIL_SATURATION` and blocks admission.
  3. **Excessive Rail Clipping:** Total rail-sample count exceeding the $0.01\%$ threshold ($N_{\text{rail}} / N_{\text{total}} > 0.0001$) triggers `EXCESSIVE_CLIPPING` and blocks admission.
  4. **Unknown Physical Full Scale:** If transducer full-scale limit is unknown or unstated, signal integrity cannot be assessed; the gate fails closed (`BLOCKED_SIGNAL_INTEGRITY` or `BLOCKED_UNKNOWN_METADATA`).

---

## 5. Calibration & Dimensional Verification

Stage 12 implements metadata-level verification of the documented physical conversion chain:
- **Location:** `adi_common.admission.verify_calibration_conversion_chain`
- **Supported Architectures:**
  1. `ANALOG_VOLTAGE_TO_EXTERNAL_ADC`: Verifies analog transducer sensitivity (e.g. $100\text{ mV/g}$), AFE gain, ADC reference voltage, ADC word size (bits), and dimensional alignment with acceleration units ($g$ or $\text{m/s}^2$).
  2. `DIGITAL_INTEGRATED_MEMS`: Verifies digital register LSB sensitivity (e.g. $\text{LSB}/g$ or $\text{mg/LSB}$) without fictitious analog front-end gain components.
- **Dimensional Math Error Handling:** Incompatible sensitivity units (e.g. specifying sensitivity in $\text{mV/(m/s}^2)$ when calibrated output units are $g$) fail with `BLOCKED_CALIBRATION` and diagnostic reason `DIMENSIONAL_MATH_ERROR`.

---

## 6. Deterministic Measurement Admission Gate Engine

The central decision engine is implemented in `evaluate_measurement_admission`:
- **Location:** `adi_common.admission.evaluate_measurement_admission`
- **Function Signature:**
  ```python
  def evaluate_measurement_admission(
      evidence: Dict[str, Any],
      stage10_report: Dict[str, Any],
      intended_use: str = "ACADEMIC_RESEARCH_NONCOMMERCIAL",
      evidence_registry: Optional[EvidenceRegistry] = None,
      signal_integrity_evidence: Optional[Dict[str, Any]] = None,
      partition_assignments: Optional[Dict[str, str]] = None,
      operational_mode: bool = False,
  ) -> Dict[str, Any]:
  ```
- **Evaluation Precedence Hierarchy:**
  1. **Precedence Gate 1: STRUCTURAL_FORMAT:** Rejects container corruption, unsupported structures, or variable missing in Stage 10 report (`BLOCKED_UNSUPPORTED_FORMAT`).
  2. **Precedence Gate 2: SIGNAL_INTEGRITY:** Rejects non-finite values, sustained rail flat-topping, or excessive clipping (`BLOCKED_SIGNAL_INTEGRITY`). Fails closed if signal integrity is unassessed.
  3. **Precedence Gate 3: USAGE_PERMISSIONS:** Rejects missing affirmative commercial grant in `COMMERCIAL_R_AND_D` or `INTERNAL_TESTING`, missing redistribution grant in `DATASET_REDISTRIBUTION`, or missing research grant (`BLOCKED_USAGE_RESTRICTIONS`).
  4. **Precedence Gate 4: SOURCE_PROVENANCE:** Rejects disagreement across the 3 cryptographic digests (evidence target, Stage 10 raw bytes, legal provenance hash), target variable mismatch, or sample count mismatch (`BLOCKED_PROVENANCE`).
  5. **Precedence Gate 5: PHYSICAL_METADATA:** Rejects missing native sampling rate, missing acceleration units, missing full-scale limit, or missing sensitivity (`BLOCKED_UNKNOWN_METADATA`).
  6. **Precedence Gate 6: CONFLICTING_EVIDENCE:** Rejects conflicting primary documentation records (`BLOCKED_CONFLICTING_EVIDENCE`).
  7. **Precedence Gate 7: CALIBRATION_VALIDITY:** Rejects invalid conversion chains, dimensional math errors, or unverified transfer functions (`BLOCKED_CALIBRATION`).
  8. **Precedence Gate 8: ADMISSION:** Grants provisional hypothetical admission (`ADMITTED_FOR_MEASUREMENT`) in `FICTIONAL_TEST_ONLY` mode.

---

## 7. Decoupled Diagnostic Evaluation Eligibility Gate

Adhering to Stage 11 scientific requirements, physical measurement validity does not imply diagnostic evaluation validity:
- **Decoupled Evaluation:** A recording may be admitted for physical measurement (its acceleration waveform and calibration are authentic) while being rejected for diagnostic evaluation.
- **Diagnostic Eligibility Criteria:**
  1. `specimen_distinctness_proven`: Requires authenticated Tier 1 or 2 teardown log evidence. If unproven, status is `BLOCKED_AMBIGUOUS_SPECIMEN` (prevents cross-load generalization claims on unknown specimens).
  2. `ground_truth_label_authority`: Requires verified primary metrology inspection evidence. If unverified, status is `BLOCKED_UNVERIFIED_FAULT_LABEL`.
  3. `partition_assignments`: If benchmark partitions are supplied, detects whether the same physical specimen group appears across multiple partitions (e.g. `TRAIN` and `TEST`), blocking with `BLOCKED_PARTITION_LEAKAGE`.

---

## 8. CLI Integration & Subcommands

Stage 12 extends the `adi-portfolio` CLI entry point with two subcommands:

### 8.1 `validate-measurement-evidence` (alias `validate-evidence`)
Validates a candidate calibration measurement evidence JSON file against the Draft 2020-12 schema:
```bash
adi-portfolio validate-measurement-evidence --evidence docs/fixtures/fictional_evidence.json
```
- **Exit Code 0:** Valid schema compliance. Outputs JSON:
  ```json
  {
    "evidence_record_id": "ev_rec_fictional_test_bench_01",
    "schema_version": "1.0.0",
    "status": "VALID",
    "target_channel": "DE_time"
  }
  ```
- **Exit Code 1:** Contract schema violation. Diagnostic written to stderr.
- **Exit Code 2:** File not found, unreadable, or malformed JSON syntax.

### 8.2 `simulate-measurement-admission` (alias `simulate-admission`)
Simulates deterministic admission evaluation on fictional fixtures using authenticated test authorities:
```bash
adi-portfolio simulate-measurement-admission \
  --evidence fictional_evidence.json \
  --inspection fictional_stage10_inspection.json \
  --signal-integrity fictional_signal_integrity.json \
  --context ACADEMIC_RESEARCH_NONCOMMERCIAL \
  --out decision_output.json
```
- **Exit Code 0:** `measurement_admission_status == "ADMITTED_FOR_MEASUREMENT"`.
- **Exit Code 1:** Any `BLOCKED_*` status (admission refused).
- **Exit Code 2:** CLI argument error, non-existent file, malformed JSON, or target `--out` file already exists (non-overwrite protection).

---

## 9. Verification Summary & Test Coverage

All verification checks were executed and independently passed:
1. **Workspace Test Suite (`make test`):**
   - `adi-common`: 114 passed (13 evidence validator tests, 5 signal integrity tests, 23 admission evaluator tests, 73 existing contract/inspection tests).
   - `adi-runtime`: 61 passed.
   - `adi-app`: 219 passed (15 new CLI admission tests, 204 existing CLI/ingestion/inspection tests).
   - **Total Tests Passed:** 394 passed across all packages.
2. **Historical Hash Verification (`make verify-hashes`):**
   - 2 historical manifests verified, 0 failures.
3. **Stage 11 Schema Verification (`scratch/verify_stage11_schema.py`):**
   - 32/32 design checks passed independently.
4. **Isolated Wheel Installation (`make verify-portfolio`):**
   - Built wheels offline for `adi-common`, `adi-runtime`, `adi-app`.
   - Verified offline dependency resolution and `uv pip check`.
   - Removed relocated sources before execution.
   - Verified installed contracts, Draft 2020-12 schemas, fail-closed empty registry, fictional test registry admission, operational disabled guard, and CLI subcommands in complete isolation.
   - Status: `PASS`.
