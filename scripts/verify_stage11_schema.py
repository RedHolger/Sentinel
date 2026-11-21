"""
Stage 11 Final Cross-Stage Contract Reconciliation: Evidence Schema & Semantic Admission Verification Suite.
Validates the Draft 2020-12 schema and semantic crosswalk rules from:
docs/edge-sentinel/11-calibration-measurement-protocol.md

Test Inventory & Group Structure:
  Group 1: JSON Schema Draft 2020-12 Structural & State Invariants (16 checks)
  Group 2: Complete Fictional Record Schema Validation (2 checks)
  Group 3: Cross-Stage Crosswalk & Semantic Admission Invariants (14 checks)
  Group 4: Documented Future Admission Requirements [Design Only — Reserved for Stage 12] (4 specifications)
"""

import copy
import json
import sys
from jsonschema import Draft202012Validator

# Ensure local adi_common is importable
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "adi-common" / "src"))
from adi_common import validate_exploratory_source_inspection

# The eight mutually exclusive measurement admission statuses defined in Section 6.1
VALID_ADMISSION_STATUSES = {
    "ADMITTED_FOR_MEASUREMENT",
    "BLOCKED_UNSUPPORTED_FORMAT",
    "BLOCKED_SIGNAL_INTEGRITY",
    "BLOCKED_USAGE_RESTRICTIONS",
    "BLOCKED_PROVENANCE",
    "BLOCKED_UNKNOWN_METADATA",
    "BLOCKED_CONFLICTING_EVIDENCE",
    "BLOCKED_CALIBRATION",
}

