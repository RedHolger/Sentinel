# 08-exploratory-inspection-design: Architectural Design for Isolated Source Inspection (Repaired)

**Document ID:** `docs/edge-sentinel/08-exploratory-inspection-design.md`  
**Stage:** 08 — Exploratory Inspection Design (Repair 01)  
**Author:** Edge Sentinel Research & Data Engineering  
**Prerequisite:** Stage 07 Accepted as Blocked Research Audit (`reviews/edge-sentinel/07-real-data-audit-repair-03.md`)  
**Supervisor Review Reference:** `reviews/edge-sentinel/08-exploratory-inspection-design.md`  
**Scope:** Architectural Design Only (Zero Downloads, Zero Installs, Zero Production Code/Schema Changes)  
**Status:** REPAIRED DESIGN — PENDING SUPERVISOR REVIEW  

---

## 1. Executive Summary & Context

Following the formal acceptance of Stage 07 as a blocked research audit (`reviews/edge-sentinel/07-real-data-audit-repair-03.md`), physical acceleration units, acquisition clipping limits, cross-record sampling rates, and bearing independence remain unproven in primary sources and their corresponding analysis gates remain strictly **BLOCKED**.

This document repairs and tightens the architectural design for an isolated exploratory source-inspection workflow. It establishes how uncalibrated external source recordings can be structurally characterized, inspected, and verified in future authorized work **without modifying existing production contracts, without weakening validation rules, and without admitting uncalibrated data into the anomaly detection pipeline**.

### Core Tenets of This Repaired Design
1. **Zero Contamination of Production Contracts:** The existing production contract (`VibrationRecordingV1`) requires positive sampling rates (`sample_rate_hz`), certified physical acceleration units (`acceleration_unit: "g" | "m/s^2"`), and orthogonal spatial axes (`channels: ["x", "y", "z"]`). It will **never** be widened to accommodate unknown units, null rates, or multi-location housing labels.
2. **Structural Validation $\ne$ Proof of Calibration:** Existing validators (such as `validate_vibration_recording` in `adi-common`) perform structural schema and type validation of supplied dictionary values; they do **not** authenticate physical calibration certificates or sensor physics. Admitting real data into a production pipeline requires a separate evidence-governed admission policy.
3. **Strict Evaluator & Detector Isolation:** The accepted synthetic scenario evaluator (`adi-portfolio evaluate`) strictly requires `provenance == "synthetic"`. The reference threshold detector (`evaluate_recording`) strictly requires a calibrated recording and matching configuration (`expected_sample_rate_hz`, `expected_acceleration_unit`, `full_scale`). Exploratory source inspections will never be passed to either component.
4. **Exploratory States $\ne$ Diagnostic Outcomes:** Diagnostic decision strings (`NORMAL`, `ANOMALY`, `INSUFFICIENT_EVIDENCE`) are reserved exclusively for calibrated operational evaluation. Exploratory source inspections output structural parsing states (`STRUCTURAL_SUCCESS`, `NONFINITE_VALUES_DETECTED`, `TARGET_VARIABLE_NOT_FOUND`, `MALFORMED_CONTAINER`, `UNSUPPORTED_STRUCTURE`, `RESOURCE_LIMIT_EXCEEDED`, `CORRUPTED_RAW_BYTES`), with measurement readiness tracked independently as `BLOCKED_UNKNOWN_METADATA`.
5. **Structural Success $\ne$ Measurement Readiness:** A file may parse cleanly with valid 64-bit float arrays and valid dimensions (`STRUCTURAL_SUCCESS`), but it remains 100% blocked for measurement and detector evaluation until primary documentation proves physical units, sensor full-scale range, and native sampling rates.
6. **Zero Implementation in Stage 08:** This document provides architecture and schema designs only. No code, schemas, test suites, third-party libraries, or datasets are downloaded, installed, or modified in this stage.

---

## 2. Architecture Decision Record (ADR): Exploratory Inspection vs. Total Blockade

### 2.1 Context and Problem Statement
Public vibration datasets (such as the Case Western Reserve University Bearing Data Center) contain real sensor time-series data stored in binary containers (MATLAB `.mat` files). However, primary documentation omits transducer sensitivities (mV/g), sensor gain settings, acquisition clipping limits, and native channel sampling rates.

Currently, two architectural paths exist:
- **Option A (Total Blockade):** Forbid any ingestion, parsing, or local file inspection of external sources until complete primary measurement metadata is published or retrieved.
- **Option B (Isolated Exploratory Source-Inspection Record):** Introduce a dedicated, isolated data contract and inspection workflow specifically designed to capture container structure, numeric dimensions, and explicit metadata status without converting to `VibrationRecordingV1`.

### 2.2 Evaluation of Options

#### Option A: Total Blockade (Status Quo)
- **Description:** Maintain a complete refusal to interact with external source files. Keep all analysis gates blocked and refuse to write any container parsers or inspection utilities until physical metadata is proven.
- **Advantages:** Zero risk of uncalibrated data leaking into detector algorithms; zero additional schema or tooling overhead.
- **Disadvantages:** Precludes any automated, reproducible characterization of candidate file integrity, file corruption, container layout, or array dimensions. Forces all research to remain purely manual HTML/text inspections, leaving container compatibility (e.g., MATLAB v4 vs v5, variable naming, endianness) completely unverified.

