"""Comprehensive unit test suite for evaluate_measurement_admission."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest

from adi_common.admission import (
    EvidenceRegistry,
    OperationalAdmissionDisabledError,
    PRECEDENCE_GATES,
    VALID_ADMISSION_STATUSES,
    analyze_signal_integrity,
    evaluate_measurement_admission,
)
from adi_common.validators import ContractValidationError


class TestMeasurementAdmissionEngine(unittest.TestCase):
    """Test suite for evaluate_measurement_admission adhering to Stage 11 & Stage 12 specifications."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source_path = Path(self.temp_dir.name) / "valid_source.json"
        artifact = {
            "schema_version": "1.0.0",
            "artifact_format": "ADI_FICTIONAL_SOURCE_V1",
            "units": "g",
            "full_scale_limit": 50.0,
            "channels": {"DE_time": [1.0] * 120000},
        }
        source_bytes = json.dumps(
            artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.source_path.write_bytes(source_bytes)
        self.source_digest = hashlib.sha256(source_bytes).hexdigest()
        self.doc_ref = {
            "document_title": "PCB 352C33 Spec Sheet",
            "locator_uri": "https://example.com/datasheets/352C33.pdf",
            "document_sha256": "05d1dc0142929ada23a90f34d0688c69472bb41c6863bde9d832112ed1576434",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Section 2.1, Table 1",
            "extracted_text_verbatim": "Sensitivity: 100 mV/g",
        }
        self.rig_ref = {
            "document_title": "Laboratory Test Rig Documentation",
            "locator_uri": "https://example.com/docs/test_rig_specs.pdf",
            "document_sha256": "7929d21c2357a5ee8dbafcbcab13431cbc32b670b0343dedecd47c10f46d39ef",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Section 1.2",
            "extracted_text_verbatim": "2HP motor test bench with 12 kHz crystal clock.",
        }
        self.teardown_ref = {
            "document_title": "Test Bench Teardown Logbook",
            "locator_uri": "https://example.com/logs/teardown_log_2026.pdf",
            "document_sha256": "65f9f8f982e5cbfccc25722376e4933975ee932432ba568eb540430d9a4feec4",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Logbook #4, page 12",
            "extracted_text_verbatim": "Bearing specimen verified distinct and serialized.",
        }
        self.license_ref = {
            "document_title": "Precision Bearing Test Rig Benchmark",
            "locator_uri": "https://example.com/datasheets/benchmark.pdf",
            "document_sha256": "c06f0b4fce8b3d19420526f3f4aeaa744f6a1cf60fda5478345b0307caf24458",
            "retrieval_date": "2026-11-19",
            "passage_citation": "License Section",
            "extracted_text_verbatim": "Creative Commons Attribution CC-BY-4.0 terms.",
        }
        self.label_ref = {
            "document_title": "Metrology Inspection Certificate",
            "locator_uri": "https://example.com/certs/metrology_01.pdf",
            "document_sha256": "883289be69ae546a5aaf08988f8524ca74ca9bf78d3f46ddad5d48010cb76610",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Certificate #994",
            "extracted_text_verbatim": "Fault label independently metrologized.",
        }

        # Complete fictional evidence record
        self.evidence = {
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
                "native_sampling_frequency_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": 12000.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "sampling_clock_provenance": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": "INTERNAL_HARDWARE_CRYSTAL", "confidence_assessment": "VERIFIED_PRIMARY"},
                "raw_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "VOLTS", "confidence_assessment": "VERIFIED_PRIMARY"},
                "calibrated_acceleration_unit": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "g", "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_bit_depth": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_input_range_volts": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_scaling_factor": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "analog_front_end_gain": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "duration_seconds": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "sample_count": 120000,
                "channel_identity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": "DE_time", "confidence_assessment": "VERIFIED_PRIMARY"},
                "analog_filtering_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": "Analog low-pass filter, 4000 Hz cutoff", "confidence_assessment": "VERIFIED_PRIMARY"},
                "anti_aliasing_verified": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "saturation_detection_limit_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.doc_ref, "resolved_value": 9.99, "confidence_assessment": "VERIFIED_PRIMARY"},
                "missing_data_sample_count": 0,
                "nonfinite_sample_count": 0,
            },
            "experiment_specimen_identity": {
                "physical_specimen_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.teardown_ref, "resolved_value": "SPECIMEN_BEARING_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "specimen_distinctness_proven": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.teardown_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "machine_asset_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": "TEST_RIG_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "equipment_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": "Reliance Electric 2HP Induction Motor", "confidence_assessment": "VERIFIED_PRIMARY"},
                "rotational_speed_rpm": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "nominal_load_hp": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "load_percentage": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "operating_regime_classification": "STEADY_STATE_STATIC_LOAD",
                "measurement_session_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref, "resolved_value": "SESSION_20261119_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "repeated_recording_dependency_flag": {
                    "status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.rig_ref,
                    "resolved_value": False, "confidence_assessment": "VERIFIED_PRIMARY",
                },
                "related_experimental_recordings": [],
                "fault_condition": {
                    "fault_category": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.label_ref, "resolved_value": "BASELINE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "fault_dimension_label": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.label_ref, "resolved_value": "NONE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "fault_provenance_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.label_ref, "resolved_value": "NATURAL_IN_SERVICE_FAILURE", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "ground_truth_label_authority": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.label_ref, "resolved_value": "Independent Metrology Inspection Team", "confidence_assessment": "VERIFIED_PRIMARY"},
                },
            },
            "dataset_legal_provenance": {
                "dataset_name": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": "Precision Bearing Test Rig Benchmark", "confidence_assessment": "VERIFIED_PRIMARY"},
                "publisher_entity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": "Analog Devices Edge AI Laboratory", "confidence_assessment": "VERIFIED_PRIMARY"},
                "primary_documentation_uris": ["https://example.com/datasheets/benchmark.pdf"],
                "dataset_version": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": "1.0.0", "confidence_assessment": "VERIFIED_PRIMARY"},
                "acquisition_date": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": "2026-11-19", "confidence_assessment": "VERIFIED_PRIMARY"},
                "source_raw_byte_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "legal_usage_license": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": "CREATIVE_COMMONS_ATTRIBUTION_CC_BY_4_0", "confidence_assessment": "VERIFIED_PRIMARY"},
                "affirmative_commercial_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "affirmative_research_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "redistribution_permitted": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "attribution_required": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": self.license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "attribution_text": "Attribution required under CC-BY-4.0 terms.",
                "transformation_derivation_history": [],
            },
        }

        # Authentic Stage 10 inspection report
        self.stage10_report = {
            "schema_version": "1.0.0",
            "inspection_id": "insp_fictional_test_bench_01_de",
            "inspected_at": "2026-11-19T12:00:00Z",
            "source_identity": {
                "source_id": "adi_edge_fictional_test_bench",
                "source_locator": "https://example.com/data/fictional_bench_01.mat",
                "source_filename": "fictional_bench_01.mat",
                "raw_byte_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                "raw_byte_size": 1950000,
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
                    "max": 1.795432,
                },
            },
            "source_documentation": {
                "sensor_physical_location": {"value": "drive_end_housing_12_oclock", "evidence_reference": "Laboratory Test Rig Documentation"},
                "test_rig_description": {"value": "2hp_reliance_electric_motor_test_stand", "evidence_reference": "Laboratory Test Rig Documentation"},
                "nominal_motor_load_hp": {"value": 0.0, "evidence_reference": "Laboratory Test Rig Documentation"},
                "nominal_motor_speed_rpm": {"value": 1797.0, "evidence_reference": "Laboratory Test Rig Documentation"},
                "recording_condition_label": {"value": "normal_baseline", "evidence_reference": "Laboratory Test Rig Documentation", "scope": "WHOLE_RECORDING_ONLY"},
            },
            "metadata_status_ledger": {
                "physical_acceleration_unit": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "Transducer sensitivity omitted in raw MAT container."},
                "native_sampling_rate_hz": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "DAQ sample clock omitted in raw MAT container."},
                "acquisition_full_scale_limit": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "Hardware full scale omitted in raw MAT container."},
                "physical_specimen_identity": {"status": "UNKNOWN_UNPROVEN", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "Specimen swap logbook omitted in raw MAT container."},
                "legal_usage_grant": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "License omitted in raw MAT container."},
            },
            "local_annotations": {
                "precautionary_specimen_group": "precautionary_group_fictional_01",
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

        self.evidence["target_source_sha256"] = self.source_digest
        self.evidence["dataset_legal_provenance"]["source_raw_byte_sha256"] = self.source_digest
        self.stage10_report["source_identity"]["raw_byte_sha256"] = self.source_digest
        self.fictional_registry = EvidenceRegistry.create_fictional_test_registry()
        self.signal_integrity_pass = {
            "schema_version": "1.0.0",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "target_channel": "DE_time",
            "sample_count": 120000,
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
                "sample_count": 120000,
                "rail_tolerance_fraction": 0.001,
                "analyzed_at": "2026-11-20T00:00:00Z",
            },
            "verification_status": "PASS",
        }
        self.signal_integrity_pass = analyze_signal_integrity(
            [1.0] * 120000,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "DE_time",
            50.0,
            "g",
        )
        self.signal_integrity_pass["source_sha256"] = self.source_digest
        self.signal_integrity_pass["data_quality_provenance"]["source_sha256"] = self.source_digest
        self.partition_assignments = {
            "recording_partitions": {
                "fictional_bench_01.mat": "TRAIN",
                "fictional_bench_02.mat": "TEST",
            },
            "recording_specimens": {
                "fictional_bench_01.mat": "SPECIMEN_BEARING_01",
                "fictional_bench_02.mat": "SPECIMEN_BEARING_02",
            },
            "target_recording_id": "fictional_bench_01.mat",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # Scenario 1: Fully documented fictional evidence -> Hypothetical measurement admission
    def test_fully_documented_fictional_evidence_admitted(self) -> None:
        """Fully documented fictional evidence passes all gates in FICTIONAL_TEST_ONLY mode."""
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=self.partition_assignments,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertEqual(res["evaluation_mode"], "FICTIONAL_TEST_ONLY")
        self.assertFalse(res["operational_execution_authorized"])
        self.assertEqual(res["precedence_gate_reached"], "ADMISSION")
        self.assertTrue(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertIn("DESIGN_ONLY_PROVISIONAL_PASS", res["diagnostic_reason"])

    # Scenario 2: Invalid Stage 10 report -> Rejected before evaluation
    def test_invalid_stage10_report_rejected(self) -> None:
        """Invalid Stage 10 report raises ContractValidationError before evaluation."""
        bad_s10 = copy.deepcopy(self.stage10_report)
        del bad_s10["source_identity"]  # Missing required field
        with self.assertRaises(ContractValidationError):
            evaluate_measurement_admission(
                evidence=self.evidence,
                stage10_report=bad_s10,
                intended_use="COMMERCIAL_R_AND_D",
                evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            )

    # Scenario 3: Source SHA-256 mismatch (target vs Stage 10)
    def test_source_sha256_mismatch_stage10_blocked_provenance(self) -> None:
        """Disagreement between target_source_sha256 and Stage 10 hash fails with BLOCKED_PROVENANCE."""
        bad_s10 = copy.deepcopy(self.stage10_report)
        bad_s10["source_identity"]["raw_byte_sha256"] = "11" * 32
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=bad_s10,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")
        self.assertEqual(res["precedence_gate_reached"], "SOURCE_PROVENANCE")
        self.assertIn("STAGE10_DIGEST_MISMATCH", res["diagnostic_reason"])

    # Scenario 3b: Source SHA-256 mismatch (target vs provenance)
    def test_source_sha256_mismatch_provenance_blocked_provenance(self) -> None:
        """Disagreement between target_source_sha256 and legal provenance hash fails with BLOCKED_PROVENANCE."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["dataset_legal_provenance"]["source_raw_byte_sha256"] = "22" * 32
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")
        self.assertEqual(res["precedence_gate_reached"], "SOURCE_PROVENANCE")
        self.assertIn("PROVENANCE_HASH_MISMATCH", res["diagnostic_reason"])

    # Scenario 4: Variable-name mismatch
    def test_variable_name_mismatch_blocked_provenance(self) -> None:
        """Disagreement on target variable name fails with BLOCKED_PROVENANCE."""
        bad_s10 = copy.deepcopy(self.stage10_report)
        bad_s10["container_facts"]["target_variable"] = "FE_time"
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=bad_s10,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")
        self.assertEqual(res["precedence_gate_reached"], "SOURCE_PROVENANCE")
        self.assertIn("VARIABLE_TARGET_MISMATCH", res["diagnostic_reason"])

    # Scenario 5: Sample-count mismatch
    def test_sample_count_mismatch_blocked_provenance(self) -> None:
        """Disagreement on sample count fails with BLOCKED_PROVENANCE."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["acquisition_signal_interpretation"]["sample_count"] = 99999
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")
        self.assertEqual(res["precedence_gate_reached"], "SOURCE_PROVENANCE")
        self.assertIn("SAMPLE_COUNT_MISMATCH", res["diagnostic_reason"])

    # Scenario 6: Missing sampling rate
    def test_missing_sampling_rate_blocked_unknown_metadata(self) -> None:
        """Missing or unstated native sampling rate fails with BLOCKED_UNKNOWN_METADATA."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["acquisition_signal_interpretation"]["native_sampling_frequency_hz"] = {
            "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
            "resolved_value": None, "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "DAQ rate unstated.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_UNKNOWN_METADATA")
        self.assertEqual(res["precedence_gate_reached"], "PHYSICAL_METADATA")
        self.assertIn("SAMPLING_RATE_UNVERIFIED", res["diagnostic_reason"])

    # Scenario 7: Unknown physical units
    def test_unknown_physical_units_blocked_unknown_metadata(self) -> None:
        """Missing or unstated physical acceleration unit fails with BLOCKED_UNKNOWN_METADATA."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["acquisition_signal_interpretation"]["calibrated_acceleration_unit"] = {
            "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
            "resolved_value": None, "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "Unit unstated.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_UNKNOWN_METADATA")
        self.assertEqual(res["precedence_gate_reached"], "PHYSICAL_METADATA")
        self.assertIn("UNITS_UNVERIFIED", res["diagnostic_reason"])

    # Scenario 8: Invalid sensitivity / dimensional mismatch
    def test_incompatible_sensitivity_units_blocked_calibration(self) -> None:
        """Sensitivity unit incompatible with output units fails with BLOCKED_CALIBRATION."""
        bad_ev = copy.deepcopy(self.evidence)
        # Unit is 'g', but sensitivity specified in mV/(m/s^2)
        bad_ev["sensor_identity"]["sensitivity_units"]["resolved_value"] = "mV/(m/s^2)"
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_CALIBRATION")
        self.assertEqual(res["precedence_gate_reached"], "CALIBRATION_VALIDITY")
        self.assertIn("DIMENSIONAL_MATH_ERROR", res["diagnostic_reason"])

    # Scenario 9: Missing physical full scale
    def test_missing_physical_full_scale_blocked_unknown_metadata(self) -> None:
        """Missing physical full scale limit fails with BLOCKED_UNKNOWN_METADATA."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["sensor_identity"]["full_scale_limit_physical"] = {
            "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
            "resolved_value": None, "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "Range unstated.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_UNKNOWN_METADATA")
        self.assertEqual(res["precedence_gate_reached"], "PHYSICAL_METADATA")
        self.assertIn("FULL_SCALE_UNVERIFIED", res["diagnostic_reason"])

    # Scenario 10: Nonfinite samples in Stage 10
    def test_nonfinite_samples_blocked_signal_integrity(self) -> None:
        """Non-finite values present in container fail with BLOCKED_SIGNAL_INTEGRITY."""
        bad_s10 = copy.deepcopy(self.stage10_report)
        bad_s10["inspection_findings"]["structural_status"] = "NONFINITE_VALUES_DETECTED"
        bad_s10["inspection_findings"]["measurement_readiness"] = "BLOCKED_NONFINITE_VALUES"
        bad_s10["container_facts"]["nonfinite_sample_count"] = 5
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=bad_s10,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")
        self.assertIn("NONFINITE_VALUES_PRESENT", res["diagnostic_reason"])

    # Scenario 11: Sustained clipping
    def test_sustained_clipping_blocked_signal_integrity(self) -> None:
        """Waveform with >= 3 consecutive rail samples fails with BLOCKED_SIGNAL_INTEGRITY."""
        clipped_sig = copy.deepcopy(self.signal_integrity_pass)
        clipped_sig["longest_contiguous_rail_run"] = 4
        clipped_sig["rail_sample_count"] = 4
        clipped_sig["verification_status"] = "SUSTAINED_RAIL_SATURATION"
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=clipped_sig,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")
        self.assertIn("SUSTAINED_RAIL_SATURATION", res["diagnostic_reason"])

    # Scenario 12: Unknown clipping assessment -> Fail closed
    def test_unknown_clipping_assessment_fails_closed(self) -> None:
        """Omitting signal_integrity_evidence fails closed with BLOCKED_SIGNAL_INTEGRITY."""
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=None,  # Not provided
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")
        self.assertIn("SIGNAL_INTEGRITY_UNASSESSED", res["diagnostic_reason"])

    # Scenario 13: Unauthenticated calibration document
    def test_unauthenticated_document_fails_closed_in_empty_registry(self) -> None:
        """Evaluating evidence against an empty registry rejects unauthenticated claims."""
        empty_reg = EvidenceRegistry.create_empty()
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=empty_reg,
            signal_integrity_evidence=self.signal_integrity_pass,
            source_artifact=self.source_path,
        )
        # Commercial grant reference is unauthenticated in empty registry -> BLOCKED_USAGE_RESTRICTIONS
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_USAGE_RESTRICTIONS")
        self.assertEqual(res["precedence_gate_reached"], "USAGE_PERMISSIONS")

    # Scenario 14: Unsupported commercial usage
    def test_unsupported_commercial_usage_blocked(self) -> None:
        """Unstated commercial grant in COMMERCIAL_R_AND_D fails with BLOCKED_USAGE_RESTRICTIONS."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["dataset_legal_provenance"]["affirmative_commercial_grant"] = {
            "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
            "resolved_value": None, "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "Commercial grant unstated.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_USAGE_RESTRICTIONS")
        self.assertEqual(res["precedence_gate_reached"], "USAGE_PERMISSIONS")
        self.assertIn("COMMERCIAL_GRANT_UNVERIFIED", res["diagnostic_reason"])

    # Scenario 15: Conflicting primary evidence
    def test_conflicting_evidence_blocked(self) -> None:
        """Reported CONFLICTING_EVIDENCE fails with BLOCKED_CONFLICTING_EVIDENCE."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["sensor_identity"]["sensitivity"] = {
            "status": "CONFLICTING_EVIDENCE", "evidence_level": None, "evidence_reference": None,
            "conflicting_references": [self.doc_ref, self.doc_ref],
            "resolved_value": None, "confidence_assessment": "CONFLICTING",
            "evidence_gap_statement": "Conflicting calibration sheets.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_CONFLICTING_EVIDENCE")
        self.assertEqual(res["precedence_gate_reached"], "CONFLICTING_EVIDENCE")
        self.assertIn("CONFLICTING_PRIMARY_RECORDS", res["diagnostic_reason"])

    # Scenario 16: Specimen identity unknown -> Decoupled
    def test_unknown_specimen_identity_decoupled(self) -> None:
        """Unproven specimen distinctness admits physical measurement but blocks diagnostic eligibility."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["experiment_specimen_identity"]["specimen_distinctness_proven"] = {
            "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
            "resolved_value": None, "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "Teardown logs missing.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(res["diagnostic_evaluation_eligibility"]["status"], "BLOCKED_AMBIGUOUS_SPECIMEN")

    # Scenario 17: Ground-truth authority missing
    def test_ground_truth_authority_missing_blocks_diagnostic(self) -> None:
        """Unverified ground-truth fault label authority blocks diagnostic eligibility."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["experiment_specimen_identity"]["fault_condition"]["ground_truth_label_authority"] = {
            "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
            "resolved_value": None, "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "No metrology verification.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(res["diagnostic_evaluation_eligibility"]["status"], "BLOCKED_UNVERIFIED_FAULT_LABEL")

    # Scenario 18: Cross-partition specimen overlap
    def test_cross_partition_specimen_leakage_blocks_diagnostic(self) -> None:
        """Specimen assigned across both train and test partitions blocks diagnostic eligibility."""
        partitions = {
            "precautionary_group_fictional_01_load0": "TRAIN",
            "precautionary_group_fictional_01_load1": "TEST",  # Same specimen leaked into TEST!
        }
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=partitions,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(res["diagnostic_evaluation_eligibility"]["status"], "BLOCKED_PARTITION_LEAKAGE")

    # Scenario 19: Operational real-data request -> Disabled
    def test_operational_real_data_request_disabled(self) -> None:
        """Attempting operational real-data admission in Stage 12 raises OperationalAdmissionDisabledError."""
        with self.assertRaises(OperationalAdmissionDisabledError):
            evaluate_measurement_admission(
                evidence=self.evidence,
                stage10_report=self.stage10_report,
                intended_use="COMMERCIAL_R_AND_D",
                evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
                signal_integrity_evidence=self.signal_integrity_pass,
                operational_mode=True,
            )

    # Scenario 20: Deterministic output for same input
    def test_deterministic_output_across_repeated_evaluations(self) -> None:
        """Repeated evaluations of identical inputs yield identical admission outcomes."""
        res1 = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        res2 = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res1["measurement_admission_status"], res2["measurement_admission_status"])
        self.assertEqual(res1["diagnostic_reason"], res2["diagnostic_reason"])
        self.assertEqual(res1["precedence_gate_reached"], res2["precedence_gate_reached"])
        self.assertEqual(
            res1["diagnostic_evaluation_eligibility"]["status"],
            res2["diagnostic_evaluation_eligibility"]["status"]
        )
        # Deterministic decision ID and decided_at
        self.assertEqual(res1["decision_id"], res2["decision_id"])
        self.assertEqual(res1["decided_at"], res2["decided_at"])
        self.assertEqual(res1, res2)

    # Scenario 21: Non-mutation
    def test_non_mutation_of_inputs(self) -> None:
        """Evaluation must not mutate input evidence or inspection report."""
        ev_copy = copy.deepcopy(self.evidence)
        s10_copy = copy.deepcopy(self.stage10_report)
        evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(self.evidence, ev_copy)
        self.assertEqual(self.stage10_report, s10_copy)

    # Scenario 22: Precedence hierarchy tests
    def test_precedence_structural_over_signal_integrity(self) -> None:
        """Structural format failure must trigger first, even if signal integrity also fails."""
        bad_s10 = copy.deepcopy(self.stage10_report)
        bad_s10["inspection_findings"]["structural_status"] = "MALFORMED_CONTAINER"
        bad_s10["inspection_findings"]["measurement_readiness"] = "BLOCKED_CORRUPTED_FILE"
        bad_s10["container_facts"]["container_format"] = "UNRECOGNIZED"
        bad_s10["container_facts"]["numeric_dtype"] = None
        bad_s10["container_facts"]["numeric_shape"] = None
        bad_s10["container_facts"]["sample_count"] = None
        bad_s10["container_facts"]["nonfinite_sample_count"] = None
        bad_s10["container_facts"]["observed_raw_extrema"] = None
        bad_s10["container_facts"]["available_variables"] = []
        bad_s10["container_facts"]["target_variable"] = None
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=bad_s10,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_UNSUPPORTED_FORMAT")
        self.assertEqual(res["precedence_gate_reached"], "STRUCTURAL_FORMAT")

    # =========================================================================
    # Stage 12 Repair 01 Adversarial Regression Suite
    # =========================================================================

    def test_adversarial_only_status_pass_blocked(self) -> None:
        """Caller supplying only {'verification_status': 'PASS'} is blocked."""
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence={"verification_status": "PASS"},
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")
        self.assertIn("SIGNAL_INTEGRITY_CONTRACT_INVALID", res["diagnostic_reason"])

    def test_adversarial_signal_digest_mismatch_blocked(self) -> None:
        """Signal integrity evidence with wrong source SHA-256 is blocked."""
        bad_sig = copy.deepcopy(self.signal_integrity_pass)
        bad_sig["source_sha256"] = "f" * 64
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=bad_sig,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")
        self.assertIn("SIGNAL_INTEGRITY_DIGEST_MISMATCH", res["diagnostic_reason"])

    def test_adversarial_signal_channel_mismatch_blocked(self) -> None:
        """Signal integrity evidence with wrong target channel is blocked."""
        bad_sig = copy.deepcopy(self.signal_integrity_pass)
        bad_sig["target_channel"] = "FE_time"
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=bad_sig,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")
        self.assertIn("SIGNAL_INTEGRITY_TARGET_MISMATCH", res["diagnostic_reason"])

    def test_adversarial_signal_sample_count_mismatch_blocked(self) -> None:
        """Signal integrity evidence with wrong sample count is blocked."""
        bad_sig = copy.deepcopy(self.signal_integrity_pass)
        bad_sig["sample_count"] = 5000
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=bad_sig,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")
        self.assertIn("SIGNAL_INTEGRITY_TARGET_MISMATCH", res["diagnostic_reason"])

    def test_adversarial_signal_unknown_status_blocked(self) -> None:
        """Signal integrity evidence with verification_status 'UNKNOWN' is blocked."""
        bad_sig = copy.deepcopy(self.signal_integrity_pass)
        bad_sig["verification_status"] = "UNKNOWN"
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=bad_sig,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")

    def test_adversarial_signal_unknown_physical_full_scale_blocked(self) -> None:
        """Signal integrity evidence with UNKNOWN_FULL_SCALE is blocked at PHYSICAL_METADATA."""
        bad_sig = copy.deepcopy(self.signal_integrity_pass)
        bad_sig["full_scale_limit"] = None
        bad_sig["verification_status"] = "UNKNOWN_FULL_SCALE"
        bad_sig["rail_sample_count"] = None
        bad_sig["longest_contiguous_rail_run"] = None
        bad_sig["clipping_ratio"] = None
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=bad_sig,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")

    def test_adversarial_signal_inconsistent_clipping_ratio_blocked(self) -> None:
        """Signal integrity evidence with clipping ratio inconsistent with rail count is blocked."""
        bad_sig = copy.deepcopy(self.signal_integrity_pass)
        bad_sig["clipping_ratio"] = 0.50
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=bad_sig,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")
        self.assertEqual(res["precedence_gate_reached"], "SIGNAL_INTEGRITY")

    def test_adversarial_forged_document_uri_rejected(self) -> None:
        """Evidence reference with forged locator URI fails authentication."""
        forged_ref = copy.deepcopy(self.doc_ref)
        forged_ref["locator_uri"] = "https://forged.example.com/datasheets/352C33.pdf"
        self.assertFalse(self.fictional_registry.is_authenticated(forged_ref, "CALIBRATION"))

    def test_adversarial_incorrect_document_digest_rejected(self) -> None:
        """Evidence reference with incorrect document SHA-256 fails authentication."""
        bad_sha_ref = copy.deepcopy(self.doc_ref)
        bad_sha_ref["document_sha256"] = "0" * 64
        self.assertFalse(self.fictional_registry.is_authenticated(bad_sha_ref, "CALIBRATION"))

    def test_adversarial_missing_partition_assignments_blocks_diagnostic(self) -> None:
        """Omitted partition assignments block diagnostic eligibility."""
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=None,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(res["diagnostic_evaluation_eligibility"]["status"], "BLOCKED_PARTITION_LEAKAGE")
        self.assertIn("PARTITION_EVIDENCE_MISSING", res["diagnostic_evaluation_eligibility"]["reasons"][0])

    def test_adversarial_unrelated_partition_assignments_blocks_diagnostic(self) -> None:
        """Partition assignments lacking target recording/specimen block diagnostic eligibility."""
        unrelated = {
            "recording_partitions": {"unrelated_file.mat": "TRAIN"},
            "recording_specimens": {"unrelated_file.mat": "SPECIMEN_UNRELATED"},
        }
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=unrelated,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(res["diagnostic_evaluation_eligibility"]["status"], "BLOCKED_PARTITION_LEAKAGE")
        self.assertIn("UNRELATED_PARTITION_MAPPING", res["diagnostic_evaluation_eligibility"]["reasons"][0])

    def test_adversarial_cross_specimen_partition_leakage_blocks_diagnostic(self) -> None:
        """Specimen assigned across multiple partitions blocks diagnostic eligibility."""
        leaked = {
            "recording_partitions": {
                "fictional_bench_01.mat": "TRAIN",
                "fictional_bench_01_load2.mat": "TEST",
            },
            "recording_specimens": {
                "fictional_bench_01.mat": "SPECIMEN_BEARING_01",
                "fictional_bench_01_load2.mat": "SPECIMEN_BEARING_01",
            },
            "target_recording_id": "fictional_bench_01.mat",
        }
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=leaked,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(res["diagnostic_evaluation_eligibility"]["status"], "BLOCKED_PARTITION_LEAKAGE")
        self.assertIn("CROSS_SPECIMEN_LEAKAGE", res["diagnostic_evaluation_eligibility"]["reasons"][0])

    def test_adversarial_unrecognized_raw_units_blocks_calibration(self) -> None:
        """Unrecognized raw stored units fail closed with BLOCKED_CALIBRATION."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["acquisition_signal_interpretation"]["raw_stored_units"]["resolved_value"] = "DIMENSIONLESS_NORMALIZED"
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_CALIBRATION")
        self.assertEqual(res["precedence_gate_reached"], "CALIBRATION_VALIDITY")
        self.assertIn("UNRECOGNIZED_RAW_UNITS", res["diagnostic_reason"])

    def test_adversarial_full_scale_inconsistency_blocks_calibration(self) -> None:
        """Disagreement between sensor full scale and measurement range peak fails with BLOCKED_CALIBRATION."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["sensor_identity"]["measurement_range_peak_g"]["resolved_value"] = 100.0  # full scale is 50.0
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_CALIBRATION")
        self.assertEqual(res["precedence_gate_reached"], "CALIBRATION_VALIDITY")
        self.assertIn("FULL_SCALE_INCONSISTENCY", res["diagnostic_reason"])

    def test_adversarial_digital_counts_missing_bit_depth_blocks_calibration(self) -> None:
        """Digital counts without ADC bit depth fails with BLOCKED_CALIBRATION."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["acquisition_signal_interpretation"]["raw_stored_units"]["resolved_value"] = "RAW_ADC_COUNTS"
        bad_ev["acquisition_signal_interpretation"]["adc_bit_depth"] = {
            "status": "UNKNOWN_UNSTATED",
            "evidence_level": None,
            "evidence_reference": None,
            "resolved_value": None,
            "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "ADC bit depth unstated.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_CALIBRATION")
        self.assertEqual(res["precedence_gate_reached"], "CALIBRATION_VALIDITY")
        self.assertIn("ADC_ENCODING_UNVERIFIED", res["diagnostic_reason"])

    def test_adversarial_voltage_conversion_missing_afe_gain_blocks_calibration(self) -> None:
        """Voltage conversion with zero/missing AFE gain fails with BLOCKED_CALIBRATION."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["acquisition_signal_interpretation"]["analog_front_end_gain"] = {
            "status": "UNKNOWN_UNSTATED",
            "evidence_level": None,
            "evidence_reference": None,
            "resolved_value": None,
            "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "AFE gain unstated.",
        }
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_CALIBRATION")
        self.assertEqual(res["precedence_gate_reached"], "CALIBRATION_VALIDITY")
        self.assertIn("AFE_GAIN_UNVERIFIED", res["diagnostic_reason"])

    def test_signal_provenance_analyzer_must_be_trusted(self) -> None:
        bad_sig = copy.deepcopy(self.signal_integrity_pass)
        bad_sig["data_quality_provenance"]["analyzer"] = "made-up"
        res = evaluate_measurement_admission(
            evidence=self.evidence, stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D", evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=bad_sig,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")

    def test_signal_scale_must_match_authenticated_domain(self) -> None:
        for field, value in (("full_scale_units", "bananas"), ("full_scale_limit", 1e99)):
            bad_sig = copy.deepcopy(self.signal_integrity_pass)
            bad_sig[field] = value
            res = evaluate_measurement_admission(
                evidence=self.evidence, stage10_report=self.stage10_report,
                intended_use="COMMERCIAL_R_AND_D", evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
                signal_integrity_evidence=bad_sig,
            )
            self.assertNotEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")

    def test_reported_clipping_ratio_must_match_counts_at_threshold(self) -> None:
        for rail_count in (12, 13):
            bad_sig = copy.deepcopy(self.signal_integrity_pass)
            bad_sig.update({
                "rail_sample_count": rail_count,
                "longest_contiguous_rail_run": 1,
                "clipping_ratio": rail_count / 120000,
                "verification_status": "PASS",
            })
            bad_sig["data_quality_provenance"]["sample_count"] = 120000
            res = evaluate_measurement_admission(
                evidence=self.evidence, stage10_report=self.stage10_report,
                intended_use="COMMERCIAL_R_AND_D", evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
                signal_integrity_evidence=bad_sig,
            )
            self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")

    def test_analog_front_end_gain_reference_must_authenticate(self) -> None:
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["acquisition_signal_interpretation"]["analog_front_end_gain"]["evidence_reference"] = copy.deepcopy(self.doc_ref)
        bad_ev["acquisition_signal_interpretation"]["analog_front_end_gain"]["evidence_reference"]["locator_uri"] = "https://forged.example/gain.pdf"
        res = evaluate_measurement_admission(
            evidence=bad_ev, stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D", evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_CALIBRATION")

    def test_missing_specimen_mapping_blocks_diagnostic_without_mutation(self) -> None:
        partitions = {"recording_partitions": {"fictional_bench_01.mat": "TRAIN"}}
        before = copy.deepcopy(partitions)
        res = evaluate_measurement_admission(
            evidence=self.evidence, stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D", evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=partitions,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(partitions, before)

    def test_nonfinite_signal_configuration_is_rejected(self) -> None:
        with self.assertRaises((TypeError, ValueError)):
            analyze_signal_integrity([100, 100, 100], "a" * 64, "DE_time", 50, rail_tolerance=float("nan"))
        with self.assertRaises((TypeError, ValueError)):
            analyze_signal_integrity([100, 100, 100], "a" * 64, "DE_time", float("inf"))
        with self.assertRaises((TypeError, ValueError)):
            analyze_signal_integrity([100, 100, 100], "a" * 64, "DE_time", True)
        with self.assertRaises((TypeError, ValueError)):
            analyze_signal_integrity([100, 100, 100], "a" * 64, "DE_time", 50, rail_tolerance=True)

    def test_repair03_tampered_trusted_signal_result_is_blocked(self) -> None:
        trusted = analyze_signal_integrity(
            [50.0] * 120000,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "DE_time",
            50.0,
            "g",
        )
        trusted["rail_sample_count"] = 0
        trusted["longest_contiguous_rail_run"] = 0
        trusted["clipping_ratio"] = 0.0
        trusted["verification_status"] = "PASS"
        trusted["source_sha256"] = self.source_digest
        trusted["data_quality_provenance"]["source_sha256"] = self.source_digest
        saturated_artifact = {
            "schema_version": "1.0.0",
            "artifact_format": "ADI_FICTIONAL_SOURCE_V1",
            "units": "g",
            "full_scale_limit": 50.0,
            "channels": {"DE_time": [50.0] * 120000},
        }
        saturated_path = Path(self.temp_dir.name) / "saturated_source.json"
        saturated_bytes = json.dumps(
            saturated_artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        saturated_path.write_bytes(saturated_bytes)
        saturated_digest = hashlib.sha256(saturated_bytes).hexdigest()
        bad_evidence = copy.deepcopy(self.evidence)
        bad_evidence["target_source_sha256"] = saturated_digest
        bad_evidence["dataset_legal_provenance"]["source_raw_byte_sha256"] = saturated_digest
        bad_stage10 = copy.deepcopy(self.stage10_report)
        bad_stage10["source_identity"]["raw_byte_sha256"] = saturated_digest
        trusted["source_sha256"] = saturated_digest
        trusted["data_quality_provenance"]["source_sha256"] = saturated_digest
        res = evaluate_measurement_admission(
            evidence=bad_evidence,
            stage10_report=bad_stage10,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=saturated_path,
            signal_integrity_evidence=trusted,
            partition_assignments=self.partition_assignments,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")

    def test_repair03_digital_scaling_forged_uri_is_blocked(self) -> None:
        bad_ev = copy.deepcopy(self.evidence)
        acq = bad_ev["acquisition_signal_interpretation"]
        acq["raw_stored_units"]["resolved_value"] = "RAW_ADC_COUNTS"
        acq["raw_stored_units"]["evidence_reference"] = copy.deepcopy(acq["raw_stored_units"]["evidence_reference"])
        acq["adc_scaling_factor"]["evidence_reference"] = copy.deepcopy(acq["adc_scaling_factor"]["evidence_reference"])
        acq["adc_scaling_factor"]["evidence_reference"]["locator_uri"] = "https://forged.example.com/scaling.pdf"
        res = evaluate_measurement_admission(
            evidence=bad_ev,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=self.partition_assignments,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_CALIBRATION")

    def test_repair03_bananas_partition_role_is_blocked(self) -> None:
        bad_partitions = {
            "recording_partitions": {
                "fictional_bench_01.mat": "BANANAS",
                "fictional_bench_02.mat": "TEST",
            },
            "recording_specimens": {
                "fictional_bench_01.mat": "SPECIMEN_BEARING_01",
                "fictional_bench_02.mat": "SPECIMEN_BEARING_02",
            },
        }
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=bad_partitions,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(res["diagnostic_evaluation_eligibility"]["status"], "BLOCKED_PARTITION_LEAKAGE")

    def test_repair04_source_digest_mismatch_is_blocked(self) -> None:
        bad_stage10 = copy.deepcopy(self.stage10_report)
        bad_stage10["source_identity"]["raw_byte_sha256"] = "0" * 64
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=bad_stage10,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=self.partition_assignments,
        )
        self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")

    def test_repair04_wrong_channel_and_all_zero_source_are_rejected(self) -> None:
        for artifact_channels in (
            {"FE_time": [1.0] * 120000},
            {"DE_time": [0.0] * 120000},
        ):
            artifact = {
                "schema_version": "1.0.0",
                "artifact_format": "ADI_FICTIONAL_SOURCE_V1",
                "units": "g",
                "full_scale_limit": 50.0,
                "channels": artifact_channels,
            }
            artifact_path = Path(self.temp_dir.name) / ("wrong_channel.json" if "FE_time" in artifact_channels else "zero_source.json")
            artifact_bytes = json.dumps(
                artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            artifact_path.write_bytes(artifact_bytes)
            artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
            bad_evidence = copy.deepcopy(self.evidence)
            bad_evidence["target_source_sha256"] = artifact_digest
            bad_evidence["dataset_legal_provenance"]["source_raw_byte_sha256"] = artifact_digest
            bad_stage10 = copy.deepcopy(self.stage10_report)
            bad_stage10["source_identity"]["raw_byte_sha256"] = artifact_digest
            bad_signal = copy.deepcopy(self.signal_integrity_pass)
            bad_signal["source_sha256"] = artifact_digest
            bad_signal["data_quality_provenance"]["source_sha256"] = artifact_digest
            res = evaluate_measurement_admission(
                evidence=bad_evidence,
                stage10_report=bad_stage10,
                intended_use="COMMERCIAL_R_AND_D",
                evidence_registry=self.fictional_registry,
                source_artifact=artifact_path,
                signal_integrity_evidence=bad_signal,
                partition_assignments=self.partition_assignments,
            )
            if "FE_time" in artifact_channels:
                self.assertEqual(res["measurement_admission_status"], "BLOCKED_PROVENANCE")
            else:
                self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")

    def test_repair04_saturated_and_nonfinite_sources_are_blocked(self) -> None:
        for name, samples in (
            ("saturated", [50.0] * 120000),
            ("nonfinite", [None] * 120000),
        ):
            artifact = {
                "schema_version": "1.0.0",
                "artifact_format": "ADI_FICTIONAL_SOURCE_V1",
                "units": "g",
                "full_scale_limit": 50.0,
                "channels": {"DE_time": samples},
            }
            artifact_path = Path(self.temp_dir.name) / f"{name}_source.json"
            artifact_bytes = json.dumps(
                artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            artifact_bytes = artifact_bytes.encode("utf-8")
            artifact_path.write_bytes(artifact_bytes)
            artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()
            bad_evidence = copy.deepcopy(self.evidence)
            bad_evidence["target_source_sha256"] = artifact_digest
            bad_evidence["dataset_legal_provenance"]["source_raw_byte_sha256"] = artifact_digest
            bad_stage10 = copy.deepcopy(self.stage10_report)
            bad_stage10["source_identity"]["raw_byte_sha256"] = artifact_digest
            bad_signal = copy.deepcopy(self.signal_integrity_pass)
            bad_signal["source_sha256"] = artifact_digest
            bad_signal["data_quality_provenance"]["source_sha256"] = artifact_digest
            res = evaluate_measurement_admission(
                evidence=bad_evidence,
                stage10_report=bad_stage10,
                intended_use="COMMERCIAL_R_AND_D",
                evidence_registry=self.fictional_registry,
                source_artifact=artifact_path,
                signal_integrity_evidence=bad_signal,
                partition_assignments=self.partition_assignments,
            )
            self.assertEqual(res["measurement_admission_status"], "BLOCKED_SIGNAL_INTEGRITY")

    def test_repair03_null_specimen_mapping_is_blocked(self) -> None:
        bad_partitions = copy.deepcopy(self.partition_assignments)
        bad_partitions["recording_specimens"]["fictional_bench_02.mat"] = None
        res = evaluate_measurement_admission(
            evidence=self.evidence,
            stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D",
            evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=bad_partitions,
        )
        self.assertEqual(res["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertFalse(res["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(res["diagnostic_evaluation_eligibility"]["status"], "BLOCKED_PARTITION_LEAKAGE")

    def test_decision_id_changes_with_signal_evidence(self) -> None:
        first = evaluate_measurement_admission(
            evidence=self.evidence, stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D", evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=self.signal_integrity_pass,
            partition_assignments=self.partition_assignments,
        )
        changed = copy.deepcopy(self.signal_integrity_pass)
        changed["data_quality_provenance"]["analyzed_at"] = "2026-11-20T01:00:00Z"
        second = evaluate_measurement_admission(
            evidence=self.evidence, stage10_report=self.stage10_report,
            intended_use="COMMERCIAL_R_AND_D", evidence_registry=self.fictional_registry,
            source_artifact=self.source_path,
            signal_integrity_evidence=changed,
            partition_assignments=self.partition_assignments,
        )
        self.assertNotEqual(first["decision_id"], second["decision_id"])


if __name__ == "__main__":
    unittest.main()