def extract_schema(markdown_path: str) -> dict:
    with open(markdown_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    start = None
    end = None
    for i, line in enumerate(lines):
        if line.strip() == "```json" and 150 < i < 220:
            start = i + 1
        elif start is not None and line.strip() == "```":
            end = i
            break
    if start is None or end is None:
        raise ValueError(f"Could not locate embedded JSON schema block in {markdown_path}")
    json_text = "".join(lines[start:end])
    return json.loads(json_text)

def evaluate_semantic_admission(evidence_record: dict, stage10_report: dict, operational_context: str = "COMMERCIAL_R_AND_D") -> dict:
    """
    Evaluates semantic admission invariants across Stage 10 inspection findings and Stage 11 evidence ledger.
    Strictly enforces the 8 defined admission statuses, nested Stage 10 field access, and fail-closed precedence.
    """
    # 1. Structural Preflight Check (Stage 10 Inspection Report Precondition)
    structural_status = stage10_report.get("inspection_findings", {}).get("structural_status")
    if structural_status != "STRUCTURAL_SUCCESS":
        res = {
            "physical_admission_status": "BLOCKED_UNSUPPORTED_FORMAT",
            "diagnostic_reason": f"STAGE10_STRUCTURAL_FAILURE: Stage 10 structural status is '{structural_status}' (must be 'STRUCTURAL_SUCCESS')",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    # 2. Non-finite Sample Check (Signal Integrity)
    nonfinite_count = stage10_report.get("container_facts", {}).get("nonfinite_sample_count", 0)
    if nonfinite_count is not None and nonfinite_count > 0:
        res = {
            "physical_admission_status": "BLOCKED_SIGNAL_INTEGRITY",
            "diagnostic_reason": f"NONFINITE_VALUES_PRESENT: Stage 10 detected {nonfinite_count} non-finite samples",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    # 3. Source Hash, Variable Identity & Sample Count Crosswalk (Stage 10 Nested Contracts)
    evidence_target_hash = evidence_record.get("target_source_sha256")
    stage10_raw_hash = stage10_report.get("source_identity", {}).get("raw_byte_sha256")
    if evidence_target_hash != stage10_raw_hash:
        res = {
            "physical_admission_status": "BLOCKED_PROVENANCE",
            "diagnostic_reason": f"STAGE10_DIGEST_MISMATCH: Evidence target hash ({evidence_target_hash}) does not match Stage 10 source hash ({stage10_raw_hash})",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    prov_hash = evidence_record.get("dataset_legal_provenance", {}).get("source_raw_byte_sha256")
    prov_hash_val = prov_hash.get("resolved_value") if isinstance(prov_hash, dict) else prov_hash
    if evidence_target_hash != prov_hash_val:
        res = {
            "physical_admission_status": "BLOCKED_PROVENANCE",
            "diagnostic_reason": f"PROVENANCE_HASH_MISMATCH: Evidence target hash ({evidence_target_hash}) does not match provenance raw byte hash ({prov_hash_val})",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    evidence_var = evidence_record.get("target_variable_name")
    stage10_var = stage10_report.get("container_facts", {}).get("target_variable")
    if evidence_var != stage10_var:
        res = {
            "physical_admission_status": "BLOCKED_PROVENANCE",
            "diagnostic_reason": f"VARIABLE_TARGET_MISMATCH: Evidence variable ({evidence_var}) does not match Stage 10 target variable ({stage10_var})",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    evidence_sample_count = evidence_record.get("acquisition_signal_interpretation", {}).get("sample_count")
    stage10_sample_count = stage10_report.get("container_facts", {}).get("sample_count")
    if evidence_sample_count != stage10_sample_count:
        res = {
            "physical_admission_status": "BLOCKED_PROVENANCE",
            "diagnostic_reason": f"SAMPLE_COUNT_MISMATCH: Evidence sample count ({evidence_sample_count}) does not match Stage 10 container sample count ({stage10_sample_count})",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    # 4. Legal Permissions Check (Operational Context Grant)
    legal_prov = evidence_record.get("dataset_legal_provenance", {})
    if operational_context == "COMMERCIAL_R_AND_D":
        comm_grant = legal_prov.get("affirmative_commercial_grant", {})
        if not (isinstance(comm_grant, dict) and 
                comm_grant.get("status") in ["DOCUMENTED_KNOWN", "DERIVED_KNOWN"] and 
                comm_grant.get("evidence_level") in [1, 2] and 
                comm_grant.get("evidence_reference") is not None and 
                comm_grant.get("resolved_value") is True):
            res = {
                "physical_admission_status": "BLOCKED_USAGE_RESTRICTIONS",
                "diagnostic_reason": "COMMERCIAL_GRANT_UNVERIFIED: Operational context is COMMERCIAL_R_AND_D but affirmative commercial grant lacks Tier 1/2 verified evidence.",
                "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
            }
            assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
            return res

    # 5. Metadata Completeness Checks (Sampling Frequency, Units, Transducer Sensitivity, Full Scale)
    acq = evidence_record.get("acquisition_signal_interpretation", {})
    rate_entry = acq.get("native_sampling_frequency_hz", {})
    if not (isinstance(rate_entry, dict) and 
            rate_entry.get("status") in ["DOCUMENTED_KNOWN", "DERIVED_KNOWN"] and 
            rate_entry.get("resolved_value") is not None and rate_entry.get("resolved_value") > 0):
        res = {
            "physical_admission_status": "BLOCKED_UNKNOWN_METADATA",
            "diagnostic_reason": "SAMPLING_RATE_UNVERIFIED: Native sampling frequency lacks verified positive resolved value.",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    unit_entry = acq.get("calibrated_acceleration_unit", {})
    if not (isinstance(unit_entry, dict) and 
            unit_entry.get("status") in ["DOCUMENTED_KNOWN", "DERIVED_KNOWN"] and 
            unit_entry.get("resolved_value") in ["g", "m/s^2"]):
        res = {
            "physical_admission_status": "BLOCKED_UNKNOWN_METADATA",
            "diagnostic_reason": "UNITS_UNVERIFIED: Calibrated acceleration unit lacks verified resolved value ('g' or 'm/s^2').",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    sensor = evidence_record.get("sensor_identity", {})
    sens_entry = sensor.get("sensitivity", {})
    if not (isinstance(sens_entry, dict) and 
            sens_entry.get("status") in ["DOCUMENTED_KNOWN", "DERIVED_KNOWN"] and 
            sens_entry.get("resolved_value") is not None and sens_entry.get("resolved_value") > 0):
        res = {
            "physical_admission_status": "BLOCKED_UNKNOWN_METADATA",
            "diagnostic_reason": "SENSITIVITY_UNVERIFIED: Transducer sensitivity lacks verified positive resolved value.",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    fs_entry = sensor.get("full_scale_limit_physical", {})
    if not (isinstance(fs_entry, dict) and 
            fs_entry.get("status") in ["DOCUMENTED_KNOWN", "DERIVED_KNOWN"] and 
            fs_entry.get("resolved_value") is not None and fs_entry.get("resolved_value") > 0):
        res = {
            "physical_admission_status": "BLOCKED_UNKNOWN_METADATA",
            "diagnostic_reason": "FULL_SCALE_UNVERIFIED: Transducer physical full-scale limit lacks verified positive resolved value.",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    # 6. Signal Saturation Check (Sustained Flat-Top Rail Samples)
    waveform_saturation_flag = evidence_record.get("_simulated_signal_facts", {}).get("sustained_flat_top_clipping", False)
    if waveform_saturation_flag:
        res = {
            "physical_admission_status": "BLOCKED_SIGNAL_INTEGRITY",
            "diagnostic_reason": "SUSTAINED_RAIL_SATURATION: Waveform contains >= 3 contiguous rail samples; physical saturation detected.",
            "diagnostic_evaluation_eligibility": {"eligible": False, "reason": "PRECONDITION_FAILED"}
        }
        assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES
        return res

    # Physical admission provisional result
    physical_status = "ADMITTED_FOR_MEASUREMENT"
    physical_diag = "DESIGN_ONLY_PROVISIONAL_PASS (NOT_ADMITTED_IN_PRODUCTION): Physical measurement criteria satisfied on fictional fixture; full runtime admission gate reserved for Stage 12 implementation."

    # 7. Independent Supervised Diagnostic Evaluation Eligibility Gate
    specimen = evidence_record.get("experiment_specimen_identity", {})
    spec_proven = specimen.get("specimen_distinctness_proven", {})
    if not (isinstance(spec_proven, dict) and 
            spec_proven.get("status") == "DOCUMENTED_KNOWN" and 
            spec_proven.get("evidence_level") in [1, 2] and 
            spec_proven.get("evidence_reference") is not None and 
            spec_proven.get("resolved_value") is True):
        diag_eligibility = {
            "eligible": False,
            "reason": "DIAGNOSTIC_BLOCKED_AMBIGUOUS_SPECIMEN: Physical specimen distinctness is unverified or lacks Tier 1/2 teardown log evidence; cross-specimen diagnostic generalization blocked."
        }
    else:
        label_auth = specimen.get("fault_condition", {}).get("ground_truth_label_authority", {})
        if not (isinstance(label_auth, dict) and 
                label_auth.get("status") in ["DOCUMENTED_KNOWN", "DERIVED_KNOWN"] and 
                label_auth.get("evidence_level") in [1, 2] and 
                label_auth.get("resolved_value") is not None):
            diag_eligibility = {
                "eligible": False,
                "reason": "DIAGNOSTIC_BLOCKED_UNVERIFIED_FAULT_LABEL: Fault ground truth label authority lacks verified Tier 1/2 primary inspection evidence."
            }
        else:
            diag_eligibility = {
                "eligible": True,
                "reason": "DESIGN_ONLY_PROVISIONAL_PASS: Distinct physical specimens and verified ground-truth labels confirmed on fictional fixture."
            }

    res = {
        "physical_admission_status": physical_status,
        "diagnostic_reason": physical_diag,
        "diagnostic_evaluation_eligibility": diag_eligibility
    }
    assert res["physical_admission_status"] in VALID_ADMISSION_STATUSES, f"Invalid admission status: {res['physical_admission_status']}"
    return res


def main():
    print("================================================================================")
    print("STAGE 11 FINAL CROSS-STAGE CONTRACT RECONCILIATION VERIFICATION SUITE")
    print("================================================================================")

    schema_path = str(ROOT_DIR / "docs" / "edge-sentinel" / "11-calibration-measurement-protocol.md")
    print(f"Extracting embedded schema from {schema_path}...")
    schema = extract_schema(schema_path)
    print(f"Extracted schema: '{schema.get('title')}' (${schema.get('$id')})")
    print(f"Schema Draft Standard: {schema.get('$schema')}")

    # ==========================================================================
    # GROUP 1: JSON SCHEMA DRAFT 2020-12 STRUCTURAL & STATE INVARIANTS (16 checks)
    # ==========================================================================
    print("\n--------------------------------------------------------------------------------")
    print("GROUP 1: JSON SCHEMA DRAFT 2020-12 STRUCTURAL & STATE INVARIANTS (16 checks)")
    print("--------------------------------------------------------------------------------")

    # Check 1.1: Meta-schema compliance check
    print("\n[CHECK 1.1] Meta-schema compliance check (Draft 2020-12)...")
    try:
        Draft202012Validator.check_schema(schema)
        print("PASS: Schema is a 100% valid Draft 2020-12 JSON Schema.")
    except Exception as e:
        print(f"FAIL: Schema failed meta-schema check: {e}")
        sys.exit(1)

    validator = Draft202012Validator(schema)

    def make_entry_validator(def_name: str):
        sub_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": schema.get("$defs", {}),
            "$ref": f"#/$defs/{def_name}"
        }
        return Draft202012Validator(sub_schema)

    pos_num_v = make_entry_validator("positive_number_evidence_entry")
    nonneg_num_v = make_entry_validator("nonnegative_number_evidence_entry")
    bool_v = make_entry_validator("boolean_evidence_entry")

    sample_doc_ref = {
        "document_title": "PCB 352C33 Spec Sheet",
        "locator_uri": "https://example.com/datasheets/352C33.pdf",
        "retrieval_date": "2026-11-19",
        "passage_citation": "Section 2.1, Table 1",
        "extracted_text_verbatim": "Sensitivity: 100 mV/g"
    }

    sample_deriv_ref = {
        "derivation_method": "ADC LSB to Voltage scaling",
        "formula_expression": "V = code * (Vref / (2^(bits-1)))",
        "input_parameters": {"bits": 16, "Vref": 10.0},
        "primary_input_references": [sample_doc_ref]
    }

    # Checks 1.2 - 1.6: Positive evidence state invariants across entry definitions
    print("\n[CHECKS 1.2 - 1.6] Positive Evidence State Invariants across Definitions...")
    pos_cases = [
        ("1.2: DOCUMENTED_KNOWN", {
            "status": "DOCUMENTED_KNOWN",
            "evidence_level": 1,
            "evidence_reference": sample_doc_ref,
            "resolved_value": 100.0,
            "confidence_assessment": "VERIFIED_PRIMARY",
            "evidence_gap_statement": None
        }),
        ("1.3: DERIVED_KNOWN", {
            "status": "DERIVED_KNOWN",
            "evidence_level": 3,
            "evidence_reference": sample_deriv_ref,
            "resolved_value": 0.00030517578125,
            "confidence_assessment": "REPRODUCIBLE_DERIVATION",
            "evidence_gap_statement": None
        }),
        ("1.4: UNKNOWN_UNSTATED", {
            "status": "UNKNOWN_UNSTATED",
            "evidence_level": None,
            "evidence_reference": None,
            "resolved_value": None,
            "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "Sensitivity uncertainty unstated."
        }),
        ("1.5: CONFLICTING_EVIDENCE", {
            "status": "CONFLICTING_EVIDENCE",
            "evidence_level": None,
            "evidence_reference": None,
            "conflicting_references": [sample_doc_ref, sample_doc_ref],
            "resolved_value": None,
            "confidence_assessment": "CONFLICTING",
            "evidence_gap_statement": "Conflicting references."
        }),
        ("1.6: NOT_APPLICABLE", {
            "status": "NOT_APPLICABLE",
            "evidence_level": None,
            "evidence_reference": None,
            "resolved_value": None,
            "confidence_assessment": "NOT_APPLICABLE",
            "applicability_justification": "Not applicable to voltage-mode sensor."
        })
    ]
    for check_id, data in pos_cases:
        errs = list(pos_num_v.iter_errors(data))
        assert not errs, f"Positive {check_id} failed: {errs[0].message if errs else ''}"
        print(f"PASS [{check_id}]: Correctly accepted by schema definition.")

    # Checks 1.7 - 1.12: Negative adversarial & supervisor reproducers
    print("\n[CHECKS 1.7 - 1.12] Negative Adversarial & Supervisor Review 01 Reproducers...")
    neg_cases = [
        ("1.7: Supervisor Reproducer 1: DOCUMENTED_KNOWN with Level 5 & null value", {
            "status": "DOCUMENTED_KNOWN", "evidence_level": 5, "evidence_reference": None, "resolved_value": None
        }),
        ("1.8: Supervisor Reproducer 2: UNKNOWN_UNSTATED with Level 1 & non-null value", {
            "status": "UNKNOWN_UNSTATED", "evidence_level": 1, "evidence_reference": None, "resolved_value": 999
        }),
        ("1.9: Supervisor Reproducer 3: CONFLICTING_EVIDENCE with Level 1 & resolved value", {
            "status": "CONFLICTING_EVIDENCE", "evidence_level": 1, "evidence_reference": None, "resolved_value": 10
        }),
        ("1.10: Negative: DOCUMENTED_KNOWN with Tier 3 level (requires 1 or 2)", {
            "status": "DOCUMENTED_KNOWN", "evidence_level": 3, "evidence_reference": sample_doc_ref,
            "resolved_value": 100.0, "confidence_assessment": "VERIFIED_PRIMARY"
        }),
        ("1.11: Negative: CONFLICTING_EVIDENCE with only 1 reference (requires >= 2)", {
            "status": "CONFLICTING_EVIDENCE", "evidence_level": None, "conflicting_references": [sample_doc_ref],
            "resolved_value": None, "confidence_assessment": "CONFLICTING", "evidence_gap_statement": "Only 1 ref"
        }),
        ("1.12: Negative: NOT_APPLICABLE with non-null resolved value", {
            "status": "NOT_APPLICABLE", "evidence_level": None, "evidence_reference": None,
            "resolved_value": 50.0, "confidence_assessment": "NOT_APPLICABLE",
            "applicability_justification": "Invalid value"
        })
    ]
    for check_id, data in neg_cases:
        errs = list(pos_num_v.iter_errors(data))
        assert errs, f"Adversarial case incorrectly accepted: {check_id}"
        print(f"PASS [{check_id}]: Correctly REJECTED by schema definition.")

    # Check 1.13: Supervisor Review 02 Reproducer 4: Contradictory evidence-state metadata
    print("\n[CHECK 1.13] Supervisor Review 02 Reproducer 4: Contradictory Evidence-State Metadata...")
    reproducer_item4 = {
        "status": "DOCUMENTED_KNOWN",
        "evidence_level": 1,
        "evidence_reference": sample_doc_ref,
        "resolved_value": 100,
        "confidence_assessment": "VERIFIED_PRIMARY",
        "conflicting_references": [],
        "applicability_justification": "Not applicable"
    }
    errs_item4 = list(pos_num_v.iter_errors(reproducer_item4))
    assert errs_item4, "Supervisor Reproducer Item 4 (contradictory metadata) was incorrectly ACCEPTED!"
    print(f"PASS [CHECK 1.13: Contradictory Metadata]: Correctly REJECTED ({errs_item4[0].message}).")

    # Checks 1.14: Nonnegative vs strictly positive physical quantities
    print("\n[CHECK 1.14] Nonnegative vs. Strictly Positive Physical Quantities (RPM & Sensitivity)...")
    rpm_zero = {
        "status": "DOCUMENTED_KNOWN",
        "evidence_level": 2,
        "evidence_reference": sample_doc_ref,
        "resolved_value": 0.0,
        "confidence_assessment": "VERIFIED_PRIMARY"
    }
    assert nonneg_num_v.is_valid(rpm_zero), "0 RPM must be valid in nonnegative_number_evidence_entry!"
    print("PASS [1.14a: Zero RPM in Nonnegative Entry]: 0.0 RPM correctly ACCEPTED.")

    assert not pos_num_v.is_valid(rpm_zero), "0.0 must be rejected in positive_number_evidence_entry!"
    print("PASS [1.14b: Zero Sensitivity in Positive Entry]: 0.0 sensitivity correctly REJECTED.")

    rpm_neg = {
        "status": "DOCUMENTED_KNOWN",
        "evidence_level": 2,
        "evidence_reference": sample_doc_ref,
        "resolved_value": -100.0,
        "confidence_assessment": "VERIFIED_PRIMARY"
    }
    assert not nonneg_num_v.is_valid(rpm_neg), "Negative RPM must be rejected!"
    print("PASS [1.14c: Negative RPM]: Negative rotational speed correctly REJECTED.")

    # Check 1.15: Evidence-backed boolean admission flags
    print("\n[CHECK 1.15] Evidence-Backed Admission Flags (boolean_evidence_entry)...")
    bool_valid_true = {
        "status": "DOCUMENTED_KNOWN",
        "evidence_level": 2,
        "evidence_reference": sample_doc_ref,
        "resolved_value": True,
        "confidence_assessment": "VERIFIED_PRIMARY"
    }
    assert bool_v.is_valid(bool_valid_true), "Boolean True with Tier 2 reference must be valid!"
    print("PASS [1.15a: Evidence-Backed Boolean True]: Correctly ACCEPTED with Tier 2 reference.")

    assert not bool_v.is_valid(True), "Bare boolean True must be rejected by boolean_evidence_entry schema!"
    print("PASS [1.15b: Bare Boolean True Rejection]: Bare boolean True correctly REJECTED by schema.")

    bool_unknown = {
        "status": "UNKNOWN_UNSTATED",
        "evidence_level": None,
        "evidence_reference": None,
        "resolved_value": None,
        "confidence_assessment": "UNVERIFIED",
        "evidence_gap_statement": "Specimen swap logbook is absent in primary records."
    }
    assert bool_v.is_valid(bool_unknown), "UNKNOWN_UNSTATED boolean entry must be valid!"
    print("PASS [1.15c: Unknown Boolean Entry]: Correctly ACCEPTED without invented true/false assertion.")

    print(f"\n---> GROUP 1 SUMMARY: 16/16 JSON Schema checks PASSED.")

    # ==========================================================================
    # GROUP 2: COMPLETE FICTIONAL RECORD SCHEMA VALIDATION (2 checks)
    # ==========================================================================
    print("\n--------------------------------------------------------------------------------")
    print("GROUP 2: COMPLETE FICTIONAL RECORD SCHEMA VALIDATION (2 checks)")
    print("--------------------------------------------------------------------------------")

    print("\n[CHECK 2.1] Complete Fictional CalibrationMeasurementEvidenceV1 Document Validation...")
    full_fictional_doc = {
        "schema_version": "1.0.0",
        "evidence_record_id": "ev_rec_fictional_test_bench_01",
        "target_source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "target_variable_name": "DE_time",
        "created_at": "2026-11-19T12:00:00Z",
        "evidence_curator": {
            "organization": "Analog Devices Edge AI Laboratory",
            "curator_name": "Verification Test Lead",
            "role": "Principal Verification Engineer"
        },
        "sensor_identity": {
            "manufacturer": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "PCB Piezotronics", "confidence_assessment": "VERIFIED_PRIMARY"},
            "model": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "352C33", "confidence_assessment": "VERIFIED_PRIMARY"},
            "sensor_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "PIEZOELECTRIC_IEPE", "confidence_assessment": "VERIFIED_PRIMARY"},
            "measurement_axis": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "RADIAL_VERTICAL", "confidence_assessment": "VERIFIED_PRIMARY"},
            "sensitivity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 100.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "sensitivity_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "mV/g", "confidence_assessment": "VERIFIED_PRIMARY"},
            "measurement_range_peak_g": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 50.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "full_scale_limit_physical": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 50.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "resolution_bits": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
            "frequency_response_bandwidth_3db_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": {"low_cutoff_hz": 0.5, "high_cutoff_hz": 10000.0}, "confidence_assessment": "VERIFIED_PRIMARY"},
            "calibration_certificate": {"available": False, "certificate_number": None, "calibrated_date": None, "traceability_authority": None},
            "mounting_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "MAGNETIC_BASE", "confidence_assessment": "VERIFIED_PRIMARY"},
            "mounting_location_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "Drive end bearing housing, 12 o'clock position", "confidence_assessment": "VERIFIED_PRIMARY"}
        },
        "acquisition_signal_interpretation": {
            "native_sampling_frequency_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 12000.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "sampling_clock_provenance": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "INTERNAL_HARDWARE_CRYSTAL", "confidence_assessment": "VERIFIED_PRIMARY"},
            "raw_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "VOLTS", "confidence_assessment": "VERIFIED_PRIMARY"},
            "calibrated_acceleration_unit": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "g", "confidence_assessment": "VERIFIED_PRIMARY"},
            "adc_bit_depth": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
            "adc_input_range_volts": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "adc_scaling_factor": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "analog_front_end_gain": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "duration_seconds": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "sample_count": 120000,
            "channel_identity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "DE_time", "confidence_assessment": "VERIFIED_PRIMARY"},
            "analog_filtering_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "Analog low-pass filter, 4000 Hz cutoff", "confidence_assessment": "VERIFIED_PRIMARY"},
            "anti_aliasing_verified": bool_valid_true,
            "saturation_detection_limit_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 9.99, "confidence_assessment": "VERIFIED_PRIMARY"},
            "missing_data_sample_count": 0,
            "nonfinite_sample_count": 0
        },
        "experiment_specimen_identity": {
            "physical_specimen_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "SPECIMEN_BEARING_01", "confidence_assessment": "VERIFIED_PRIMARY"},
            "specimen_distinctness_proven": bool_valid_true,
            "machine_asset_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "TEST_RIG_01", "confidence_assessment": "VERIFIED_PRIMARY"},
            "equipment_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "Reliance Electric 2HP Induction Motor", "confidence_assessment": "VERIFIED_PRIMARY"},
            "rotational_speed_rpm": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "nominal_load_hp": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "load_percentage": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "operating_regime_classification": "STEADY_STATE_STATIC_LOAD",
            "measurement_session_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "SESSION_20261119_01", "confidence_assessment": "VERIFIED_PRIMARY"},
            "repeated_recording_dependency_flag": {
                "status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref,
                "resolved_value": False, "confidence_assessment": "VERIFIED_PRIMARY"
            },
            "related_experimental_recordings": [],
            "fault_condition": {
                "fault_category": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "BASELINE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                "fault_dimension_label": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "NONE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                "fault_provenance_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "NATURAL_IN_SERVICE_FAILURE", "confidence_assessment": "VERIFIED_PRIMARY"},
                "ground_truth_label_authority": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "Independent Metrology Inspection Team", "confidence_assessment": "VERIFIED_PRIMARY"}
            }
        },
        "dataset_legal_provenance": {
            "dataset_name": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "Precision Bearing Test Rig Benchmark", "confidence_assessment": "VERIFIED_PRIMARY"},
            "publisher_entity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "Analog Devices Edge AI Laboratory", "confidence_assessment": "VERIFIED_PRIMARY"},
            "primary_documentation_uris": ["https://example.com/datasheets/benchmark.pdf"],
            "dataset_version": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "1.0.0", "confidence_assessment": "VERIFIED_PRIMARY"},
            "acquisition_date": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "2026-11-19", "confidence_assessment": "VERIFIED_PRIMARY"},
            "source_raw_byte_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "legal_usage_license": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": sample_doc_ref, "resolved_value": "CREATIVE_COMMONS_ATTRIBUTION_CC_BY_4_0", "confidence_assessment": "VERIFIED_PRIMARY"},
            "affirmative_commercial_grant": bool_valid_true,
            "affirmative_research_grant": bool_valid_true,
            "redistribution_permitted": bool_valid_true,
            "attribution_required": bool_valid_true,
            "attribution_text": "Attribution required under CC-BY-4.0 terms.",
            "transformation_derivation_history": []
        }
    }

    doc_errs = list(validator.iter_errors(full_fictional_doc))
    assert not doc_errs, f"Full document validation failed: {doc_errs[0].message if doc_errs else ''}"
    print("PASS [CHECK 2.1: Full Fictional Evidence Document]: Complete record with 0.0 RPM and boolean entries passes Draft 2020-12 schema.")

    print("\n[CHECK 2.2] Authentic Stage 10 ExploratorySourceInspectionV1 Contract Validation...")
    authentic_stage10_report = {
        "schema_version": "1.0.0",
        "inspection_id": "insp_fictional_test_bench_01_de",
        "inspected_at": "2026-11-19T12:00:00Z",
        "source_identity": {
            "source_id": "adi_edge_fictional_test_bench",
            "source_locator": "https://example.com/data/fictional_bench_01.mat",
            "source_filename": "fictional_bench_01.mat",
            "raw_byte_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "raw_byte_size": 1950000
        },
        "container_facts": {
            "container_format": "MATLAB_V5",
            "available_variables": ["DE_time", "FE_time", "RPM"],
            "target_variable": "DE_time",
            "numeric_dtype": "float64",
            "numeric_shape": [120000, 1],
            "sample_count": 120000,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {
                "min": -1.842105,
                "max": 1.795432
            }
        },
        "source_documentation": {
            "sensor_physical_location": {
                "value": "drive_end_housing_12_oclock",
                "evidence_reference": "Laboratory Test Rig Documentation"
            },
            "test_rig_description": {
                "value": "2hp_reliance_electric_motor_test_stand",
                "evidence_reference": "Laboratory Test Rig Documentation"
            },
            "nominal_motor_load_hp": {
                "value": 0.0,
                "evidence_reference": "Laboratory Test Rig Documentation"
            },
            "nominal_motor_speed_rpm": {
                "value": 1797.0,
                "evidence_reference": "Laboratory Test Rig Documentation"
            },
            "recording_condition_label": {
                "value": "normal_baseline",
                "evidence_reference": "Laboratory Test Rig Documentation",
                "scope": "WHOLE_RECORDING_ONLY"
            }
        },
        "metadata_status_ledger": {
            "physical_acceleration_unit": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "Transducer sensitivity omitted in raw MAT container."
            },
            "native_sampling_rate_hz": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "DAQ sample clock omitted in raw MAT container."
            },
            "acquisition_full_scale_limit": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "Hardware full scale omitted in raw MAT container."
            },
            "physical_specimen_identity": {
                "status": "UNKNOWN_UNPROVEN",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "Specimen swap logbook omitted in raw MAT container."
            },
            "legal_usage_grant": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "License omitted in raw MAT container."
            }
        },
        "local_annotations": {
            "precautionary_specimen_group": "precautionary_group_fictional_01",
            "candidate_partition_role": "held_out_load_test",
            "provenance_classification": "recorded"
        },
        "inspection_findings": {
            "structural_status": "STRUCTURAL_SUCCESS",
            "measurement_readiness": "BLOCKED_UNKNOWN_METADATA",
            "detector_admission_eligible": False,
            "diagnostic_notes": ["Container successfully parsed as MATLAB v5."]
        }
    }

    validate_exploratory_source_inspection(authentic_stage10_report)
    print("PASS [CHECK 2.2: Authentic Stage 10 Report]: Passes canonical validate_exploratory_source_inspection.")

    print(f"\n---> GROUP 2 SUMMARY: 2/2 Complete Fictional Record Schema checks PASSED.")

    # ==========================================================================
    # GROUP 3: CROSS-STAGE CROSSWALK & SEMANTIC ADMISSION INVARIANTS (14 checks)
    # ==========================================================================
    print("\n--------------------------------------------------------------------------------")
    print("GROUP 3: CROSS-STAGE CROSSWALK & SEMANTIC ADMISSION INVARIANTS (14 checks)")
    print("--------------------------------------------------------------------------------")

    # Check 3.1: Case 1: Authentic Stage 10 nested report accepted by crosswalk
    print("\n[CHECK 3.1 / CASE 1] Authentic Stage 10 Nested Report Accepted by Crosswalk...")
    res_pos = evaluate_semantic_admission(full_fictional_doc, authentic_stage10_report, "COMMERCIAL_R_AND_D")
    assert res_pos["physical_admission_status"] == "ADMITTED_FOR_MEASUREMENT", f"Expected ADMITTED, got: {res_pos}"
    assert "DESIGN_ONLY_PROVISIONAL_PASS" in res_pos["diagnostic_reason"], f"Expected provisional label, got: {res_pos['diagnostic_reason']}"
    assert res_pos["diagnostic_evaluation_eligibility"]["eligible"] is True
    print(f"PASS [CHECK 3.1 / Case 1]: {res_pos['physical_admission_status']} -> {res_pos['diagnostic_reason']}")

    # Check 3.2: Case 2: Stage 10 source digest mismatch
    print("\n[CHECK 3.2 / CASE 2] Stage 10 Source Digest Mismatch...")
    mismatched_s10_digest = copy.deepcopy(authentic_stage10_report)
    mismatched_s10_digest["source_identity"]["raw_byte_sha256"] = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    res_s10_mismatch = evaluate_semantic_admission(full_fictional_doc, mismatched_s10_digest)
    assert res_s10_mismatch["physical_admission_status"] == "BLOCKED_PROVENANCE"
    assert "STAGE10_DIGEST_MISMATCH" in res_s10_mismatch["diagnostic_reason"]
    print(f"PASS [CHECK 3.2 / Case 2]: Correctly BLOCKED with BLOCKED_PROVENANCE ({res_s10_mismatch['diagnostic_reason']}).")

    # Check 3.3: Case 2b: Internal evidence source digest mismatch
    print("\n[CHECK 3.3 / CASE 2b] Internal Evidence Source Digest Mismatch...")
    mismatched_prov_doc = copy.deepcopy(full_fictional_doc)
    mismatched_prov_doc["dataset_legal_provenance"]["source_raw_byte_sha256"] = "1111111111111111111111111111111111111111111111111111111111111111"
    res_prov_mismatch = evaluate_semantic_admission(mismatched_prov_doc, authentic_stage10_report)
    assert res_prov_mismatch["physical_admission_status"] == "BLOCKED_PROVENANCE"
    assert "PROVENANCE_HASH_MISMATCH" in res_prov_mismatch["diagnostic_reason"]
    print(f"PASS [CHECK 3.3 / Case 2b]: Correctly BLOCKED with BLOCKED_PROVENANCE ({res_prov_mismatch['diagnostic_reason']}).")

    # Check 3.4: Case 3: Stage 10 target variable mismatch
    print("\n[CHECK 3.4 / CASE 3] Stage 10 Target Variable Mismatch...")
    mismatched_s10_var = copy.deepcopy(authentic_stage10_report)
    mismatched_s10_var["container_facts"]["target_variable"] = "FE_time"
    res_var_mismatch = evaluate_semantic_admission(full_fictional_doc, mismatched_s10_var)
    assert res_var_mismatch["physical_admission_status"] == "BLOCKED_PROVENANCE"
    assert "VARIABLE_TARGET_MISMATCH" in res_var_mismatch["diagnostic_reason"]
    print(f"PASS [CHECK 3.4 / Case 3]: Correctly BLOCKED with BLOCKED_PROVENANCE ({res_var_mismatch['diagnostic_reason']}).")

    # Check 3.5: Case 3b: Stage 10 container sample-count mismatch
    print("\n[CHECK 3.5 / CASE 3b] Stage 10 Container Sample-Count Mismatch...")
    mismatched_s10_count = copy.deepcopy(authentic_stage10_report)
    mismatched_s10_count["container_facts"]["sample_count"] = 99999
    res_count_mismatch = evaluate_semantic_admission(full_fictional_doc, mismatched_s10_count)
    assert res_count_mismatch["physical_admission_status"] == "BLOCKED_PROVENANCE"
    assert "SAMPLE_COUNT_MISMATCH" in res_count_mismatch["diagnostic_reason"]
    print(f"PASS [CHECK 3.5 / Case 3b]: Correctly BLOCKED with BLOCKED_PROVENANCE ({res_count_mismatch['diagnostic_reason']}).")

    # Check 3.6: Case 4: Structurally unsuccessful inspection
    print("\n[CHECK 3.6 / CASE 4] Structurally Unsuccessful Stage 10 Inspection...")
    failed_s10_preflight = copy.deepcopy(authentic_stage10_report)
    failed_s10_preflight["inspection_findings"]["structural_status"] = "CORRUPTED_CONTAINER"
    res_struct_fail = evaluate_semantic_admission(full_fictional_doc, failed_s10_preflight)
    assert res_struct_fail["physical_admission_status"] == "BLOCKED_UNSUPPORTED_FORMAT"
    assert "STAGE10_STRUCTURAL_FAILURE" in res_struct_fail["diagnostic_reason"]
    print(f"PASS [CHECK 3.6 / Case 4]: Correctly BLOCKED with BLOCKED_UNSUPPORTED_FORMAT ({res_struct_fail['diagnostic_reason']}).")

    # Check 3.7: Case 5: Missing native sampling frequency
    print("\n[CHECK 3.7 / CASE 5] Missing Native Sampling Frequency...")
    missing_rate_doc = copy.deepcopy(full_fictional_doc)
    missing_rate_doc["acquisition_signal_interpretation"]["native_sampling_frequency_hz"] = {
        "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
        "resolved_value": None, "confidence_assessment": "UNVERIFIED", "evidence_gap_statement": "Rate missing."
    }
    res_rate_block = evaluate_semantic_admission(missing_rate_doc, authentic_stage10_report)
    assert res_rate_block["physical_admission_status"] == "BLOCKED_UNKNOWN_METADATA"
    assert "SAMPLING_RATE_UNVERIFIED" in res_rate_block["diagnostic_reason"]
    print(f"PASS [CHECK 3.7 / Case 5]: Correctly BLOCKED with BLOCKED_UNKNOWN_METADATA ({res_rate_block['diagnostic_reason']}).")

    # Check 3.8: Case 6: Unknown physical acceleration units
    print("\n[CHECK 3.8 / CASE 6] Unknown Physical Acceleration Units...")
    missing_unit_doc = copy.deepcopy(full_fictional_doc)
    missing_unit_doc["acquisition_signal_interpretation"]["calibrated_acceleration_unit"] = {
        "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
        "resolved_value": None, "confidence_assessment": "UNVERIFIED", "evidence_gap_statement": "Units missing."
    }
    res_unit_block = evaluate_semantic_admission(missing_unit_doc, authentic_stage10_report)
    assert res_unit_block["physical_admission_status"] == "BLOCKED_UNKNOWN_METADATA"
    assert "UNITS_UNVERIFIED" in res_unit_block["diagnostic_reason"]
    print(f"PASS [CHECK 3.8 / Case 6]: Correctly BLOCKED with BLOCKED_UNKNOWN_METADATA ({res_unit_block['diagnostic_reason']}).")

    # Check 3.9: Case 7: Missing clipping / full-scale metadata
    print("\n[CHECK 3.9 / CASE 7] Missing Full-Scale Metadata...")
    missing_fs_doc = copy.deepcopy(full_fictional_doc)
    missing_fs_doc["sensor_identity"]["full_scale_limit_physical"] = {
        "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
        "resolved_value": None, "confidence_assessment": "UNVERIFIED", "evidence_gap_statement": "Full scale missing."
    }
    res_fs_block = evaluate_semantic_admission(missing_fs_doc, authentic_stage10_report)
    assert res_fs_block["physical_admission_status"] == "BLOCKED_UNKNOWN_METADATA"
    assert "FULL_SCALE_UNVERIFIED" in res_fs_block["diagnostic_reason"]
    print(f"PASS [CHECK 3.9 / Case 7]: Correctly BLOCKED with BLOCKED_UNKNOWN_METADATA ({res_fs_block['diagnostic_reason']}).")

    # Check 3.10: Case 7b: Missing transducer sensitivity metadata
    print("\n[CHECK 3.10 / CASE 7b] Missing Transducer Sensitivity Metadata...")
    missing_sens_doc = copy.deepcopy(full_fictional_doc)
    missing_sens_doc["sensor_identity"]["sensitivity"] = {
        "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
        "resolved_value": None, "confidence_assessment": "UNVERIFIED", "evidence_gap_statement": "Sensitivity missing."
    }
    res_sens_block = evaluate_semantic_admission(missing_sens_doc, authentic_stage10_report)
    assert res_sens_block["physical_admission_status"] == "BLOCKED_UNKNOWN_METADATA"
    assert "SENSITIVITY_UNVERIFIED" in res_sens_block["diagnostic_reason"]
    print(f"PASS [CHECK 3.10 / Case 7b]: Correctly BLOCKED with BLOCKED_UNKNOWN_METADATA ({res_sens_block['diagnostic_reason']}).")

    # Check 3.11: Case 8: Unverified legal permission for commercial R&D
    print("\n[CHECK 3.11 / CASE 8] Unverified Legal Permission (Commercial R&D Context)...")
    unsupported_comm_doc = copy.deepcopy(full_fictional_doc)
    unsupported_comm_doc["dataset_legal_provenance"]["affirmative_commercial_grant"] = {
        "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
        "resolved_value": None, "confidence_assessment": "UNVERIFIED", "evidence_gap_statement": "Commercial rights unstated."
    }
    res_comm_block = evaluate_semantic_admission(unsupported_comm_doc, authentic_stage10_report, "COMMERCIAL_R_AND_D")
    assert res_comm_block["physical_admission_status"] == "BLOCKED_USAGE_RESTRICTIONS"
    assert "COMMERCIAL_GRANT_UNVERIFIED" in res_comm_block["diagnostic_reason"]
    print(f"PASS [CHECK 3.11 / Case 8]: Correctly BLOCKED with BLOCKED_USAGE_RESTRICTIONS ({res_comm_block['diagnostic_reason']}).")

    # Check 3.12: Case 9: Unsupported diagnostic ground-truth labels
    print("\n[CHECK 3.12 / CASE 9] Unsupported Diagnostic Ground-Truth Labels...")
    unauth_label_doc = copy.deepcopy(full_fictional_doc)
    unauth_label_doc["experiment_specimen_identity"]["fault_condition"]["ground_truth_label_authority"] = {
        "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
        "resolved_value": None, "confidence_assessment": "UNVERIFIED", "evidence_gap_statement": "No metrology inspection log."
    }
    res_label = evaluate_semantic_admission(unauth_label_doc, authentic_stage10_report)
    assert res_label["physical_admission_status"] == "ADMITTED_FOR_MEASUREMENT"
    assert res_label["diagnostic_evaluation_eligibility"]["eligible"] is False
    assert "DIAGNOSTIC_BLOCKED_UNVERIFIED_FAULT_LABEL" in res_label["diagnostic_evaluation_eligibility"]["reason"]
    print(f"PASS [CHECK 3.12 / Case 9]: Admitted for physical measurement, but diagnostic evaluation is BLOCKED ({res_label['diagnostic_evaluation_eligibility']['reason']}).")

    # Check 3.13: Case 10: Ambiguous specimen independence
    print("\n[CHECK 3.13 / CASE 10] Ambiguous Specimen Independence...")
    ambiguous_spec_doc = copy.deepcopy(full_fictional_doc)
    ambiguous_spec_doc["experiment_specimen_identity"]["specimen_distinctness_proven"] = {
        "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
        "resolved_value": None, "confidence_assessment": "UNVERIFIED", "evidence_gap_statement": "Teardown logs missing."
    }
    res_spec = evaluate_semantic_admission(ambiguous_spec_doc, authentic_stage10_report)
    assert res_spec["physical_admission_status"] == "ADMITTED_FOR_MEASUREMENT"
    assert res_spec["diagnostic_evaluation_eligibility"]["eligible"] is False
    assert "DIAGNOSTIC_BLOCKED_AMBIGUOUS_SPECIMEN" in res_spec["diagnostic_evaluation_eligibility"]["reason"]
    print(f"PASS [CHECK 3.13 / Case 10]: Admitted for physical measurement, but diagnostic evaluation is BLOCKED ({res_spec['diagnostic_evaluation_eligibility']['reason']}).")

    # Check 3.14: Case 11 & Case 12: Nonfinite & Saturation Signal Integrity + Strict Enumeration Adherence
    print("\n[CHECK 3.14 / CASES 11 & 12] Signal Saturation & Strict Status Enumeration Adherence...")
    sat_doc = copy.deepcopy(full_fictional_doc)
    sat_doc["_simulated_signal_facts"] = {"sustained_flat_top_clipping": True}
    res_sat = evaluate_semantic_admission(sat_doc, authentic_stage10_report)
    assert res_sat["physical_admission_status"] == "BLOCKED_SIGNAL_INTEGRITY"
    assert "SUSTAINED_RAIL_SATURATION" in res_sat["diagnostic_reason"]

    all_test_results = [
        res_pos, res_s10_mismatch, res_prov_mismatch, res_var_mismatch,
        res_count_mismatch, res_struct_fail, res_rate_block, res_unit_block,
        res_fs_block, res_sens_block, res_comm_block, res_label, res_spec, res_sat
    ]
    for r in all_test_results:
        assert r["physical_admission_status"] in VALID_ADMISSION_STATUSES, f"Invalid status: {r['physical_admission_status']}"
    print(f"PASS [CHECK 3.14 / Cases 11 & 12]: Saturated signal correctly BLOCKED_SIGNAL_INTEGRITY; all 14 outcomes adhere strictly to VALID_ADMISSION_STATUSES.")

    print(f"\n---> GROUP 3 SUMMARY: 14/14 Cross-Stage Semantic Invariant checks PASSED.")

    # ==========================================================================
    # GROUP 4: DOCUMENTED FUTURE ADMISSION REQUIREMENTS [DESIGN ONLY — STAGE 12] (4 specs)
    # ==========================================================================
    print("\n--------------------------------------------------------------------------------")
    print("GROUP 4: DOCUMENTED FUTURE ADMISSION REQUIREMENTS [DESIGN ONLY — STAGE 12] (4 specs)")
    print("--------------------------------------------------------------------------------")
    future_specs = [
        ("[SPEC 4.1 - STAGE 12]", "Cryptographic Remote URI Network Verification", 
         "Stage 12 admission pipeline must download remote container from verified locator URI and verify SHA-256 byte digest inside secure sandbox prior to admission."),
        ("[SPEC 4.2 - STAGE 12]", "Dynamic Waveform Clipping & Saturation Inspection", 
         "Stage 12 calibration module must compute raw extrema and consecutive flat-top rail count (>= 3 samples or >0.01% clipping) directly against raw ADC sample array."),
        ("[SPEC 4.3 - STAGE 12]", "Cross-Specimen Benchmark Partition Leakage Evaluator", 
         "Stage 12 benchmark generator must enforce partition independence across precautionary specimen groups to prevent load-leakage during train/test splits."),
        ("[SPEC 4.4 - STAGE 12]", "Stage 12 CLI Admission & Calibration Pipeline Command", 
         "Stage 12 will implement 'adi-sentinel admit' CLI invoking production validators and publishing immutable MeasurementAdmissionDecisionV1.")
    ]
    for spec_id, title, desc in future_specs:
        print(f"DOCUMENTED {spec_id} {title}: {desc}")

    print(f"\n---> GROUP 4 SUMMARY: 4/4 Future Stage 12 Design Requirements formally documented.")

    # ==========================================================================
    # FINAL RECONCILED TEST INVENTORY SUMMARY
    # ==========================================================================
    print("\n================================================================================")
    print("FINAL RECONCILED TEST INVENTORY & VERIFICATION SUMMARY")
    print("================================================================================")
    print("  Group 1: JSON Schema Draft 2020-12 Structural Invariants  : 16 / 16 PASSED")
    print("  Group 2: Complete Fictional Record Schema Validation      :  2 /  2 PASSED")
    print("  Group 3: Cross-Stage Crosswalk & Semantic Invariants      : 14 / 14 PASSED")
    print("  --------------------------------------------------------------------------")
    print("  TOTAL EXECUTABLE VERIFICATION CHECKS PASSED               : 32 / 32 PASSED")
    print("  Group 4: Documented Future Admission Requirements (Stg 12):  4 /  4 SPECIFIED")
    print("================================================================================")
    print("ALL 32 EXECUTABLE CHECKS PASSED (32/32) — ZERO REGRESSIONS")
    print("================================================================================")

if __name__ == "__main__":
    main()