#### Option B: Dedicated Exploratory Source-Inspection Contract (Recommended)
- **Description:** Introduce an isolated exploratory record (`ExploratorySourceInspectionV1`) that characterizes container-level facts, array shapes, raw byte hashes, and explicit metadata statuses.
- **Strict Constraints:**
  - `VibrationRecordingV1` is preserved without changes (no optional units, no null rates).
  - The detector configuration (`ThresholdConfiguration`) and runtime detector (`evaluate_recording`) remain unmodified and strictly reject exploratory records.
  - Exploratory inspection never emits diagnostic outcomes (`NORMAL`, `ANOMALY`, `INSUFFICIENT_EVIDENCE`).
  - Synthetic scenario evaluation (`adi-portfolio evaluate`) remains isolated.
- **Advantages:** Enables rigorous, reproducible automated verification of file-level integrity, byte digests, array shapes, and variable names; explicitly documents known vs. unknown parameters in a machine-readable format; enforces an impenetrable wall between container inspection and anomaly detection.
- **Disadvantages:** Requires maintaining a separate schema and validation boundary.

### 2.3 Decision Outcome
**Adopt Option B: Dedicated Exploratory Source-Inspection Record.**  
Option B represents the smallest useful engineering artifact: it enables structural verification of candidate files without compromising the physical rigor and calibrated invariants of existing production contracts.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        ARCHITECTURAL ISOLATION BOUNDARY                                │
│                                                                                        │
│   UNVERIFIED RAW SOURCES               EXPLORATORY WORKFLOW (FUTURE)                   │
│   ┌────────────────────┐               ┌──────────────────────────────────────────┐    │
│   │ Candidate .mat     │ ────────────► │ ExploratorySourceInspectionV1            │    │
│   │ Raw Source Bytes   │ (Inspection)  │ - Raw SHA-256 Digest & Byte Length       │    │
│   └────────────────────┘               │ - Observed Container Array Names & Dtype │    │
│                                        │ - Explicit Metadata Status: UNKNOWN      │    │
│                                        │ - Structural Status: STRUCTURAL_SUCCESS  │    │
│                                        │ - Measurement Readiness: BLOCKED         │    │
│                                        └──────────────────────────────────────────┘    │
│                                                              │                         │
│                                                              ▼                         │
│   ══════════════════════════════════════════════════════════════════════════════════   │
│   INVIOLABLE FIREWALL: NO ADMISSION WITHOUT PROVEN PHYSICAL CALIBRATION & FULL SCALE   │
│   ══════════════════════════════════════════════════════════════════════════════════   │
│                                                              │                         │
│                                        PROHIBITED ADMISSION  X                         │
│                                                              ▼                         │
│   CALIBRATED CONTRACTS                 PRODUCTION PIPELINE (EXISTING)                  │
│   ┌────────────────────┐               ┌──────────────────────────────────────────┐    │
│   │ VibrationRecording │ ────────────► │ Threshold Reference Anomaly Detector     │    │
│   │ - Certified Units  │               │ - Full-Scale Saturation Gate             │    │
│   │ - Certified Rates  │               │ - RMS Window Scoring                     │    │
│   │ - Orthogonal Axes  │               │ - Evaluator: provenance == "synthetic"   │    │
│   └────────────────────┘               └──────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Proposed Inspection Report Schema (`ExploratorySourceInspectionV1`)

The schema defines a strictly typed, machine-readable record for structural source inspection. It enforces clean separation between:
1. **Observed Container Facts:** Directly measured properties of raw file bytes and internal data structures.
2. **Source Documentation:** Textual claims extracted from primary documentation (websites, manuals, papers).
3. **Local Annotations:** Internal repository context, precautionary grouping, and candidate partition roles.
4. **Metadata Status Ledger:** Explicit accounting of known vs. unknown physical parameters with field-specific typing and cited evidence references.
5. **Inspection Findings:** Objective structural parsing results, separating structural validity from measurement readiness.

### 3.1 Division of Responsibilities: JSON Schema vs. Runtime Validator

To ensure complete enforcement without ambiguity, responsibilities are cleanly divided:
- **Enforced Directly in JSON Schema (Draft 2020-12):**
  - Structural property existence, strict types, format constraints (`date-time`, `uri`, `pattern`).
  - Conditional metadata ledger rules via `oneOf`: if status is `UNKNOWN_*`, `resolved_value` MUST be null, `evidence_reference` MUST be null, and `evidence_gap_statement` MUST be a non-empty string. If status is `DOCUMENTED_KNOWN`, `resolved_value` MUST be non-null conforming to field-specific types, `evidence_reference` MUST be a structured object, and `evidence_gap_statement` MUST be null.
  - Status-dependent nullability via `allOf`/`if/then`: if `structural_status` is not `STRUCTURAL_SUCCESS` or `NONFINITE_VALUES_DETECTED`, container target array facts (`numeric_dtype`, `numeric_shape`, `sample_count`, `nonfinite_sample_count`, `observed_raw_extrema`) MUST be null.
  - Fixed invariant: `detector_admission_eligible: false` as an enum constant.
