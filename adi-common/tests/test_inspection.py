"""Focused unit tests for ExploratorySourceInspectionV1 contract and validator."""

import copy
import sys
import unittest

from adi_common import (
    ContractValidationError,
    load_schema,
    validate_exploratory_source_inspection,
    validate_vibration_recording,
)


class TestExploratorySourceInspection(unittest.TestCase):
    """Test suite for ExploratorySourceInspectionV1 validation."""

    def setUp(self):
        self.valid_success_unknown = {
            "schema_version": "1.0.0",
            "inspection_id": "insp_cwru_fictional_97_de",
            "inspected_at": "2026-11-16T13:00:00Z",
            "source_identity": {
                "source_id": "cwru_bearing_data_center",
                "source_locator": "https://engineering.case.edu/sites/default/files/fictional_97.mat",
                "source_filename": "fictional_97.mat",
                "raw_byte_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "raw_byte_size": 1950000,
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
                    "max": 1.795432,
                },
            },
            "source_documentation": {
                "sensor_physical_location": {
                    "value": "drive_end_housing_12_oclock",
                    "evidence_reference": "CWRU Apparatus & Procedures HTML page",
                },
                "test_rig_description": {
                    "value": "2hp_reliance_electric_motor_test_stand",
                    "evidence_reference": "CWRU Apparatus & Procedures HTML page",
                },
                "nominal_motor_load_hp": {
                    "value": 0.0,
                    "evidence_reference": "CWRU Normal Baseline Data HTML table",
                },
                "nominal_motor_speed_rpm": {
                    "value": 1797.0,
                    "evidence_reference": "CWRU Normal Baseline Data HTML table",
                },
                "recording_condition_label": {
                    "value": "normal_baseline",
                    "evidence_reference": "CWRU Normal Baseline Data HTML table",
                    "scope": "WHOLE_RECORDING_ONLY",
                },
            },
            "metadata_status_ledger": {
                "physical_acceleration_unit": {
                    "status": "UNKNOWN_UNSTATED",
                    "resolved_value": None,
                    "evidence_reference": None,
                    "evidence_gap_statement": "Transducer sensitivity omitted.",
                },
                "native_sampling_rate_hz": {
                    "status": "UNKNOWN_UNSTATED",
                    "resolved_value": None,
                    "evidence_reference": None,
                    "evidence_gap_statement": "Normal baseline table omits sampling rate.",
                },
                "acquisition_full_scale_limit": {
                    "status": "UNKNOWN_UNSTATED",
                    "resolved_value": None,
                    "evidence_reference": None,
                    "evidence_gap_statement": "Hardware DAQ input range omitted.",
                },
                "physical_specimen_identity": {
                    "status": "UNKNOWN_UNPROVEN",
                    "resolved_value": None,
                    "evidence_reference": None,
                    "evidence_gap_statement": "Specimen identity unproven across runs.",
                },
                "legal_usage_grant": {
                    "status": "UNKNOWN_UNSTATED",
                    "resolved_value": None,
                    "evidence_reference": None,
                    "evidence_gap_statement": "Copyright notice with no explicit grant.",
                },
            },
            "local_annotations": {
                "precautionary_specimen_group": "precautionary_group_cwru_normal",
                "candidate_partition_role": "held_out_load_test",
                "provenance_classification": "recorded",
            },
            "inspection_findings": {
                "structural_status": "STRUCTURAL_SUCCESS",
                "measurement_readiness": "BLOCKED_UNKNOWN_METADATA",
                "detector_admission_eligible": False,
                "diagnostic_notes": ["Container successfully parsed as MATLAB v5."],
            },
        }

    def test_schema_loading_from_resources(self):
        """Verify schema is accessible through package resources."""
        schema = load_schema("exploratory_source_inspection.schema.v1.json")
        self.assertEqual(schema.get("title"), "ExploratorySourceInspectionV1")
        self.assertEqual(schema.get("properties", {}).get("schema_version", {}).get("enum"), ["1.0.0"])

    def test_valid_success_unknown_metadata(self):
        """Test valid structural success report with unknown metadata."""
        result = validate_exploratory_source_inspection(self.valid_success_unknown)
        self.assertIsNone(result)

    def test_valid_documented_known_metadata(self):
        """Test valid report with fully documented known metadata."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["metadata_status_ledger"]["physical_acceleration_unit"] = {
            "status": "DOCUMENTED_KNOWN",
            "resolved_value": "m/s^2",
            "evidence_reference": {
                "source_document": "Manufacturer Calibration Sheet",
                "passage_or_table": "Table 2",
                "access_provenance": "Direct archive inspection",
            },
            "evidence_gap_statement": None,
        }
        rep["metadata_status_ledger"]["native_sampling_rate_hz"] = {
            "status": "DOCUMENTED_KNOWN",
            "resolved_value": 48000.0,
            "evidence_reference": {
                "source_document": "DAQ Hardware Log",
                "passage_or_table": "Header Block",
                "access_provenance": "Direct log inspection",
            },
            "evidence_gap_statement": None,
        }
        rep["metadata_status_ledger"]["acquisition_full_scale_limit"] = {
            "status": "DOCUMENTED_KNOWN",
            "resolved_value": 50.0,
            "evidence_reference": {
                "source_document": "DAQ Manual",
                "passage_or_table": "Section 4.1",
                "access_provenance": "Manual inspection",
            },
            "evidence_gap_statement": None,
        }
        rep["metadata_status_ledger"]["physical_specimen_identity"] = {
            "status": "DOCUMENTED_KNOWN",
            "resolved_value": "SKF-6205-SN-9981",
            "evidence_reference": {
                "source_document": "Test Stand Specimen Log",
                "passage_or_table": "Entry 41",
                "access_provenance": "Laboratory logbook",
            },
            "evidence_gap_statement": None,
        }
        rep["metadata_status_ledger"]["legal_usage_grant"] = {
            "status": "DOCUMENTED_KNOWN",
            "resolved_value": "CC-BY-4.0",
            "evidence_reference": {
                "source_document": "Dataset Portal License Page",
                "passage_or_table": "License Grant",
                "access_provenance": "Direct web inspection",
            },
            "evidence_gap_statement": None,
        }
        self.assertIsNone(validate_exploratory_source_inspection(rep))

    def test_all_status_and_readiness_mappings(self):
        """Verify all exact status/readiness pairs validate, and mismatches fail."""
        status_specs = [
            ("STRUCTURAL_SUCCESS", "BLOCKED_UNKNOWN_METADATA", "MATLAB_V5", "float64", [10, 1], 10, 0, {"min": -1.0, "max": 1.0}),
            ("NONFINITE_VALUES_DETECTED", "BLOCKED_NONFINITE_VALUES", "MATLAB_V5", "float32", [1, 5], 5, 2, {"min": 0.0, "max": 2.0}),
            ("TARGET_VARIABLE_NOT_FOUND", "BLOCKED_TARGET_NOT_FOUND", "MATLAB_V5", None, None, None, None, None),
            ("MALFORMED_CONTAINER", "BLOCKED_CORRUPTED_FILE", "UNRECOGNIZED", None, None, None, None, None),
            ("CORRUPTED_RAW_BYTES", "BLOCKED_CORRUPTED_FILE", "UNRECOGNIZED", None, None, None, None, None),
            ("UNSUPPORTED_STRUCTURE", "BLOCKED_UNSUPPORTED_FORMAT", "MATLAB_V7_3_HDF5", None, None, None, None, None),
            ("RESOURCE_LIMIT_EXCEEDED", "BLOCKED_RESOURCE_LIMIT", "MATLAB_V5", None, None, None, None, None),
        ]

        for st, rd, fmt, dtype, shape, count, nonfinite, extrema in status_specs:
            with self.subTest(status=st, readiness=rd):
                rep = copy.deepcopy(self.valid_success_unknown)
                rep["inspection_findings"]["structural_status"] = st
                rep["inspection_findings"]["measurement_readiness"] = rd
                rep["container_facts"]["container_format"] = fmt
                rep["container_facts"]["numeric_dtype"] = dtype
                rep["container_facts"]["numeric_shape"] = shape
                rep["container_facts"]["sample_count"] = count
                rep["container_facts"]["nonfinite_sample_count"] = nonfinite
                rep["container_facts"]["observed_raw_extrema"] = extrema

                if st == "TARGET_VARIABLE_NOT_FOUND":
                    rep["container_facts"]["target_variable"] = "MISSING_VAR"
                    rep["container_facts"]["available_variables"] = ["X097_DE_time"]
                elif st in ("MALFORMED_CONTAINER", "CORRUPTED_RAW_BYTES"):
                    rep["container_facts"]["available_variables"] = []
                    rep["container_facts"]["target_variable"] = None

                self.assertIsNone(validate_exploratory_source_inspection(rep))

                # Test readiness mismatch
                rep_bad = copy.deepcopy(rep)
                rep_bad["inspection_findings"]["measurement_readiness"] = "BLOCKED_RESOURCE_LIMIT" if rd != "BLOCKED_RESOURCE_LIMIT" else "BLOCKED_CORRUPTED_FILE"
                with self.assertRaises(ContractValidationError):
                    validate_exploratory_source_inspection(rep_bad)

    def test_unknown_with_value_rejected(self):
        """Reject unknown status when resolved_value is provided."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["metadata_status_ledger"]["native_sampling_rate_hz"]["resolved_value"] = 12000.0
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_known_without_evidence_rejected(self):
        """Reject documented known status when evidence_reference is missing or null."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["metadata_status_ledger"]["native_sampling_rate_hz"] = {
            "status": "DOCUMENTED_KNOWN",
            "resolved_value": 12000.0,
            "evidence_reference": None,
            "evidence_gap_statement": None,
        }
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_known_with_null_value_rejected(self):
        """Reject documented known status when resolved_value is null."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["metadata_status_ledger"]["physical_acceleration_unit"] = {
            "status": "DOCUMENTED_KNOWN",
            "resolved_value": None,
            "evidence_reference": {
                "source_document": "Doc",
                "passage_or_table": "P1",
                "access_provenance": "HTML",
            },
            "evidence_gap_statement": None,
        }
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_vector_count_mismatch_rejected(self):
        """Reject report where sample_count does not equal shape product."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["container_facts"]["sample_count"] = 1  # shape is [243938, 1]
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_integers_as_dtype_rejected_for_success(self):
        """Reject integer dtypes (e.g. int16, int32) for successful records."""
        for bad_dtype in ("int16", "int32", "uint8", "float16"):
            with self.subTest(dtype=bad_dtype):
                rep = copy.deepcopy(self.valid_success_unknown)
                rep["container_facts"]["numeric_dtype"] = bad_dtype
                with self.assertRaises(ContractValidationError):
                    validate_exploratory_source_inspection(rep)

    def test_matrix_shape_rejected(self):
        """Reject 2D matrices where both dimensions are greater than 1."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["container_facts"]["numeric_shape"] = [100, 2]
        rep["container_facts"]["sample_count"] = 200
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_one_dimensional_shape_rejected(self):
        """Reject 1D shape arrays; must be 2D row/column vector."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["container_facts"]["numeric_shape"] = [243938]
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_empty_shape_rejected(self):
        """Reject shape with zero dimension."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["container_facts"]["numeric_shape"] = [0, 1]
        rep["container_facts"]["sample_count"] = 0
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_oversized_shape_rejected(self):
        """Reject sample_count exceeding 10,000,000."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["container_facts"]["numeric_shape"] = [10_000_001, 1]
        rep["container_facts"]["sample_count"] = 10_000_001
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_nonfinite_count_extrema_consistency(self):
        """Test nonfinite count and extrema rules including all-nonfinite."""
        # 1. Structural success with positive nonfinite_sample_count must fail
        rep1 = copy.deepcopy(self.valid_success_unknown)
        rep1["container_facts"]["nonfinite_sample_count"] = 5
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep1)

        # 2. Nonfinite status with zero nonfinite_sample_count must fail
        rep2 = copy.deepcopy(self.valid_success_unknown)
        rep2["inspection_findings"]["structural_status"] = "NONFINITE_VALUES_DETECTED"
        rep2["inspection_findings"]["measurement_readiness"] = "BLOCKED_NONFINITE_VALUES"
        rep2["container_facts"]["nonfinite_sample_count"] = 0
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep2)

        # 3. All nonfinite samples with null extrema must pass
        rep3 = copy.deepcopy(self.valid_success_unknown)
        rep3["inspection_findings"]["structural_status"] = "NONFINITE_VALUES_DETECTED"
        rep3["inspection_findings"]["measurement_readiness"] = "BLOCKED_NONFINITE_VALUES"
        rep3["container_facts"]["numeric_shape"] = [10, 1]
        rep3["container_facts"]["sample_count"] = 10
        rep3["container_facts"]["nonfinite_sample_count"] = 10
        rep3["container_facts"]["observed_raw_extrema"] = None
        self.assertIsNone(validate_exploratory_source_inspection(rep3))

        # 4. All nonfinite samples with non-null extrema must fail
        rep4 = copy.deepcopy(rep3)
        rep4["container_facts"]["observed_raw_extrema"] = {"min": 0.0, "max": 1.0}
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep4)

        # 5. Inverted extrema (min > max) must fail
        rep5 = copy.deepcopy(self.valid_success_unknown)
        rep5["container_facts"]["observed_raw_extrema"] = {"min": 5.0, "max": 2.0}
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep5)

    def test_target_variable_not_found_rules(self):
        """Test rules for TARGET_VARIABLE_NOT_FOUND status."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["inspection_findings"]["structural_status"] = "TARGET_VARIABLE_NOT_FOUND"
        rep["inspection_findings"]["measurement_readiness"] = "BLOCKED_TARGET_NOT_FOUND"
        rep["container_facts"]["numeric_dtype"] = None
        rep["container_facts"]["numeric_shape"] = None
        rep["container_facts"]["sample_count"] = None
        rep["container_facts"]["nonfinite_sample_count"] = None
        rep["container_facts"]["observed_raw_extrema"] = None
        rep["container_facts"]["target_variable"] = "X999_DE_time"
        rep["container_facts"]["available_variables"] = ["X097_DE_time", "X097_FE_time"]

        self.assertIsNone(validate_exploratory_source_inspection(rep))

        # Target variable present in available_variables must fail
        rep_bad = copy.deepcopy(rep)
        rep_bad["container_facts"]["available_variables"] = ["X999_DE_time"]
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep_bad)

    def test_bad_digest_newline_rejected(self):
        """Reject raw_byte_sha256 containing newline."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["source_identity"]["raw_byte_sha256"] = "a" * 64 + "\n"
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_invalid_datetime_rejected(self):
        """Reject invalid date-time strings in inspected_at."""
        for bad_dt in ("invalid-date", "2026-02-31T00:00:00Z", "2026-11-16", "2026-11-16T13:00:00"):
            with self.subTest(dt=bad_dt):
                rep = copy.deepcopy(self.valid_success_unknown)
                rep["inspected_at"] = bad_dt
                with self.assertRaises(ContractValidationError):
                    validate_exploratory_source_inspection(rep)

    def test_booleans_in_numeric_positions_rejected(self):
        """Reject boolean True/False in numeric fields."""
        # boolean sample_count
        rep1 = copy.deepcopy(self.valid_success_unknown)
        rep1["container_facts"]["sample_count"] = True
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep1)

        # boolean shape dimension
        rep2 = copy.deepcopy(self.valid_success_unknown)
        rep2["container_facts"]["numeric_shape"] = [True, 1]
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep2)

        # boolean rate in known metadata
        rep3 = copy.deepcopy(self.valid_success_unknown)
        rep3["metadata_status_ledger"]["native_sampling_rate_hz"] = {
            "status": "DOCUMENTED_KNOWN",
            "resolved_value": True,
            "evidence_reference": {"source_document": "D", "passage_or_table": "P", "access_provenance": "A"},
            "evidence_gap_statement": None,
        }
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep3)

    def test_huge_and_nonfinite_numerics_rejected(self):
        """Reject values exceeding binary64 domain or nonfinite floats."""
        # 10**400 in raw_byte_size
        rep1 = copy.deepcopy(self.valid_success_unknown)
        rep1["source_identity"]["raw_byte_size"] = 10**400
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep1)

        # float inf in extrema min
        rep2 = copy.deepcopy(self.valid_success_unknown)
        rep2["container_facts"]["observed_raw_extrema"]["min"] = float("-inf")
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep2)

    def test_extra_fields_rejected(self):
        """Reject payloads containing unexpected extra fields."""
        # Top-level extra field
        rep1 = copy.deepcopy(self.valid_success_unknown)
        rep1["unexpected_top_field"] = "bad"
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep1)

        # Container facts extra field
        rep2 = copy.deepcopy(self.valid_success_unknown)
        rep2["container_facts"]["unexpected_sub_field"] = 123
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep2)

    def test_detector_admission_eligible_must_be_false(self):
        """Reject report if detector_admission_eligible is True."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["inspection_findings"]["detector_admission_eligible"] = True
        with self.assertRaises(ContractValidationError):
            validate_exploratory_source_inspection(rep)

    def test_diagnostic_outcome_fields_rejected(self):
        """Reject report containing diagnostic decision fields (outcome, score, etc.)."""
        for diag_key in ("outcome", "score", "threshold", "quality_status"):
            with self.subTest(diag_key=diag_key):
                rep = copy.deepcopy(self.valid_success_unknown)
                rep[diag_key] = "NORMAL" if diag_key == "outcome" else 0.5
                with self.assertRaises(ContractValidationError):
                    validate_exploratory_source_inspection(rep)

    def test_input_nonmutation(self):
        """Verify validate_exploratory_source_inspection does not mutate caller input."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep_snapshot = copy.deepcopy(rep)
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep, rep_snapshot)

    def test_vibration_recording_validator_rejects_inspection_report(self):
        """Verify that ordinary VibrationRecordingV1 validator rejects an inspection report."""
        with self.assertRaises(ContractValidationError):
            validate_vibration_recording(self.valid_success_unknown)


    def test_oversized_integer_raw_byte_size_renders_cleanly(self):
        """TC-INSP-25: Oversized integer (10**5000) exceeding Python decimal conversion limit raises ContractValidationError, and message renders cleanly."""
        for large_val in (10**5000, -(10**5000)):
            rep = copy.deepcopy(self.valid_success_unknown)
            rep["source_identity"]["raw_byte_size"] = large_val
            with self.assertRaises(ContractValidationError) as ctx:
                validate_exploratory_source_inspection(rep)
            # Ensure not a leaked standard ValueError or TypeError
            self.assertIs(type(ctx.exception), ContractValidationError)
            rendered_msg = str(ctx.exception)
            self.assertTrue(len(rendered_msg) > 0)
            self.assertIn("source_identity.raw_byte_size", rendered_msg)
            self.assertIn("binary64", rendered_msg)

    def test_valid_maximum_range_boundary_preserved(self):
        """TC-INSP-26: Exact binary64 maximum integer validates, while max + 1 raises ContractValidationError."""
        max_int = int(sys.float_info.max)
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["source_identity"]["raw_byte_size"] = max_int
        # Must validate successfully
        self.assertIsNone(validate_exploratory_source_inspection(rep))

        # max + 1 must be rejected cleanly
        rep["source_identity"]["raw_byte_size"] = max_int + 1
        with self.assertRaises(ContractValidationError) as ctx:
            validate_exploratory_source_inspection(rep)
        self.assertIs(type(ctx.exception), ContractValidationError)
        rendered_msg = str(ctx.exception)
        self.assertTrue(len(rendered_msg) > 0)
        self.assertIn("source_identity.raw_byte_size", rendered_msg)

    def test_mixed_top_level_non_string_keys_rejected(self):
        """TC-INSP-27: Mixed top-level keys 1 and 'extra' raise ContractValidationError, not TypeError."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep[1] = "bad_int_key"
        rep["extra"] = "extra_field"
        with self.assertRaises(ContractValidationError) as ctx:
            validate_exploratory_source_inspection(rep)
        self.assertIs(type(ctx.exception), ContractValidationError)
        rendered_msg = str(ctx.exception)
        self.assertTrue(len(rendered_msg) > 0)
        self.assertIn("Object key", rendered_msg)
        self.assertIn("int", rendered_msg)

    def test_nested_non_string_key_rejected(self):
        """TC-INSP-28: Nested non-string dictionary keys raise ContractValidationError with safe message."""
        rep = copy.deepcopy(self.valid_success_unknown)
        rep["local_annotations"][42] = "nested_int_key"
        with self.assertRaises(ContractValidationError) as ctx:
            validate_exploratory_source_inspection(rep)
        self.assertIs(type(ctx.exception), ContractValidationError)
        rendered_msg = str(ctx.exception)
        self.assertTrue(len(rendered_msg) > 0)
        self.assertIn("local_annotations", rendered_msg)
        self.assertIn("int", rendered_msg)

    def test_representative_non_json_values_rejected(self):
        """TC-INSP-29: Non-JSON types (set, tuple, custom object, bytes) raise ContractValidationError."""
        cases = [
            ("set", {1, 2}, lambda d: d["local_annotations"].__setitem__("precautionary_specimen_group", {1, 2})),
            ("tuple", (10, 20), lambda d: d["container_facts"].__setitem__("numeric_shape", (10, 20))),
            ("object", object(), lambda d: d["source_documentation"].__setitem__("nominal_motor_load_hp", object())),
            ("bytes", b"binary_sha", lambda d: d["source_identity"].__setitem__("raw_byte_sha256", b"binary_sha")),
        ]
        for type_label, bad_val, mutator in cases:
            with self.subTest(type_label=type_label):
                rep = copy.deepcopy(self.valid_success_unknown)
                mutator(rep)
                with self.assertRaises(ContractValidationError) as ctx:
                    validate_exploratory_source_inspection(rep)
                self.assertIs(type(ctx.exception), ContractValidationError)
                rendered_msg = str(ctx.exception)
                self.assertTrue(len(rendered_msg) > 0)
                self.assertIn("Unsupported data type", rendered_msg)


if __name__ == "__main__":
    unittest.main()
