"""Unit and integration test suite for Stage 12: adi-portfolio admission CLI subcommands.

Verifies:
- validate-measurement-evidence on valid fictional evidence returns exit code 0
- validate-measurement-evidence on schema-invalid evidence returns exit code 1
- validate-measurement-evidence on nonexistent or malformed JSON returns exit code 2
- validate-evidence alias works identically
- simulate-measurement-admission on fully authenticated fictional fixture returns exit code 0
- simulate-measurement-admission on blocked fixtures returns exit code 1
- simulate-measurement-admission atomic --out writing and non-overwrite protection
- simulate-admission alias works identically
- Rejection of invalid contexts, missing files, malformed JSON, and contract invalidity with exit code 2
- Backward compatibility of existing commands (demo, benchmark, evaluate, analyze, inspect-source)
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from typing import Any, Dict
import unittest
from unittest.mock import patch

from adi_app.portfolio import main


def _create_valid_fixtures() -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    doc_ref = {
        "document_title": "PCB 352C33 Spec Sheet",
        "locator_uri": "https://example.com/datasheets/352C33.pdf",
        "document_sha256": "05d1dc0142929ada23a90f34d0688c69472bb41c6863bde9d832112ed1576434",
        "retrieval_date": "2026-11-19",
        "passage_citation": "Section 2.1, Table 1",
        "extracted_text_verbatim": "Sensitivity: 100 mV/g",
    }
    rig_ref = {
        "document_title": "Laboratory Test Rig Documentation",
        "locator_uri": "https://example.com/docs/test_rig_specs.pdf",
        "document_sha256": "7929d21c2357a5ee8dbafcbcab13431cbc32b670b0343dedecd47c10f46d39ef",
        "retrieval_date": "2026-11-19",
        "passage_citation": "Section 1.2",
        "extracted_text_verbatim": "2HP motor test bench with 12 kHz crystal clock.",
    }
    teardown_ref = {
        "document_title": "Test Bench Teardown Logbook",
        "locator_uri": "https://example.com/logs/teardown_log_2026.pdf",
        "document_sha256": "65f9f8f982e5cbfccc25722376e4933975ee932432ba568eb540430d9a4feec4",
        "retrieval_date": "2026-11-19",
        "passage_citation": "Logbook #4, page 12",
        "extracted_text_verbatim": "Bearing specimen verified distinct and serialized.",
    }
    license_ref = {
        "document_title": "Precision Bearing Test Rig Benchmark",
        "locator_uri": "https://example.com/datasheets/benchmark.pdf",
        "document_sha256": "c06f0b4fce8b3d19420526f3f4aeaa744f6a1cf60fda5478345b0307caf24458",
        "retrieval_date": "2026-11-19",
        "passage_citation": "License Section",
        "extracted_text_verbatim": "Creative Commons Attribution CC-BY-4.0 terms.",
    }
    label_ref = {
        "document_title": "Metrology Inspection Certificate",
        "locator_uri": "https://example.com/certs/metrology_01.pdf",
        "document_sha256": "883289be69ae546a5aaf08988f8524ca74ca9bf78d3f46ddad5d48010cb76610",
        "retrieval_date": "2026-11-19",
        "passage_citation": "Certificate #994",
        "extracted_text_verbatim": "Fault label independently metrologized.",
    }

    evidence = {
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
            "manufacturer": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "PCB Piezotronics", "confidence_assessment": "VERIFIED_PRIMARY"},
            "model": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "352C33", "confidence_assessment": "VERIFIED_PRIMARY"},
            "sensor_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "PIEZOELECTRIC_IEPE", "confidence_assessment": "VERIFIED_PRIMARY"},
            "measurement_axis": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "RADIAL_VERTICAL", "confidence_assessment": "VERIFIED_PRIMARY"},
            "sensitivity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 100.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "sensitivity_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "mV/g", "confidence_assessment": "VERIFIED_PRIMARY"},
            "measurement_range_peak_g": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 50.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "full_scale_limit_physical": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 50.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "resolution_bits": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
            "frequency_response_bandwidth_3db_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": {"low_cutoff_hz": 0.5, "high_cutoff_hz": 10000.0}, "confidence_assessment": "VERIFIED_PRIMARY"},
            "calibration_certificate": {"available": False, "certificate_number": None, "calibrated_date": None, "traceability_authority": None},
            "mounting_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "MAGNETIC_BASE", "confidence_assessment": "VERIFIED_PRIMARY"},
            "mounting_location_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "Drive end bearing housing, 12 o'clock position", "confidence_assessment": "VERIFIED_PRIMARY"},
        },
        "acquisition_signal_interpretation": {
            "native_sampling_frequency_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": 12000.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "sampling_clock_provenance": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "INTERNAL_HARDWARE_CRYSTAL", "confidence_assessment": "VERIFIED_PRIMARY"},
            "raw_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "VOLTS", "confidence_assessment": "VERIFIED_PRIMARY"},
            "calibrated_acceleration_unit": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "g", "confidence_assessment": "VERIFIED_PRIMARY"},
            "adc_bit_depth": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
            "adc_input_range_volts": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "adc_scaling_factor": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "analog_front_end_gain": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "duration_seconds": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "sample_count": 120000,
            "channel_identity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "DE_time", "confidence_assessment": "VERIFIED_PRIMARY"},
            "analog_filtering_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "Analog low-pass filter, 4000 Hz cutoff", "confidence_assessment": "VERIFIED_PRIMARY"},
            "anti_aliasing_verified": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
            "saturation_detection_limit_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 9.99, "confidence_assessment": "VERIFIED_PRIMARY"},
            "missing_data_sample_count": 0,
            "nonfinite_sample_count": 0,
        },
        "experiment_specimen_identity": {
            "physical_specimen_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": teardown_ref, "resolved_value": "SPECIMEN_BEARING_01", "confidence_assessment": "VERIFIED_PRIMARY"},
            "specimen_distinctness_proven": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": teardown_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
            "machine_asset_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "TEST_RIG_01", "confidence_assessment": "VERIFIED_PRIMARY"},
            "equipment_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "Reliance Electric 2HP Induction Motor", "confidence_assessment": "VERIFIED_PRIMARY"},
            "rotational_speed_rpm": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "nominal_load_hp": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "load_percentage": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
            "operating_regime_classification": "STEADY_STATE_STATIC_LOAD",
            "measurement_session_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "SESSION_20261119_01", "confidence_assessment": "VERIFIED_PRIMARY"},
            "repeated_recording_dependency_flag": {
                "status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref,
                "resolved_value": False, "confidence_assessment": "VERIFIED_PRIMARY",
            },
            "related_experimental_recordings": [],
            "fault_condition": {
                "fault_category": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": label_ref, "resolved_value": "BASELINE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                "fault_dimension_label": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": label_ref, "resolved_value": "NONE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                "fault_provenance_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": label_ref, "resolved_value": "NATURAL_IN_SERVICE_FAILURE", "confidence_assessment": "VERIFIED_PRIMARY"},
                "ground_truth_label_authority": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": label_ref, "resolved_value": "Independent Metrology Inspection Team", "confidence_assessment": "VERIFIED_PRIMARY"},
            },
        },
        "dataset_legal_provenance": {
            "dataset_name": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "Precision Bearing Test Rig Benchmark", "confidence_assessment": "VERIFIED_PRIMARY"},
            "publisher_entity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "Analog Devices Edge AI Laboratory", "confidence_assessment": "VERIFIED_PRIMARY"},
            "primary_documentation_uris": ["https://example.com/datasheets/benchmark.pdf"],
            "dataset_version": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "1.0.0", "confidence_assessment": "VERIFIED_PRIMARY"},
            "acquisition_date": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "2026-11-19", "confidence_assessment": "VERIFIED_PRIMARY"},
            "source_raw_byte_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "legal_usage_license": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "CREATIVE_COMMONS_ATTRIBUTION_CC_BY_4_0", "confidence_assessment": "VERIFIED_PRIMARY"},
            "affirmative_commercial_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
            "affirmative_research_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
            "redistribution_permitted": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
            "attribution_required": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
            "attribution_text": "Attribution required under CC-BY-4.0 terms.",
            "transformation_derivation_history": [],
        },
    }

    stage10_report = {
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

    signal_integrity = {
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
        "verification_status": "PASS",
        "data_quality_provenance": {
            "analyzer": "adi_common.admission.analyze_signal_integrity",
            "analysis_policy_version": "stage12.signal-integrity.v2",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "target_channel": "DE_time",
            "sample_count": 120000,
            "rail_tolerance_fraction": 0.01,
            "analyzed_at": "2026-11-20T00:00:00Z",
        },
    }

    return evidence, stage10_report, signal_integrity


class TestPortfolioAdmissionCLI(unittest.TestCase):
    """Test suite for adi-portfolio validate-measurement-evidence and simulate-measurement-admission."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.evidence, self.stage10_report, self.sig_integrity = _create_valid_fixtures()

        self.ev_path = self.dir_path / "valid_evidence.json"
        self.ev_path.write_text(json.dumps(self.evidence, indent=2), encoding="utf-8")

        self.s10_path = self.dir_path / "valid_s10.json"
        self.s10_path.write_text(json.dumps(self.stage10_report, indent=2), encoding="utf-8")

        self.sig_path = self.dir_path / "valid_sig.json"
        self.sig_path.write_text(json.dumps(self.sig_integrity, indent=2), encoding="utf-8")

        artifact = {
            "schema_version": "1.0.0",
            "artifact_format": "ADI_FICTIONAL_SOURCE_V1",
            "units": "g",
            "full_scale_limit": 50.0,
            "channels": {"DE_time": [1.0] * 120000},
        }
        self.source_path = self.dir_path / "valid_source.json"
        source_bytes = json.dumps(
            artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        self.source_path.write_bytes(source_bytes)
        source_digest = hashlib.sha256(source_bytes).hexdigest()
        self.evidence["target_source_sha256"] = source_digest
        self.evidence["dataset_legal_provenance"]["source_raw_byte_sha256"] = source_digest
        self.stage10_report["source_identity"]["raw_byte_sha256"] = source_digest
        self.sig_integrity["source_sha256"] = source_digest
        self.sig_integrity["data_quality_provenance"]["source_sha256"] = source_digest
        self.sig_integrity["data_quality_provenance"]["rail_tolerance_fraction"] = 0.001
        self.ev_path.write_text(json.dumps(self.evidence, indent=2), encoding="utf-8")
        self.s10_path.write_text(json.dumps(self.stage10_report, indent=2), encoding="utf-8")
        self.sig_path.write_text(json.dumps(self.sig_integrity, indent=2), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # 1. validate-measurement-evidence tests
    def test_validate_measurement_evidence_success(self) -> None:
        """Valid evidence returns exit code 0 and outputs VALID result."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main(["validate-measurement-evidence", "--evidence", str(self.ev_path)])

        self.assertEqual(ret, 0)
        self.assertEqual(err_buf.getvalue(), "")
        parsed = json.loads(out_buf.getvalue())
        self.assertEqual(parsed["status"], "VALID")
        self.assertEqual(parsed["evidence_record_id"], "ev_rec_fictional_test_bench_01")
        self.assertEqual(parsed["target_channel"], "DE_time")

    def test_validate_evidence_alias(self) -> None:
        """Alias 'validate-evidence' works identically."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main(["validate-evidence", "--evidence", str(self.ev_path)])

        self.assertEqual(ret, 0)
        parsed = json.loads(out_buf.getvalue())
        self.assertEqual(parsed["status"], "VALID")

    def test_validate_measurement_evidence_schema_failure_exit_1(self) -> None:
        """Schema-violating evidence returns exit code 1 with stderr diagnostic."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["experiment_specimen_identity"]["rotational_speed_rpm"]["resolved_value"] = -500.0  # Invalid negative RPM
        bad_path = self.dir_path / "bad_rpm_evidence.json"
        bad_path.write_text(json.dumps(bad_ev, indent=2), encoding="utf-8")

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main(["validate-measurement-evidence", "--evidence", str(bad_path)])

        self.assertEqual(ret, 1)
        self.assertIn("Validation failed", err_buf.getvalue())

    def test_validate_measurement_evidence_missing_file_exit_2(self) -> None:
        """Nonexistent file returns exit code 2."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main(["validate-measurement-evidence", "--evidence", str(self.dir_path / "nonexistent.json")])

        self.assertEqual(ret, 2)
        self.assertIn("File not found", err_buf.getvalue())

    def test_validate_measurement_evidence_malformed_json_exit_2(self) -> None:
        """Malformed JSON file returns exit code 2."""
        malformed_path = self.dir_path / "malformed.json"
        malformed_path.write_text("{this is not json}", encoding="utf-8")

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main(["validate-measurement-evidence", "--evidence", str(malformed_path)])

        self.assertEqual(ret, 2)
        self.assertIn("Malformed JSON", err_buf.getvalue())

    # 2. simulate-measurement-admission tests
    def test_simulate_measurement_admission_success_exit_0(self) -> None:
        """Fully authenticated fictional fixture returns exit code 0 and valid decision."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main([
                "simulate-measurement-admission",
                "--evidence", str(self.ev_path),
                "--inspection", str(self.s10_path),
                "--signal-integrity", str(self.sig_path),
                "--source-artifact", str(self.source_path),
                "--context", "ACADEMIC_RESEARCH_NONCOMMERCIAL",
            ])

        self.assertEqual(ret, 0)
        self.assertEqual(err_buf.getvalue(), "")
        decision = json.loads(out_buf.getvalue())
        self.assertEqual(decision["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertEqual(decision["evaluation_mode"], "FICTIONAL_TEST_ONLY")
        self.assertFalse(decision["operational_execution_authorized"])
        self.assertEqual(decision["precedence_gate_reached"], "ADMISSION")
        self.assertFalse(decision["diagnostic_evaluation_eligibility"]["eligible"])
        self.assertEqual(
            decision["diagnostic_evaluation_eligibility"]["status"],
            "BLOCKED_PARTITION_LEAKAGE",
        )

    def test_simulate_measurement_admission_with_partitions_success_exit_0(self) -> None:
        """Fully authenticated fictional fixture with partition assignments admits diagnostic eligibility."""
        partitions = {
            "recording_partitions": {"fictional_bench_01.mat": "TRAIN"},
            "recording_specimens": {"fictional_bench_01.mat": "SPECIMEN_BEARING_01"},
        }
        part_path = self.dir_path / "valid_partitions.json"
        part_path.write_text(json.dumps(partitions, indent=2), encoding="utf-8")

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main([
                "simulate-measurement-admission",
                "--evidence", str(self.ev_path),
                "--inspection", str(self.s10_path),
                "--signal-integrity", str(self.sig_path),
                "--source-artifact", str(self.source_path),
                "--partitions", str(part_path),
                "--context", "ACADEMIC_RESEARCH_NONCOMMERCIAL",
            ])

        self.assertEqual(ret, 0)
        self.assertEqual(err_buf.getvalue(), "")
        decision = json.loads(out_buf.getvalue())
        self.assertEqual(decision["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")
        self.assertEqual(decision["evaluation_mode"], "FICTIONAL_TEST_ONLY")
        self.assertFalse(decision["operational_execution_authorized"])
        self.assertEqual(decision["precedence_gate_reached"], "ADMISSION")
        self.assertTrue(decision["diagnostic_evaluation_eligibility"]["eligible"])

    def test_simulate_admission_alias(self) -> None:
        """Alias 'simulate-admission' works identically."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main([
                "simulate-admission",
                "--evidence", str(self.ev_path),
                "--inspection", str(self.s10_path),
                "--signal-integrity", str(self.sig_path),
                "--source-artifact", str(self.source_path),
            ])

        self.assertEqual(ret, 0)
        decision = json.loads(out_buf.getvalue())
        self.assertEqual(decision["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")

    def test_simulate_measurement_admission_blocked_returns_exit_1(self) -> None:
        """Refused admission (e.g. unknown native sampling rate) returns exit code 1."""
        bad_ev = copy.deepcopy(self.evidence)
        bad_ev["acquisition_signal_interpretation"]["native_sampling_frequency_hz"] = {
            "status": "UNKNOWN_UNSTATED", "evidence_level": None, "evidence_reference": None,
            "resolved_value": None, "confidence_assessment": "UNVERIFIED",
            "evidence_gap_statement": "Rate unstated.",
        }
        bad_ev_path = self.dir_path / "bad_ev_sampling.json"
        bad_ev_path.write_text(json.dumps(bad_ev, indent=2), encoding="utf-8")

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main([
                "simulate-measurement-admission",
                "--evidence", str(bad_ev_path),
                "--inspection", str(self.s10_path),
                "--signal-integrity", str(self.sig_path),
                "--source-artifact", str(self.source_path),
            ])

        self.assertEqual(ret, 1)
        decision = json.loads(out_buf.getvalue())
        self.assertEqual(decision["measurement_admission_status"], "BLOCKED_UNKNOWN_METADATA")
        self.assertEqual(decision["precedence_gate_reached"], "PHYSICAL_METADATA")

    def test_simulate_measurement_admission_atomic_out_and_non_overwrite(self) -> None:
        """Simulate writes decision atomically to --out and rejects overwrite."""
        out_file = self.dir_path / "decision_output.json"
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main([
                "simulate-measurement-admission",
                "--evidence", str(self.ev_path),
                "--inspection", str(self.s10_path),
                "--signal-integrity", str(self.sig_path),
                "--source-artifact", str(self.source_path),
                "--out", str(out_file),
            ])

        self.assertEqual(ret, 0)
        self.assertTrue(out_file.exists())
        written_data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(written_data["measurement_admission_status"], "ADMITTED_FOR_MEASUREMENT")

        # Second run targeting existing out_file must fail with exit code 2 (non-overwrite)
        out_buf2 = io.StringIO()
        err_buf2 = io.StringIO()
        with patch("sys.stdout", out_buf2), patch("sys.stderr", err_buf2):
            ret2 = main([
                "simulate-measurement-admission",
                "--evidence", str(self.ev_path),
                "--inspection", str(self.s10_path),
                "--signal-integrity", str(self.sig_path),
                "--source-artifact", str(self.source_path),
                "--out", str(out_file),
            ])
        self.assertEqual(ret2, 2)
        self.assertIn("Output file already exists", err_buf2.getvalue())

    def test_simulate_measurement_admission_invalid_context_exit_2(self) -> None:
        """Unsupported operational context returns exit code 2."""
        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main([
                "simulate-measurement-admission",
                "--evidence", str(self.ev_path),
                "--inspection", str(self.s10_path),
                "--source-artifact", str(self.source_path),
                "--context", "NONEXISTENT_CONTEXT",
            ])

        self.assertEqual(ret, 2)
        self.assertIn("Unsupported operational context", err_buf.getvalue())

    def test_simulate_measurement_admission_malformed_stage10_exit_2(self) -> None:
        """Stage 10 input that fails contract validation returns exit code 2."""
        bad_s10 = copy.deepcopy(self.stage10_report)
        del bad_s10["source_identity"]
        bad_s10_path = self.dir_path / "bad_s10.json"
        bad_s10_path.write_text(json.dumps(bad_s10, indent=2), encoding="utf-8")

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("sys.stderr", err_buf):
            ret = main([
                "simulate-measurement-admission",
                "--evidence", str(self.ev_path),
                "--inspection", str(bad_s10_path),
                "--source-artifact", str(self.source_path),
            ])

        self.assertEqual(ret, 2)
        self.assertIn("source_identity", err_buf.getvalue())

    # 3. Backward compatibility tests
    def test_backward_compat_demo(self) -> None:
        """Demo command remains functioning."""
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            ret = main(["demo"])
        self.assertEqual(ret, 0)
        self.assertIn("ADI Portfolio Demo", out_buf.getvalue())

    def test_backward_compat_benchmark(self) -> None:
        """Benchmark command remains functioning."""
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            ret = main(["benchmark"])
        self.assertEqual(ret, 1)

    def test_backward_compat_evaluate_scaffold(self) -> None:
        """Evaluate command without suite remains functioning."""
        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf):
            ret = main(["evaluate"])
        self.assertEqual(ret, 1)

    def test_no_command_returns_2(self) -> None:
        """Invoking with no command prints help and returns exit code 2."""
        err_buf = io.StringIO()
        with patch("sys.stderr", err_buf):
            ret = main([])
        self.assertEqual(ret, 2)


if __name__ == "__main__":
    unittest.main()