- **Enforced in Proposed Runtime Validator (`validate_exploratory_source_inspection`):**
  - Shape/count arithmetic consistency: exact equality $\text{sample\_count} = \prod_i \text{numeric\_shape}[i]$.
  - Target variable membership: `target_variable` must exist in `available_variables` when structural status is success; conversely, when `structural_status == "TARGET_VARIABLE_NOT_FOUND"`, `target_variable` must not be present in `available_variables`.
  - Extrema ordering: $\text{observed\_raw\_extrema.min} \le \text{observed\_raw\_extrema.max}$.
  - Nonfinite consistency: when `structural_status == "STRUCTURAL_SUCCESS"`, `nonfinite_sample_count == 0`; when `structural_status == "NONFINITE_VALUES_DETECTED"`, `nonfinite_sample_count > 0`.
  - Rejection of complex or sparse array types.

### 3.2 Draft JSON Schema Specification (`exploratory_source_inspection.schema.v1.json`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://analog.com/schemas/adi-edge/exploratory_source_inspection.schema.v1.json",
  "title": "ExploratorySourceInspectionV1",
  "description": "Schema for reporting structural inspection of uncalibrated or exploratory vibration data sources.",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "schema_version",
    "inspection_id",
    "inspected_at",
    "source_identity",
    "container_facts",
    "source_documentation",
    "metadata_status_ledger",
    "local_annotations",
    "inspection_findings"
  ],
  "properties": {
    "schema_version": {
      "type": "string",
      "enum": ["1.0.0"],
      "description": "Contract version. Must be '1.0.0'."
    },
    "inspection_id": {
      "type": "string",
      "pattern": "^insp_[a-z0-9_-]+$",
      "description": "Unique deterministic identifier for this inspection event, derived from source_id, raw_byte_sha256 prefix, and target_variable."
    },
    "inspected_at": {
      "type": "string",
      "format": "date-time",
      "description": "ISO-8601 UTC timestamp of inspection execution."
    },
    "source_identity": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "source_id",
        "source_locator",
        "source_filename",
        "raw_byte_sha256",
        "raw_byte_size"
      ],
      "properties": {
        "source_id": { "type": "string", "minLength": 1 },
        "source_locator": { "type": "string", "format": "uri" },
        "source_filename": { "type": "string", "minLength": 1 },
        "raw_byte_sha256": {
          "type": "string",
          "pattern": "^[a-f0-9]{64}$",
          "description": "Cryptographic SHA-256 of raw source bytes before parsing. Any container repackaging or byte change alters this digest."
        },
        "raw_byte_size": {
          "type": "integer",
          "minimum": 0,
          "description": "Exact byte length of the raw source file on disk."
        }
      }
    },
    "container_facts": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "container_format",
        "available_variables",
        "target_variable",
        "numeric_dtype",
        "numeric_shape",
        "sample_count",
        "nonfinite_sample_count",
        "observed_raw_extrema"
      ],
      "properties": {
        "container_format": {
          "type": "string",
          "enum": ["MATLAB_V4", "MATLAB_V5", "MATLAB_V7_3_HDF5", "CSV_ASCII", "UNRECOGNIZED"]
        },
        "available_variables": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Complete list of internal variable/array identifiers detected in the container."
        },
        "target_variable": {
          "type": ["string", "null"],
          "description": "Specific internal variable selected for channel inspection."
        },
        "numeric_dtype": {
          "type": ["string", "null"],
          "description": "Native binary datatype (e.g. 'float64', 'int16'). Must be null if structural status is not SUCCESS or NONFINITE."
        },
        "numeric_shape": {
          "type": ["array", "null"],
          "items": { "type": "integer", "minimum": 0 },
          "description": "Observed matrix dimensions [rows, cols]. Must be null if structural status is not SUCCESS or NONFINITE."
        },
        "sample_count": {
          "type": ["integer", "null"],
          "minimum": 0,
          "description": "Total discrete sample values in target array. Must be null if structural status is not SUCCESS or NONFINITE."
        },
        "nonfinite_sample_count": {
          "type": ["integer", "null"],
          "minimum": 0,
          "description": "Count of IEEE 754 NaN, +Inf, or -Inf values observed. Must be null if structural status is not SUCCESS or NONFINITE."
        },
        "observed_raw_extrema": {
          "type": ["object", "null"],
          "additionalProperties": false,
          "required": ["min", "max"],
          "properties": {
            "min": { "type": "number", "description": "Minimum raw uncalibrated finite sample value." },
            "max": { "type": "number", "description": "Maximum raw uncalibrated finite sample value." }
          },
          "description": "Uncalibrated sample extrema across finite values. Must NEVER be labeled with physical units. Null if array contains zero finite values or if container parsing failed."
        }
      }
    },
    "source_documentation": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "sensor_physical_location",
        "test_rig_description",
        "nominal_motor_load_hp",
        "nominal_motor_speed_rpm",
        "recording_condition_label"
      ],
      "properties": {
        "sensor_physical_location": {
          "type": "object",
          "additionalProperties": false,
          "required": ["value", "evidence_reference"],
          "properties": {
            "value": { "type": ["string", "null"] },
            "evidence_reference": { "type": "string" }
          }
        },
        "test_rig_description": {
          "type": "object",
          "additionalProperties": false,
          "required": ["value", "evidence_reference"],
          "properties": {
            "value": { "type": ["string", "null"] },
            "evidence_reference": { "type": "string" }
          }
        },
        "nominal_motor_load_hp": {
          "type": "object",
          "additionalProperties": false,
          "required": ["value", "evidence_reference"],
          "properties": {
            "value": { "type": ["number", "null"] },
            "evidence_reference": { "type": "string" }
          }
        },
        "nominal_motor_speed_rpm": {
          "type": "object",
          "additionalProperties": false,
          "required": ["value", "evidence_reference"],
          "properties": {
            "value": { "type": ["number", "null"] },
            "evidence_reference": { "type": "string" }
          }
        },
        "recording_condition_label": {
          "type": "object",
          "additionalProperties": false,
          "required": ["value", "evidence_reference", "scope"],
          "properties": {
            "value": { "type": "string" },
            "evidence_reference": { "type": "string" },
            "scope": {
              "type": "string",
              "enum": ["WHOLE_RECORDING_ONLY"],
              "description": "Explicit declaration that label applies strictly to the overall recording, never individual windows."
            }
          }
        }
      }
    },
    "metadata_status_ledger": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "physical_acceleration_unit",
        "native_sampling_rate_hz",
        "acquisition_full_scale_limit",
        "physical_specimen_identity",
        "legal_usage_grant"
      ],
      "properties": {
        "physical_acceleration_unit": {
          "oneOf": [
            { "$ref": "#/$defs/metadata_ledger_unknown" },
            {
              "type": "object",
              "additionalProperties": false,
              "required": ["status", "resolved_value", "evidence_reference", "evidence_gap_statement"],
              "properties": {
                "status": { "const": "DOCUMENTED_KNOWN" },
                "resolved_value": { "type": "string", "enum": ["g", "m/s^2"] },
                "evidence_reference": { "$ref": "#/$defs/evidence_reference_object" },
                "evidence_gap_statement": { "type": "null" }
              }
            }
          ]
        },
        "native_sampling_rate_hz": {
          "oneOf": [
            { "$ref": "#/$defs/metadata_ledger_unknown" },
            {
              "type": "object",
              "additionalProperties": false,
              "required": ["status", "resolved_value", "evidence_reference", "evidence_gap_statement"],
              "properties": {
                "status": { "const": "DOCUMENTED_KNOWN" },
                "resolved_value": { "type": "number", "exclusiveMinimum": 0 },
                "evidence_reference": { "$ref": "#/$defs/evidence_reference_object" },
                "evidence_gap_statement": { "type": "null" }
              }
            }
          ]
        },
        "acquisition_full_scale_limit": {
          "oneOf": [
            { "$ref": "#/$defs/metadata_ledger_unknown" },
            {
              "type": "object",
              "additionalProperties": false,
              "required": ["status", "resolved_value", "evidence_reference", "evidence_gap_statement"],
              "properties": {
                "status": { "const": "DOCUMENTED_KNOWN" },
                "resolved_value": { "type": "number", "exclusiveMinimum": 0 },
                "evidence_reference": { "$ref": "#/$defs/evidence_reference_object" },
                "evidence_gap_statement": { "type": "null" }
              }
            }
          ]
        },
        "physical_specimen_identity": {
          "oneOf": [
            { "$ref": "#/$defs/metadata_ledger_unknown" },
            {
              "type": "object",
              "additionalProperties": false,
              "required": ["status", "resolved_value", "evidence_reference", "evidence_gap_statement"],
              "properties": {
                "status": { "const": "DOCUMENTED_KNOWN" },
                "resolved_value": { "type": "string", "minLength": 1 },
                "evidence_reference": { "$ref": "#/$defs/evidence_reference_object" },
                "evidence_gap_statement": { "type": "null" }
              }
            }
          ]
        },
        "legal_usage_grant": {
          "oneOf": [
            { "$ref": "#/$defs/metadata_ledger_unknown" },
            {
              "type": "object",
              "additionalProperties": false,
              "required": ["status", "resolved_value", "evidence_reference", "evidence_gap_statement"],
              "properties": {
                "status": { "const": "DOCUMENTED_KNOWN" },
                "resolved_value": { "type": "string", "minLength": 1 },
                "evidence_reference": { "$ref": "#/$defs/evidence_reference_object" },
                "evidence_gap_statement": { "type": "null" }
              }
            }
          ]
        }
      }
    },
    "local_annotations": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "precautionary_specimen_group",
        "candidate_partition_role",
        "provenance_classification"
      ],
      "properties": {
        "precautionary_specimen_group": {
          "type": "string",
          "description": "Local clustering identifier to prevent data leakage across load runs."
        },
        "candidate_partition_role": {
          "type": "string",
          "enum": ["development_calibration", "held_out_load_test", "unassigned"]
        },
        "provenance_classification": {
          "type": "string",
          "enum": ["recorded"],
          "description": "Must be 'recorded'. Prevents ingestion into synthetic evaluators."
        }
      }
    },
    "inspection_findings": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "structural_status",
        "measurement_readiness",
        "detector_admission_eligible",
        "diagnostic_notes"
      ],
      "properties": {
        "structural_status": {
          "type": "string",
          "enum": [
            "STRUCTURAL_SUCCESS",
            "NONFINITE_VALUES_DETECTED",
            "TARGET_VARIABLE_NOT_FOUND",
            "MALFORMED_CONTAINER",
            "UNSUPPORTED_STRUCTURE",
            "RESOURCE_LIMIT_EXCEEDED",
            "CORRUPTED_RAW_BYTES"
          ]
        },
        "measurement_readiness": {
          "type": "string",
          "enum": [
            "BLOCKED_UNKNOWN_METADATA",
            "BLOCKED_NONFINITE_VALUES",
            "BLOCKED_TARGET_NOT_FOUND",
            "BLOCKED_UNSUPPORTED_FORMAT",
            "BLOCKED_CORRUPTED_FILE",
            "BLOCKED_RESOURCE_LIMIT"
          ]
        },
        "detector_admission_eligible": {
          "type": "boolean",
          "enum": [false],
          "description": "Inviolable invariant: Exploratory source inspections are NEVER eligible for detector admission."
        },
        "diagnostic_notes": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    }
  },
  "allOf": [
    {
      "if": {
        "properties": {
          "inspection_findings": {
            "properties": {
              "structural_status": { "enum": ["STRUCTURAL_SUCCESS", "NONFINITE_VALUES_DETECTED"] }
            }
          }
        }
      },
      "then": {
        "properties": {
          "container_facts": {
            "properties": {
              "target_variable": { "type": "string", "minLength": 1 },
              "numeric_dtype": { "type": "string", "enum": ["float64", "float32", "int16", "int32"] },
              "numeric_shape": { "type": "array", "minItems": 1, "maxItems": 2, "items": { "type": "integer", "minimum": 1 } },
              "sample_count": { "type": "integer", "minimum": 1 },
              "nonfinite_sample_count": { "type": "integer", "minimum": 0 }
            }
          }
        }
      },
      "else": {
        "properties": {
          "container_facts": {
            "properties": {
              "numeric_dtype": { "type": "null" },
              "numeric_shape": { "type": "null" },
              "sample_count": { "type": "null" },
              "nonfinite_sample_count": { "type": "null" },
              "observed_raw_extrema": { "type": "null" }
            }
          }
        }
      }
    }
  ],
  "$defs": {
    "metadata_ledger_unknown": {
      "type": "object",
      "additionalProperties": false,
      "required": ["status", "resolved_value", "evidence_reference", "evidence_gap_statement"],
      "properties": {
        "status": { "enum": ["UNKNOWN_UNSTATED", "UNKNOWN_UNPROVEN"] },
        "resolved_value": { "type": "null" },
        "evidence_reference": { "type": "null" },
        "evidence_gap_statement": { "type": "string", "minLength": 1 }
      }
    },
    "evidence_reference_object": {
      "type": "object",
      "additionalProperties": false,
      "required": ["source_document", "passage_or_table", "access_provenance"],
      "properties": {
        "source_document": { "type": "string", "minLength": 1 },
        "passage_or_table": { "type": "string", "minLength": 1 },
        "access_provenance": { "type": "string", "minLength": 1 }
      }
    }
  }
}
```

---

## 4. Inviolable System Boundaries & Anti-Patterns

To guarantee architectural integrity and prevent data corruption or false scientific claims, the following boundaries are established:

### 4.1 Boundary 1: Inspection Cannot Produce a Calibrated Recording
- A successful structural inspection (`structural_status: "STRUCTURAL_SUCCESS"`) establishes only that file bytes represent a readable numeric matrix.
- It **never** satisfies `VibrationRecordingV1`.
- A valid `VibrationRecordingV1` instance can be instantiated if and only if independent, authoritative documentation or calibration certificates establish:
  1. The precise sensitivity factor converting raw numeric units to physical acceleration ($g$ or $\text{m/s}^2$).
  2. The exact native sampling rate of that specific channel.
  3. Valid spatial orthogonal axis assignment ($x, y, z$).

### 4.2 Boundary 2: Detector Admission Boundary
Admission to the reference anomaly detector (`adi_runtime.evaluate_recording`) strictly requires:
1. Valid `VibrationRecordingV1` conforming to schema `1.0.0`.
2. Matching configuration sample rate: `recording.sample_rate_hz == config.expected_sample_rate_hz`.
3. Matching configuration acceleration unit: `recording.acceleration_unit == config.expected_acceleration_unit`.
4. Documented, non-null `full_scale` limit for saturation abstention checking.

Because exploratory source inspections have unknown rates, unknown units, and unknown full scale, **detector admission is unconditionally blocked (`detector_admission_eligible: false`)**.

### 4.3 Boundary 3: Strictly Prohibited Heuristics & Substitutions
Under no circumstances may the following invalid inferences be made:
- **File length $\ne$ sampling rate:** An array of 240,000 samples does not imply 20 seconds at 12 kHz, 5 seconds at 48 kHz, or any other duration.
- **Observed sample extrema $\ne$ sensor full scale:** Observing sample values between $-1.8$ and $+1.8$ does not imply a sensor range of $\pm 2.0\text{ g}$, $\pm 5.0\text{ V}$, or any other clipping limit. Saturation assessment must remain `UNAVAILABLE`, never marked `PASS`.
- **Motor RPM $\ne$ sensor sample rate:** Motor rotational frequency (e.g. 1797 RPM $\approx 29.95\text{ Hz}$) describes rotor speed, not DAQ sampling frequency.
- **Sensor catalog nominal range $\ne$ acquisition chain limits:** Third-party sensor datasheets (e.g. PCB 353B33 nominal $\pm 500\text{ g}$) do not establish electrical amplifier gains, filter cutoffs, or A/D clipping limits.
- **Informal benchmark conventions $\ne$ primary documentation:** Conventions adopted in secondary academic papers (e.g. assuming $1\text{ V} \equiv 1\text{ g}$) are not primary evidence and must never override primary source omissions.

### 4.4 Boundary 4: Evaluator Isolation
The synthetic scenario evaluation command (`adi-portfolio evaluate --suite PATH`) strictly requires `provenance == "synthetic"` on all evaluated cases. Exploratory sources carry `provenance: "recorded"`. The synthetic evaluator remains 100% isolated and cannot be modified to ingest exploratory records.

### 4.5 Boundary 5: Specimen Independence
Local precautionary group IDs (e.g. `precautionary_group_cwru_normal`) are local clustering tags designed to prevent operational leakage across load runs. They do **not** prove that tested components were independent physical bearings. Independent bearing generalization remains **BLOCKED**.

---

## 5. Bounded Future Acquisition & Inspection Plan (Operational Safeguards)

If a future task is authorized to acquire and inspect the four candidate CWRU files (`97.mat`, `98.mat`, `105.mat`, `106.mat`), it must adhere to the following operational safeguards. **Zero network calls or file downloads are executed in this stage.**

### 5.1 Prospective Operational Safeguards
1. **Prospective Byte Limit (Operational Cap):**
   - Impose an enforced cap of **50 MB per file**.
   - Check the `Content-Length` header before download; if `Content-Length > 50 MB`, abort immediately.
   - During streaming, count transferred bytes; if stream exceeds 50 MB, truncate connection, delete partial file, and raise `OperationalLimitExceededError`.
   - *Note:* This limit is an operational safety boundary, not an empirical claim of actual remote file size.
2. **Network Timeout Policy:**
   - Connect timeout: **10 seconds**.
   - Read/transfer timeout: **30 seconds**.
   - Total session timeout per candidate: **60 seconds**.
3. **Strict Origin & Protocol Whitelist:**
   - Allowed download origin: strictly `https://engineering.case.edu/sites/default/files/`.
   - Cross-domain redirects are strictly forbidden (fail immediately).
   - Insecure protocol downgrades (`https://` $\to$ `http://`) are strictly forbidden.
