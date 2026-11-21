"""Unit tests for CalibrationMeasurementEvidenceV1 and MeasurementAdmissionDecisionV1 validators."""

from __future__ import annotations

import copy
import json
import unittest

from adi_common.validators import (
    ContractValidationError,
    validate_calibration_measurement_evidence,
    validate_measurement_admission_decision,
    validate_signal_integrity_evidence,
)


class TestEvidenceValidators(unittest.TestCase):
    """Test suite for evidence and decision contract validators."""

    def setUp(self) -> None:
        self.doc_ref = {
            "document_title": "PCB 352C33 Spec Sheet",
            "locator_uri": "https://example.com/datasheets/352C33.pdf",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Section 2.1, Table 1",
            "extracted_text_verbatim": "Sensitivity: 100 mV/g",
        }
        self.valid_evidence = {
            "schema_version": "1.0.0",
            "evidence_record_id": "ev_rec_fictional_test_bench_01",
            "target_source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "target_variable_name": "DE_time",
            "created_at": "2026-11-19T12:00:00Z",
            "evidence_curator": {
                "organization": "Analog Devices Edge AI Laboratory",
                "curator_name": "Verification Test Lead",
                "role": "Principal Verification Engineer",
            },
            "sensor_identity": {
                "manufacturer": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "PCB Piezotronics", "confidence_assessment": "VERIFIED_PRIMARY"},
                "model": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "352C33", "confidence_assessment": "VERIFIED_PRIMARY"},
                "sensor_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "PIEZOELECTRIC_IEPE", "confidence_assessment": "VERIFIED_PRIMARY"},
                "measurement_axis": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "RADIAL_VERTICAL", "confidence_assessment": "VERIFIED_PRIMARY"},
                "sensitivity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 100.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "sensitivity_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "mV/g", "confidence_assessment": "VERIFIED_PRIMARY"},
                "measurement_range_peak_g": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 50.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "full_scale_limit_physical": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 50.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "resolution_bits": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
                "frequency_response_bandwidth_3db_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": {"low_cutoff_hz": 0.5, "high_cutoff_hz": 10000.0}, "confidence_assessment": "VERIFIED_PRIMARY"},
                "calibration_certificate": {"available": False, "certificate_number": None, "calibrated_date": None, "traceability_authority": None},
                "mounting_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "MAGNETIC_BASE", "confidence_assessment": "VERIFIED_PRIMARY"},
                "mounting_location_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "Drive end bearing housing, 12 o'clock position", "confidence_assessment": "VERIFIED_PRIMARY"},
            },
            "acquisition_signal_interpretation": {
                "native_sampling_frequency_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 12000.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "sampling_clock_provenance": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "INTERNAL_HARDWARE_CRYSTAL", "confidence_assessment": "VERIFIED_PRIMARY"},
                "raw_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "VOLTS", "confidence_assessment": "VERIFIED_PRIMARY"},
                "calibrated_acceleration_unit": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "g", "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_bit_depth": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_input_range_volts": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_scaling_factor": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "analog_front_end_gain": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "duration_seconds": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "sample_count": 120000,
                "channel_identity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "DE_time", "confidence_assessment": "VERIFIED_PRIMARY"},
                "analog_filtering_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "Analog low-pass filter, 4000 Hz cutoff", "confidence_assessment": "VERIFIED_PRIMARY"},
                "anti_aliasing_verified": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "saturation_detection_limit_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 9.99, "confidence_assessment": "VERIFIED_PRIMARY"},
                "missing_data_sample_count": 0,
                "nonfinite_sample_count": 0,
            },
            "experiment_specimen_identity": {
                "physical_specimen_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "SPECIMEN_BEARING_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "specimen_distinctness_proven": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "machine_asset_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "TEST_RIG_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "equipment_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "Reliance Electric 2HP Induction Motor", "confidence_assessment": "VERIFIED_PRIMARY"},
                "rotational_speed_rpm": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "nominal_load_hp": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "load_percentage": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "operating_regime_classification": "STEADY_STATE_STATIC_LOAD",
                "measurement_session_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "SESSION_20261119_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "repeated_recording_dependency_flag": {
                    "status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref,
                    "resolved_value": False, "confidence_assessment": "VERIFIED_PRIMARY",
                },
                "related_experimental_recordings": [],
                "fault_condition": {
                    "fault_category": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "BASELINE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "fault_dimension_label": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "NONE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "fault_provenance_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "NATURAL_IN_SERVICE_FAILURE", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "ground_truth_label_authority": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "Independent Metrology Inspection Team", "confidence_assessment": "VERIFIED_PRIMARY"},
                },
            },
            "dataset_legal_provenance": {
                "dataset_name": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "Precision Bearing Test Rig Benchmark", "confidence_assessment": "VERIFIED_PRIMARY"},
                "publisher_entity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "Analog Devices Edge AI Laboratory", "confidence_assessment": "VERIFIED_PRIMARY"},
                "primary_documentation_uris": ["https://example.com/datasheets/benchmark.pdf"],
                "dataset_version": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "1.0.0", "confidence_assessment": "VERIFIED_PRIMARY"},
                "acquisition_date": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "2026-11-19", "confidence_assessment": "VERIFIED_PRIMARY"},
                "source_raw_byte_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "legal_usage_license": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "CREATIVE_COMMONS_ATTRIBUTION_CC_BY_4_0", "confidence_assessment": "VERIFIED_PRIMARY"},
                "affirmative_commercial_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "affirmative_research_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "redistribution_permitted": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "attribution_required": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "attribution_text": "Attribution required under CC-BY-4.0 terms.",
                "transformation_derivation_history": [],
            },
        }

        self.valid_decision = {
            "schema_version": "1.0.0",
            "decision_id": "dec_adm_fictional_test_01",
            "decided_at": "2026-11-20T00:00:00Z",
            "evidence_record_id": "ev_rec_fictional_test_bench_01",
            "target_source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "target_variable_name": "DE_time",
            "intended_use": "COMMERCIAL_R_AND_D",
            "evaluation_mode": "FICTIONAL_TEST_ONLY",
            "measurement_admission_status": "ADMITTED_FOR_MEASUREMENT",
            "diagnostic_reason": "DESIGN_ONLY_PROVISIONAL_PASS: Physical measurement criteria satisfied on fictional fixture.",
            "precedence_gate_reached": "ADMISSION",
            "operational_execution_authorized": False,
            "diagnostic_evaluation_eligibility": {
                "eligible": True,
                "status": "ELIGIBLE_FOR_DIAGNOSTIC_EVALUATION",
                "specimen_distinctness_proven": True,
                "ground_truth_fault_authority_verified": True,
                "precautionary_specimen_group": "precautionary_group_01",
                "reasons": ["Distinct physical specimens and verified labels confirmed on fictional fixture."],
            },
            "signal_integrity_summary": {
                "sample_count": 120000,
                "nonfinite_sample_count": 0,
                "rail_sample_count": 0,
                "longest_contiguous_rail_run": 0,
                "clipping_ratio": 0.0,
                "verification_status": "PASS",
            },
        }

    def test_valid_evidence_passes(self) -> None:
        """Valid fictional evidence record passes validation."""
        result = validate_calibration_measurement_evidence(self.valid_evidence)
        self.assertEqual(result["evidence_record_id"], "ev_rec_fictional_test_bench_01")

    def test_non_dict_evidence_rejected(self) -> None:
        """Non-dict evidence payloads must be rejected."""
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence("string_not_dict")
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence([self.valid_evidence])
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(True)

    def test_non_finite_numeric_rejected(self) -> None:
        """NaN or Infinity floats in evidence must be rejected."""
        bad_doc = copy.deepcopy(self.valid_evidence)
        bad_doc["sensor_identity"]["sensitivity"]["resolved_value"] = float("nan")
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(bad_doc)

        bad_doc2 = copy.deepcopy(self.valid_evidence)
        bad_doc2["sensor_identity"]["sensitivity"]["resolved_value"] = float("inf")
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(bad_doc2)

    def test_oversized_integer_rejected(self) -> None:
        """Python integers exceeding IEEE 754 binary64 must be rejected."""
        bad_doc = copy.deepcopy(self.valid_evidence)
        bad_doc["acquisition_signal_interpretation"]["sample_count"] = 10**400
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(bad_doc)

    def test_evidence_backed_booleans_reject_bare_primitives(self) -> None:
        """Bare boolean primitives in evidence-backed flag slots must be rejected."""
        bad_doc = copy.deepcopy(self.valid_evidence)
        bad_doc["experiment_specimen_identity"]["specimen_distinctness_proven"] = True
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(bad_doc)

    def test_zero_rpm_accepted_negative_rpm_rejected(self) -> None:
        """0.0 RPM must be valid; negative RPM must be rejected."""
        # 0.0 RPM is valid (setUp has 0.0)
        validate_calibration_measurement_evidence(self.valid_evidence)

        bad_doc = copy.deepcopy(self.valid_evidence)
        bad_doc["experiment_specimen_identity"]["rotational_speed_rpm"]["resolved_value"] = -150.0
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(bad_doc)

    def test_zero_sensitivity_rejected(self) -> None:
        """Zero transducer sensitivity must be rejected."""
        bad_doc = copy.deepcopy(self.valid_evidence)
        bad_doc["sensor_identity"]["sensitivity"]["resolved_value"] = 0.0
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(bad_doc)

    def test_contradictory_metadata_rejected(self) -> None:
        """DOCUMENTED_KNOWN leaking conflicting_references or applicability_justification is rejected."""
        bad_doc = copy.deepcopy(self.valid_evidence)
        bad_doc["sensor_identity"]["sensitivity"]["conflicting_references"] = []
        bad_doc["sensor_identity"]["sensitivity"]["applicability_justification"] = "Not applicable"
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(bad_doc)

    def test_additional_properties_rejected(self) -> None:
        """Unsupported additional properties must be rejected."""
        bad_doc = copy.deepcopy(self.valid_evidence)
        bad_doc["extraneous_top_level_field"] = "malicious_payload"
        with self.assertRaises(ContractValidationError):
            validate_calibration_measurement_evidence(bad_doc)

    def test_valid_decision_passes(self) -> None:
        """Valid measurement admission decision passes validation."""
        result = validate_measurement_admission_decision(self.valid_decision)
        self.assertEqual(result["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")

    def test_invalid_decision_status_rejected(self) -> None:
        """Admission decision with an invalid status is rejected."""
        bad_dec = copy.deepcopy(self.valid_decision)
        bad_dec["measurement_admission_status"] = "BLOCKED_INTEGRITY_VIOLATION"  # Obsolete status
        with self.assertRaises(ContractValidationError):
            validate_measurement_admission_decision(bad_dec)

    def test_invalid_precedence_gate_rejected(self) -> None:
        """Decision with an invalid precedence gate name is rejected."""
        bad_dec = copy.deepcopy(self.valid_decision)
        bad_dec["precedence_gate_reached"] = "UNKNOWN_GATE"
        with self.assertRaises(ContractValidationError):
            validate_measurement_admission_decision(bad_dec)

    def test_operational_execution_authorized_true_rejected(self) -> None:
        """operational_execution_authorized: True must be rejected by validator."""
        bad_dec = copy.deepcopy(self.valid_decision)
        bad_dec["operational_execution_authorized"] = True
        with self.assertRaises(ContractValidationError):
            validate_measurement_admission_decision(bad_dec)

    def test_operational_real_data_mode_rejected(self) -> None:
        """evaluation_mode: 'OPERATIONAL_REAL_DATA' must be rejected by validator."""
        bad_dec = copy.deepcopy(self.valid_decision)
        bad_dec["evaluation_mode"] = "OPERATIONAL_REAL_DATA"
        with self.assertRaises(ContractValidationError):
            validate_measurement_admission_decision(bad_dec)

    def test_operational_real_data_and_authorized_combination_rejected(self) -> None:
        """Supervisor reproducer: OPERATIONAL_REAL_DATA with authorized true is strictly rejected."""
        bad_dec = copy.deepcopy(self.valid_decision)
        bad_dec["evaluation_mode"] = "OPERATIONAL_REAL_DATA"
        bad_dec["operational_execution_authorized"] = True
        with self.assertRaises(ContractValidationError):
            validate_measurement_admission_decision(bad_dec)

    def test_signal_integrity_validator_valid_passes(self) -> None:
        """Valid SignalIntegrityEvidenceV1 payload passes validation."""
        sig = {
            "schema_version": "1.0.0",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "target_channel": "DE_time",
            "sample_count": 1000,
            "nonfinite_sample_count": 0,
            "full_scale_limit": 50.0,
            "full_scale_units": "g",
            "rail_sample_count": 0,
            "longest_contiguous_rail_run": 0,
            "clipping_ratio": 0.0,
            "data_quality_provenance": {
                "analyzer": "adi_common.admission.analyze_signal_integrity",
                "analysis_policy_version": "stage12.signal-integrity.v2",
                "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "target_channel": "DE_time",
                "sample_count": 1000,
                "rail_tolerance_fraction": 0.001,
            },
            "verification_status": "PASS",
        }
        res = validate_signal_integrity_evidence(sig)
        self.assertEqual(res["verification_status"], "PASS")

    def test_signal_integrity_validator_incomplete_rejected(self) -> None:
        """Incomplete payload like {'verification_status': 'PASS'} is rejected."""
        with self.assertRaises(ContractValidationError):
            validate_signal_integrity_evidence({"verification_status": "PASS"})

    def test_signal_integrity_validator_unknown_status_rejected(self) -> None:
        """Unknown verification status is rejected."""
        sig = {
            "schema_version": "1.0.0",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "target_channel": "DE_time",
            "sample_count": 1000,
            "nonfinite_sample_count": 0,
            "data_quality_provenance": {"analyzer": "test"},
            "verification_status": "UNKNOWN",
        }
        with self.assertRaises(ContractValidationError):
            validate_signal_integrity_evidence(sig)

    def test_signal_integrity_validator_inconsistent_clipping_ratio_rejected(self) -> None:
        """Inconsistent clipping ratio is rejected."""
        sig = {
            "schema_version": "1.0.0",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "target_channel": "DE_time",
            "sample_count": 1000,
            "nonfinite_sample_count": 0,
            "full_scale_limit": 50.0,
            "rail_sample_count": 10,
            "longest_contiguous_rail_run": 1,
            "clipping_ratio": 0.50,  # Expected 10/1000 = 0.01 != 0.50
            "data_quality_provenance": {"analyzer": "test"},
            "verification_status": "PASS",
        }
        with self.assertRaises(ContractValidationError):
            validate_signal_integrity_evidence(sig)

    def test_non_mutation(self) -> None:
        """Validators must not mutate the input dictionary."""
        ev_orig = copy.deepcopy(self.valid_evidence)
        validate_calibration_measurement_evidence(self.valid_evidence)
        self.assertEqual(self.valid_evidence, ev_orig)

        dec_orig = copy.deepcopy(self.valid_decision)
        validate_measurement_admission_decision(self.valid_decision)
        self.assertEqual(self.valid_decision, dec_orig)


if __name__ == "__main__":
    unittest.main()