4. **Atomic Storage & Raw Cryptographic Digesting:**
   - Stream download directly to a temporary staging file: `raw/cwru/staging/<filename>.part`.
   - Compute streaming SHA-256 simultaneously across raw incoming bytes.
   - Upon successful, error-free transfer within limits, atomically rename `.part` to `raw/cwru/<filename>`.
   - The verified SHA-256 and byte length must be recorded in the inspection report before any file parser is invoked.
5. **Execution Safety & Code Rejection:**
   - Never execute embedded code.
   - If the file container contains executable MATLAB functions, scripts, user-defined Java objects, or serialized class methods, the parser must refuse to instantiate them and immediately reject the file with `UNSUPPORTED_STRUCTURE`.
6. **Malformed & Truncated Transfer Handling:**
   - If a transfer fails mid-stream or connection drops, delete the incomplete `.part` file immediately. Never leave corrupt partial files on disk.

### 5.2 Legal and Licensing Status (Factual Ledger)
- **Primary Terms:** The official CWRU pages host files under general university copyright (*© 2024 Case Western Reserve University*) without an explicit open-source license, API terms of service, or formal redistribution grant.
- **Factual Status:** Public availability on a web server does not constitute an affirmative redistribution grant. This report records stated terms factually and does not provide a legal opinion.
- **Independence from Synthetic Design:** This factual legal unknown applies specifically to remote file scraping and public redistribution of CWRU files; it does **not** block local synthetic parser testing or offline schema validation.

---

## 6. Parser Operational Bounds, Dependency Strategy & Validation Matrix

Parsing binary MATLAB Level 5 files requires defensive operational bounds to prevent memory exhaustion, decompression bombs, or hung processes. No software is installed in this design stage.

### 6.1 Defensive Parser Operational Bounds
To prevent malicious or malformed files from destabilizing the environment, the future parser must enforce the following pre-allocation bounds:
1. **On-Disk Input File Size Cap:** Maximum **50 MB** on disk.
2. **Pre-Allocation Tag & Dimension Inspection (Decompression Bomb Guard):**
   - Malicious or corrupted Level 5 MAT files can declare massive matrix dimensions (e.g., $10^6 \times 10^6$) in small compressed tags.
   - A post-allocation check is fatal (causes OS kernel OOM or memory exhaustion).
   - The parser must inspect Level 5 tag headers (type and dimension sub-elements) **before** allocating numeric arrays:
     - Enforce maximum array rank: $\le 2$ (only 1D vectors and 2D single-column/row matrices supported).
     - Enforce maximum declared elements: **10,000,000 elements** ($\approx 80\text{ MB}$ uncompressed memory footprint for 64-bit floats).
     - If declared dimensions $\text{rows} \times \text{cols} > 10,000,000$, abort immediately *before* allocation with `structural_status: "RESOURCE_LIMIT_EXCEEDED"` and `measurement_readiness: "BLOCKED_RESOURCE_LIMIT"`.
3. **Execution Runtime Cap:**
   - Enforce a strict execution timeout of **10.0 seconds** wall-clock CPU time.
   - If parsing does not finish within 10.0s, abort execution with `RESOURCE_LIMIT_EXCEEDED`.
4. **Controlled Failure Behavior:**
   - Tripping any cap must emit a well-formed `ExploratorySourceInspectionV1` report with `structural_status: "RESOURCE_LIMIT_EXCEEDED"` and target array container facts set to `null`.

### 6.2 Narrow Initial Supported Scope
- **Supported Container Format:** Level 5 MATLAB binary (`MATLAB_V5`) containing real numeric 2D float64/float32 vectors.
- **Unsupported Formats:** Recognizing formats (e.g. MATLAB v4, MATLAB v7.3 HDF5, CSV) by header signature does **not** imply parser support. They are explicitly rejected with `UNSUPPORTED_STRUCTURE`.
- **Unsupported Data Types:** Complex numbers, sparse matrices, multidimensional arrays ($>2\text{D}$), nested MATLAB structs, cell arrays, and empty arrays ($N=0$) are strictly rejected with `UNSUPPORTED_STRUCTURE`.

### 6.3 Observed Environment vs. Proposed Addition
- **Actual Local Environment Observations:**
  - Python executable: `/Users/manuvashistha/Developer/ADI/.release/venv/bin/python` (CPython 3.11.15).
  - Installed packages in `.release/venv`: `numpy` version **2.4.6**; `jsonschema` version **4.26.0**.
  - SciPy status: **NOT INSTALLED**.
- **Dependency Strategy & Wheel Implications:**
  - Standard scientific parser `scipy.io.loadmat` is recommended over writing an ad-hoc in-house binary parser.
  - While `scipy.io.loadmat` is an active, widely maintained library (BSD-3-Clause), it is not inherently hardened against malformed dimension tags; the defensive pre-allocation bounds described in Section 6.1 are mandatory.
  - SciPy wheel file size in `.release/wheels/`: **PENDING_MEASUREMENT** (to be measured when a specific platform wheel is staged in a future authorized stage).

### 6.4 Future Parser Validation Test Matrix (Synthetic MAT Fixtures)
When an implementation stage is authorized, the parser must be validated against an offline synthetic test matrix created without remote access:
1. `test_mat_valid_float64_matrix`: Synthetic MAT v5 with valid float64 2D matrix $\implies$ verifies array dimensions, dtype, sample count, and raw-byte SHA-256.
2. `test_mat_malformed_header`: Corrupted 128-byte header $\implies$ rejects with `MALFORMED_CONTAINER`.
3. `test_mat_unsupported_struct`: Nested structs/cells $\implies$ rejects with `UNSUPPORTED_STRUCTURE`.
4. `test_mat_nonfinite_values`: IEEE 754 NaN/Inf $\implies$ reports `NONFINITE_VALUES_DETECTED`.
5. `test_mat_missing_target_variable`: Variable not in container $\implies$ reports `TARGET_VARIABLE_NOT_FOUND`.
6. `test_mat_excessive_dimensions`: Tag declares $> 10^7$ elements $\implies$ rejects pre-allocation with `RESOURCE_LIMIT_EXCEEDED`.

---

## 7. Explicitly Fictional Illustrative Report Examples

The following two examples illustrate the proposed schema. **These records are completely fictional models; no real files were downloaded or inspected.**

### 7.1 Example 1: Successful Structural Inspection with Unknown Metadata

```json
{
  "schema_version": "1.0.0",
  "inspection_id": "insp_cwru_fictional_97_de",
  "inspected_at": "2026-11-16T13:00:00Z",
  "source_identity": {
    "source_id": "cwru_bearing_data_center",
    "source_locator": "https://engineering.case.edu/sites/default/files/fictional_97.mat",
    "source_filename": "fictional_97.mat",
    "raw_byte_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "raw_byte_size": 1950000
  },
  "container_facts": {
    "container_format": "MATLAB_V5",
    "available_variables": ["X097_DE_time", "X097_FE_time", "X097_RPM"],
    "target_variable": "X097_DE_time",
    "numeric_dtype": "float64",
    "numeric_shape": [243938, 1],
    "sample_count": 243938,
    "nonfinite_sample_count": 0,
    "observed_raw_extrema": {
      "min": -1.842105,
      "max": 1.795432
    }
  },
  "source_documentation": {
    "sensor_physical_location": {
      "value": "drive_end_housing_12_oclock",
      "evidence_reference": "CWRU Apparatus & Procedures HTML page"
    },
    "test_rig_description": {
      "value": "2hp_reliance_electric_motor_test_stand",
      "evidence_reference": "CWRU Apparatus & Procedures HTML page"
    },
    "nominal_motor_load_hp": {
      "value": 0.0,
      "evidence_reference": "CWRU Normal Baseline Data HTML table"
    },
    "nominal_motor_speed_rpm": {
      "value": 1797.0,
      "evidence_reference": "CWRU Normal Baseline Data HTML table"
    },
    "recording_condition_label": {
      "value": "normal_baseline",
      "evidence_reference": "CWRU Normal Baseline Data HTML table",
      "scope": "WHOLE_RECORDING_ONLY"
    }
  },
  "metadata_status_ledger": {
    "physical_acceleration_unit": {
      "status": "UNKNOWN_UNSTATED",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Transducer sensitivity (mV/g) and physical unit omitted from primary documentation. File inspection confirms numeric floats but cannot guarantee physical units."
    },
    "native_sampling_rate_hz": {
      "status": "UNKNOWN_UNSTATED",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Normal baseline table omits sampling rate per channel. Sample count (243,938) cannot establish sample rate without time axis or calibration certificate."
    },
    "acquisition_full_scale_limit": {
      "status": "UNKNOWN_UNSTATED",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Hardware DAQ input range and clipping limits omitted. Observed sample extrema [-1.84, +1.79] cannot establish full-scale bounds."
    },
    "physical_specimen_identity": {
      "status": "UNKNOWN_UNPROVEN",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Primary documentation does not establish distinct bearing specimen identity across runs. Same test stand used."
    },
    "legal_usage_grant": {
      "status": "UNKNOWN_UNSTATED",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Official site hosts files under university copyright without explicit open-source license grant or terms of use."
    }
  },
  "local_annotations": {
    "precautionary_specimen_group": "precautionary_group_cwru_normal",
    "candidate_partition_role": "held_out_load_test",
    "provenance_classification": "recorded"
  },
  "inspection_findings": {
    "structural_status": "STRUCTURAL_SUCCESS",
    "measurement_readiness": "BLOCKED_UNKNOWN_METADATA",
    "detector_admission_eligible": false,
    "diagnostic_notes": [
      "Container successfully parsed as MATLAB v5.",
      "Target variable X097_DE_time extracted as 2D float64 matrix with zero nonfinite values.",
      "Measurement gates remain BLOCKED due to unstated physical units, unconfirmed sampling rate, and unstated DAQ full scale.",
      "Record is strictly ineligible for detector admission."
    ]
  }
}
```

### 7.2 Example 2: Rejected Malformed or Unsupported Source

```json
{
  "schema_version": "1.0.0",
  "inspection_id": "insp_fictional_corrupted_02",
  "inspected_at": "2026-11-16T13:00:00Z",
  "source_identity": {
    "source_id": "cwru_bearing_data_center",
    "source_locator": "https://engineering.case.edu/sites/default/files/corrupted_mock.mat",
    "source_filename": "corrupted_mock.mat",
    "raw_byte_sha256": "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
    "raw_byte_size": 4096
  },
  "container_facts": {
    "container_format": "UNRECOGNIZED",
    "available_variables": [],
    "target_variable": null,
    "numeric_dtype": null,
    "numeric_shape": null,
    "sample_count": null,
    "nonfinite_sample_count": null,
    "observed_raw_extrema": null
  },
  "source_documentation": {
    "sensor_physical_location": {
      "value": null,
      "evidence_reference": "Unavailable; container unreadable"
    },
    "test_rig_description": {
      "value": null,
      "evidence_reference": "Unavailable; container unreadable"
    },
    "nominal_motor_load_hp": {
      "value": null,
      "evidence_reference": "Unavailable; container unreadable"
    },
    "nominal_motor_speed_rpm": {
      "value": null,
      "evidence_reference": "Unavailable; container unreadable"
    },
    "recording_condition_label": {
      "value": "corrupted_candidate",
      "evidence_reference": "Local test stub",
      "scope": "WHOLE_RECORDING_ONLY"
    }
  },
  "metadata_status_ledger": {
    "physical_acceleration_unit": {
      "status": "UNKNOWN_UNSTATED",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Container unreadable."
    },
    "native_sampling_rate_hz": {
      "status": "UNKNOWN_UNSTATED",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Container unreadable."
    },
    "acquisition_full_scale_limit": {
      "status": "UNKNOWN_UNSTATED",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Container unreadable."
    },
    "physical_specimen_identity": {
      "status": "UNKNOWN_UNPROVEN",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Container unreadable."
    },
    "legal_usage_grant": {
      "status": "UNKNOWN_UNSTATED",
      "resolved_value": null,
      "evidence_reference": null,
      "evidence_gap_statement": "Container unreadable."
    }
  },
  "local_annotations": {
    "precautionary_specimen_group": "unassigned",
    "candidate_partition_role": "unassigned",
    "provenance_classification": "recorded"
  },
  "inspection_findings": {
    "structural_status": "MALFORMED_CONTAINER",
    "measurement_readiness": "BLOCKED_CORRUPTED_FILE",
    "detector_admission_eligible": false,
    "diagnostic_notes": [
      "File header does not match MATLAB Level 5 magic bytes or valid Level 4 binary structure.",
      "Container parsing aborted at byte offset 128 due to invalid header format.",
      "Structural validation failed: MALFORMED_CONTAINER."
    ]
  }
}
```

---

## 8. Summary of Smallest Proposed Future Implementation Task

When the supervisor authorizes implementation work in a subsequent stage, the smallest, fully bounded implementation task consists of:

1. **Schema Publication:** Publish `exploratory_source_inspection.schema.v1.json` under `adi-common/schemas/` with dedicated runtime validation functions (`validate_exploratory_source_inspection`).
2. **Parser Dependency Staging:** If authorized, vendor `scipy` into `.release/wheels/` and add to project virtualenv.
3. **Synthetic Validation Suite:** Author synthetic in-memory MAT fixture generator and unit tests verifying the 6 parsing failure modes without remote access.
4. **Offline CLI Tool:** Expose an isolated local inspection tool:
   ```bash
   adi-portfolio inspect-source --mat PATH --variable VARNAME --out REPORT.json
   ```
   Operating strictly on local files, enforcing defensive pre-allocation bounds, producing strictly `ExploratorySourceInspectionV1` JSON records, and forbidding any connection to `VibrationRecordingV1` or the anomaly detector.

**Current Stage Status:** Stage 08 is documentation only. Zero implementation, zero downloads, zero package installations, and zero test reruns have been performed.

```text
STATUS: READY FOR SUPERVISOR REVIEW
```
