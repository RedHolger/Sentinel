"""Relocate package sources, build wheels, install and test offline in isolation.

Enforces strict dependency resolution gates: all declared dependencies must resolve
offline, and uv pip check must pass before packaging_verification can be PASS.
Restores full installed contract, runtime feature, and CLI JSON schema verification.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def run_release_verification(runner=subprocess.run, root_dir=None, clean_env=None):
    """Executes the complete isolated release verification pipeline.

    Args:
        runner: Callable matching subprocess.run signature.
        root_dir: Path to repository root.
        clean_env: Sanitized environment mapping.

    Returns:
        tuple[int, dict]: (exit_code, report_dict)
    """
    if root_dir is None:
        root_dir = Path(__file__).resolve().parents[1]
    else:
        root_dir = Path(root_dir)

    if clean_env is None:
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith(('ADI_', 'PYTHONPATH', 'VIRTUAL_ENV'))}

    with tempfile.TemporaryDirectory(prefix='adi-release-') as temporary:
        work = Path(temporary)
        wheels_dir = work / 'wheels'
        wheels_dir.mkdir(parents=True, exist_ok=True)

        # 1. Relocate package sources and build wheels offline
        for package in ['adi-common', 'adi-runtime', 'adi-app']:
            target = work / package
            target.mkdir(parents=True, exist_ok=True)
            for name in ['pyproject.toml', 'README.md']:
                src_file = root_dir / package / name
                if src_file.exists():
                    shutil.copy2(src_file, target / name)
            for name in ['src', 'tests', 'schemas']:
                source = root_dir / package / name
                if source.exists():
                    shutil.copytree(source, target / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            runner(['uv', 'build', '--offline', '--wheel', '--out-dir', str(wheels_dir), str(target)], check=True, env=clean_env, cwd=work)

        # 2. Create clean virtual environment
        runner(['uv', 'venv', '--python', '3.11', str(work / 'env')], check=True, env=clean_env, cwd=work)
        python = work / 'env/bin/python'
        executable = work / 'env/bin/adi-portfolio'

        common_whl = next(wheels_dir.glob('adi_common-*.whl'), wheels_dir / 'adi_common-0.1.0-py3-none-any.whl')
        runtime_whl = next(wheels_dir.glob('adi_runtime-*.whl'), wheels_dir / 'adi_runtime-0.1.0-py3-none-any.whl')
        app_whl = next(wheels_dir.glob('adi_app-*.whl'), wheels_dir / 'adi_app-0.1.0-py3-none-any.whl')

        # 3. Attempt normal dependency-resolved installation of all three wheels offline (NO --no-deps)
        install_proc = runner(
            ['uv', 'pip', 'install', '--offline', '--python', str(python), str(common_whl), str(runtime_whl), str(app_whl)],
            capture_output=True, text=True, env=clean_env, cwd=work
        )

        if install_proc.returncode != 0:
            err_output = ((install_proc.stderr or '') + "\n" + (install_proc.stdout or '')).strip()
            # Accurately identify specific missing packages only when evidenced in diagnostic output
            identified_missing = []
            for line in err_output.splitlines():
                lower = line.lower()
                if "numpy" in lower and "numpy" not in identified_missing:
                    identified_missing.append("numpy")
                for pkg in ["jsonschema", "attrs", "referencing", "rpds-py", "typing-extensions"]:
                    if pkg in lower and pkg not in identified_missing:
                        identified_missing.append(pkg)

            if identified_missing:
                blocker_reason = f"Dependency installation failed: package(s) {', '.join(identified_missing)} unavailable or failed to install in offline cache."
            else:
                blocker_reason = "Dependency installation failed: uv pip install --offline returned nonzero exit code."

            report = {
                'packaging_verification': 'BLOCKED',
                'relocated_source_build': 'PASS',
                'dependency_resolution': 'FAILED',
                'failed_operation': 'uv pip install --offline',
                'blocker_reason': blocker_reason,
                'diagnostic_output': err_output,
                'identified_missing_packages': identified_missing,
                'executed_checks': ['relocated_source_build'],
                'pending_checks': [
                    'dependency_check_verification',
                    'installed_adi_common_contracts_check',
                    'installed_adi_runtime_features_check',
                    'installed_cli_executable_check'
                ],
                'functional_validation': 'BLOCKED',
                'gate_status': 'FAILURE_MANDATED_ON_DEPENDENCY_INSTALLATION_FAILURE'
            }
            return 1, report

        # 4. Mandatory uv pip check: verify complete environment consistency
        exec_env = dict(clean_env)
        exec_env['PATH'] = f"{work / 'env/bin'}:{exec_env.get('PATH', '')}"

        pip_check = runner(['uv', 'pip', 'check', '--python', str(python)], capture_output=True, text=True, env=exec_env, cwd=work)
        if pip_check.returncode != 0:
            details = ((pip_check.stdout or '') + "\n" + (pip_check.stderr or '')).strip()
            report = {
                'packaging_verification': 'FAILED',
                'relocated_source_build': 'PASS',
                'dependency_resolution': 'PASS',
                'dependency_check': 'FAILED',
                'failed_operation': 'uv pip check',
                'diagnostic_output': details,
                'executed_checks': ['relocated_source_build', 'dependency_resolution'],
                'pending_checks': [
                    'installed_adi_common_contracts_check',
                    'installed_adi_runtime_features_check',
                    'installed_cli_executable_check'
                ],
                'functional_validation': 'BLOCKED',
                'gate_status': 'FAILURE_MANDATED_ON_DEPENDENCY_CHECK_FAILURE'
            }
            return 1, report

        # 5. Remove relocated sources before execution to expose undeclared source imports
        shutil.rmtree(work / 'adi-app', ignore_errors=True)
        shutil.rmtree(work / 'adi-common', ignore_errors=True)
        shutil.rmtree(work / 'adi-runtime', ignore_errors=True)

        # 6. In-process check for installed adi-common contracts
        common_code = '''
import hashlib
import json
import socket
import sys
import adi_common
from adi_common import (
    ContractValidationError,
    EvidenceRegistry,
    OperationalAdmissionDisabledError,
    evaluate_measurement_admission,
    load_schema,
    validate_anomaly_decision,
    validate_calibration_measurement_evidence,
    validate_exploratory_source_inspection,
    validate_measurement_admission_decision,
    validate_vibration_recording,
)

source = adi_common.__file__
assert 'site-packages' in source, f"adi_common source not in site-packages: {source}"

def deny(*args, **kwargs):
    raise RuntimeError('Network forbidden during offline release verification')
socket.socket.connect = deny
socket.create_connection = deny

# 1. Verify schema loading from package resources
rec_schema = load_schema("vibration_recording.schema.v1.json")
assert rec_schema.get("title") == "VibrationRecordingV1"
dec_schema = load_schema("anomaly_decision.schema.v1.json")
assert dec_schema.get("title") == "AnomalyDecisionV1"
insp_schema = load_schema("exploratory_source_inspection.schema.v1.json")
assert insp_schema.get("title") == "ExploratorySourceInspectionV1"
cal_schema = load_schema("calibration_measurement_evidence.schema.v1.json")
assert cal_schema.get("title") == "CalibrationMeasurementEvidenceV1"
adm_schema = load_schema("measurement_admission_decision.schema.v1.json")
assert adm_schema.get("title") == "MeasurementAdmissionDecisionV1"

# 2. Exercise valid recording payload
valid_recording = {
    "schema_version": "1.0.0",
    "recording_id": "rec-2026-rel-01",
    "machine_id": "pump-west-04",
    "session_id": "sess-shift1-002",
    "provenance": "synthetic",
    "sample_rate_hz": 4000.0,
    "acceleration_unit": "g",
    "channels": ["x"],
    "samples": [[0.012], [0.015], [-0.008], [0.021]],
    "source_id": "synth-gen-v1-sine-sweep",
    "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}
rec_res = validate_vibration_recording(valid_recording)
assert rec_res["recording_id"] == "rec-2026-rel-01"

# 3. Exercise valid decision payload
valid_decision = {
    "schema_version": "1.0.0",
    "decision_id": "dec-2026-rel-01",
    "recording_ref": "rec-2026-rel-01",
    "detector_id": "iso-10816-spectral-energy",
    "detector_version": "0.1.0",
    "outcome": "NORMAL",
    "execution_environment": "HOST_CPU",
    "quality_status": "PASS",
    "reason_codes": [],
    "score": 0.42,
    "threshold": 0.85,
}
dec_res = validate_anomaly_decision(valid_decision)
assert dec_res["outcome"] == "NORMAL"

# 4. Reject newline digest
bad_digest_rec = dict(valid_recording)
bad_digest_rec["source_sha256"] = "a" * 64 + "\\n"
try:
    validate_vibration_recording(bad_digest_rec)
    raise AssertionError("Failed to reject newline-terminated SHA-256")
except ContractValidationError:
    pass

# 5. Reject oversized numeric input (10**400 in samples)
oversized_rec = dict(valid_recording)
oversized_rec["samples"] = [[10**400], [0.02]]
try:
    validate_vibration_recording(oversized_rec)
    raise AssertionError("Failed to reject oversized integer sample value")
except ContractValidationError:
    pass

# 6. Reject oversized sample_rate_hz
oversized_hz_rec = dict(valid_recording)
oversized_hz_rec["sample_rate_hz"] = 10**400
try:
    validate_vibration_recording(oversized_hz_rec)
    raise AssertionError("Failed to reject oversized sample_rate_hz")
except ContractValidationError:
    pass

# 7. Reject oversized score and threshold
oversized_dec = dict(valid_decision)
oversized_dec["score"] = 10**400
try:
    validate_anomaly_decision(oversized_dec)
    raise AssertionError("Failed to reject oversized score")
except ContractValidationError:
    pass

# 8. Exercise valid exploratory source inspection payload
valid_inspection = {
    "schema_version": "1.0.0",
    "inspection_id": "insp-rel-01",
    "inspected_at": "2026-11-16T13:00:00Z",
    "source_identity": {
        "source_id": "cwru_bearing_data_center",
        "source_locator": "https://engineering.case.edu/sites/default/files/97.mat",
        "source_filename": "97.mat",
        "raw_byte_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "raw_byte_size": 1950000,
    },
    "container_facts": {
        "container_format": "MATLAB_V5",
        "available_variables": ["X097_DE_time"],
        "target_variable": "X097_DE_time",
        "numeric_dtype": "float64",
        "numeric_shape": [1000, 1],
        "sample_count": 1000,
        "nonfinite_sample_count": 0,
        "observed_raw_extrema": {"min": -1.5, "max": 1.5},
    },
    "source_documentation": {
        "sensor_physical_location": {"value": "DE", "evidence_reference": "Apparatus"},
        "test_rig_description": {"value": "Rig", "evidence_reference": "Apparatus"},
        "nominal_motor_load_hp": {"value": 0, "evidence_reference": "Table"},
        "nominal_motor_speed_rpm": {"value": 1797, "evidence_reference": "Table"},
        "recording_condition_label": {"value": "normal", "evidence_reference": "Table", "scope": "WHOLE_RECORDING_ONLY"},
    },
    "metadata_status_ledger": {
        "physical_acceleration_unit": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "Omitted"},
        "native_sampling_rate_hz": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "Omitted"},
        "acquisition_full_scale_limit": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "Omitted"},
        "physical_specimen_identity": {"status": "UNKNOWN_UNPROVEN", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "Unproven"},
        "legal_usage_grant": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "Unstated"},
    },
    "local_annotations": {
        "precautionary_specimen_group": "group_normal",
        "candidate_partition_role": "held_out_load_test",
        "provenance_classification": "recorded",
    },
    "inspection_findings": {
        "structural_status": "STRUCTURAL_SUCCESS",
        "measurement_readiness": "BLOCKED_UNKNOWN_METADATA",
        "detector_admission_eligible": False,
        "diagnostic_notes": ["OK"],
    },
}
assert validate_exploratory_source_inspection(valid_inspection) is None

# 9. Reject invalid inspection report (unknown with value)
bad_insp = dict(valid_inspection)
bad_insp["metadata_status_ledger"] = dict(valid_inspection["metadata_status_ledger"])
bad_insp["metadata_status_ledger"]["native_sampling_rate_hz"] = {
    "status": "UNKNOWN_UNSTATED",
    "resolved_value": 12000.0,
    "evidence_reference": None,
    "evidence_gap_statement": "Omitted",
}
try:
    validate_exploratory_source_inspection(bad_insp)
    raise AssertionError("Failed to reject invalid inspection report")
except ContractValidationError:
    pass

# 10. Reject oversized integer (10**5000) without decimal conversion error
oversized_insp = dict(valid_inspection)
oversized_insp["source_identity"] = dict(valid_inspection["source_identity"])
oversized_insp["source_identity"]["raw_byte_size"] = 10**5000
try:
    validate_exploratory_source_inspection(oversized_insp)
    raise AssertionError("Failed to reject oversized integer in inspection report")
except ContractValidationError as exc:
    assert len(str(exc)) > 0

# 11. Reject non-string object key without TypeError
bad_key_insp = dict(valid_inspection)
bad_key_insp[1] = "bad"
try:
    validate_exploratory_source_inspection(bad_key_insp)
    raise AssertionError("Failed to reject non-string key in inspection report")
except ContractValidationError as exc:
    assert len(str(exc)) > 0

# 12. Exercise valid calibration measurement evidence and admission decision contracts
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

valid_evidence = {
    "schema_version": "1.0.0",
    "evidence_record_id": "ev_rec_rel_01",
    "target_source_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "target_variable_name": "X097_DE_time",
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
        "mounting_location_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "Drive end bearing housing", "confidence_assessment": "VERIFIED_PRIMARY"},
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
        "sample_count": 1000,
        "channel_identity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "X097_DE_time", "confidence_assessment": "VERIFIED_PRIMARY"},
        "analog_filtering_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": "Analog low-pass filter", "confidence_assessment": "VERIFIED_PRIMARY"},
        "anti_aliasing_verified": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
        "saturation_detection_limit_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": doc_ref, "resolved_value": 9.99, "confidence_assessment": "VERIFIED_PRIMARY"},
        "missing_data_sample_count": 0,
        "nonfinite_sample_count": 0,
    },
    "experiment_specimen_identity": {
        "physical_specimen_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": teardown_ref, "resolved_value": "SPECIMEN_01", "confidence_assessment": "VERIFIED_PRIMARY"},
        "specimen_distinctness_proven": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": teardown_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
        "machine_asset_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "TEST_RIG_01", "confidence_assessment": "VERIFIED_PRIMARY"},
        "equipment_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "2HP Induction Motor", "confidence_assessment": "VERIFIED_PRIMARY"},
        "rotational_speed_rpm": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": 1797.0, "confidence_assessment": "VERIFIED_PRIMARY"},
        "nominal_load_hp": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
        "load_percentage": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
        "operating_regime_classification": "STEADY_STATE_STATIC_LOAD",
        "measurement_session_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": "SESSION_01", "confidence_assessment": "VERIFIED_PRIMARY"},
        "repeated_recording_dependency_flag": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": rig_ref, "resolved_value": False, "confidence_assessment": "VERIFIED_PRIMARY"},
        "related_experimental_recordings": [],
        "fault_condition": {
            "fault_category": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": label_ref, "resolved_value": "BASELINE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
            "fault_dimension_label": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": label_ref, "resolved_value": "NONE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
            "fault_provenance_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": label_ref, "resolved_value": "NATURAL_IN_SERVICE_FAILURE", "confidence_assessment": "VERIFIED_PRIMARY"},
            "ground_truth_label_authority": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": label_ref, "resolved_value": "Independent Metrology Team", "confidence_assessment": "VERIFIED_PRIMARY"},
        },
    },
    "dataset_legal_provenance": {
        "dataset_name": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "Precision Benchmark", "confidence_assessment": "VERIFIED_PRIMARY"},
        "publisher_entity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "Analog Devices Edge AI Laboratory", "confidence_assessment": "VERIFIED_PRIMARY"},
        "primary_documentation_uris": ["https://example.com/datasheets/benchmark.pdf"],
        "dataset_version": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "1.0.0", "confidence_assessment": "VERIFIED_PRIMARY"},
        "acquisition_date": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "2026-11-19", "confidence_assessment": "VERIFIED_PRIMARY"},
        "source_raw_byte_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "legal_usage_license": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": "CREATIVE_COMMONS_ATTRIBUTION_CC_BY_4_0", "confidence_assessment": "VERIFIED_PRIMARY"},
        "affirmative_commercial_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
        "affirmative_research_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
        "redistribution_permitted": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
        "attribution_required": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
        "attribution_text": "Attribution required under CC-BY-4.0 terms.",
        "transformation_derivation_history": [],
    },
}
validate_calibration_measurement_evidence(valid_evidence)

sig_pass = {
    "schema_version": "1.0.0",
    "source_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "target_channel": "X097_DE_time",
    "sample_count": 1000,
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
        "source_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "target_channel": "X097_DE_time",
        "sample_count": 1000,
        "rail_tolerance_fraction": 0.01,
        "analyzed_at": "2026-11-20T00:00:00Z",
    },
}

source_artifact = {
    "schema_version": "1.0.0",
    "artifact_format": "ADI_FICTIONAL_SOURCE_V1",
    "units": "g",
    "full_scale_limit": 50.0,
    "channels": {"X097_DE_time": [1.0] * 1000},
}
source_artifact_bytes = json.dumps(
    source_artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
).encode("utf-8")
source_artifact_sha = hashlib.sha256(source_artifact_bytes).hexdigest()
valid_evidence["target_source_sha256"] = source_artifact_sha
valid_evidence["dataset_legal_provenance"]["source_raw_byte_sha256"] = source_artifact_sha
valid_inspection["source_identity"]["raw_byte_sha256"] = source_artifact_sha
sig_pass["source_sha256"] = source_artifact_sha
sig_pass["data_quality_provenance"]["source_sha256"] = source_artifact_sha
sig_pass["data_quality_provenance"]["rail_tolerance_fraction"] = 0.001

# 13. Fail-closed behavior with empty registry
empty_reg = EvidenceRegistry.create_empty()
res_empty = evaluate_measurement_admission(
    evidence=valid_evidence,
    stage10_report=valid_inspection,
    intended_use="ACADEMIC_RESEARCH_NONCOMMERCIAL",
    evidence_registry=empty_reg,
    signal_integrity_evidence=sig_pass,
    source_artifact=source_artifact,
)
assert res_empty["measurement_admission_status"] == "BLOCKED_USAGE_RESTRICTIONS"
validate_measurement_admission_decision(res_empty)

# 14. Hypothetical admission with fictional test registry
fict_reg = EvidenceRegistry.create_fictional_test_registry()
partition_assignments = {
    "recording_partitions": {"97.mat": "TRAIN"},
    "recording_specimens": {"97.mat": "SPECIMEN_01"},
}
res_admit = evaluate_measurement_admission(
    evidence=valid_evidence,
    stage10_report=valid_inspection,
    intended_use="ACADEMIC_RESEARCH_NONCOMMERCIAL",
    evidence_registry=fict_reg,
    signal_integrity_evidence=sig_pass,
    partition_assignments=partition_assignments,
    source_artifact=source_artifact,
)
assert res_admit["measurement_admission_status"] == "ADMITTED_FOR_MEASUREMENT"
assert res_admit["evaluation_mode"] == "FICTIONAL_TEST_ONLY"
assert res_admit["operational_execution_authorized"] is False
assert res_admit["diagnostic_evaluation_eligibility"]["eligible"] is True
validate_measurement_admission_decision(res_admit)

# 14b. Fail-closed rejection of incomplete caller-provided verification_status PASS
res_bad_sig = evaluate_measurement_admission(
    evidence=valid_evidence,
    stage10_report=valid_inspection,
    intended_use="ACADEMIC_RESEARCH_NONCOMMERCIAL",
    evidence_registry=fict_reg,
    signal_integrity_evidence={"verification_status": "PASS"},
)
assert res_bad_sig["measurement_admission_status"] == "BLOCKED_SIGNAL_INTEGRITY"
validate_measurement_admission_decision(res_bad_sig)

# 14c. Fail-closed rejection of schema-violating decision (e.g. operational_execution_authorized: True or operational mode)
try:
    bad_dec = dict(res_admit)
    bad_dec["operational_execution_authorized"] = True
    validate_measurement_admission_decision(bad_dec)
    raise AssertionError("Failed to reject operational_execution_authorized: True")
except ContractValidationError:
    pass

try:
    bad_dec2 = dict(res_admit)
    bad_dec2["evaluation_mode"] = "OPERATIONAL_REAL_DATA"
    validate_measurement_admission_decision(bad_dec2)
    raise AssertionError("Failed to reject evaluation_mode: OPERATIONAL_REAL_DATA")
except ContractValidationError:
    pass

# 15. Operational execution request raises OperationalAdmissionDisabledError
try:
    evaluate_measurement_admission(
        evidence=valid_evidence,
        stage10_report=valid_inspection,
        intended_use="ACADEMIC_RESEARCH_NONCOMMERCIAL",
        evidence_registry=fict_reg,
        signal_integrity_evidence=sig_pass,
        operational_mode=True,
    )
    raise AssertionError("Failed to raise OperationalAdmissionDisabledError on operational_mode=True")
except OperationalAdmissionDisabledError:
    pass
'''
        common_res = runner([str(python), '-I', '-c', common_code], capture_output=True, text=True, env=exec_env, cwd=work)
        if common_res.returncode != 0:
            raise RuntimeError(f"installed adi-common contracts check failed: code={common_res.returncode}, stderr={common_res.stderr}")

        # 7. In-process check for installed adi-runtime window features
        runtime_code = '''
import math
import socket
import adi_runtime
from adi_runtime import (
    WindowFeatureError,
    extract_window_features,
    compute_channel_metrics,
    stable_mean,
    stable_rms,
    stable_peak_abs,
)

source = adi_runtime.__file__
assert 'site-packages' in source, f"adi_runtime source not in site-packages: {source}"

def deny(*args, **kwargs):
    raise RuntimeError('Network forbidden during offline release verification')
socket.socket.connect = deny
socket.create_connection = deny

# Opposite-sign extreme-value calculation
m_opp = compute_channel_metrics([1.5e308, -1.5e308])
assert m_opp["mean"] == 0.0, f"Expected mean 0.0, got {m_opp['mean']}"
assert m_opp["rms"] == 1.5e308
assert m_opp["peak_abs"] == 1.5e308

# Invalid-input rejection on public helper
for bad_input in [[True], [], [float("nan")], [10**400]]:
    try:
        compute_channel_metrics(bad_input)
        raise AssertionError(f"Failed to reject invalid input {bad_input} in compute_channel_metrics")
    except WindowFeatureError:
        pass

# Analytical example [3.0, 4.0]
rec = {
    "schema_version": "1.0.0",
    "recording_id": "rec-rel-01",
    "machine_id": "asset-01",
    "session_id": "sess-01",
    "provenance": "synthetic",
    "sample_rate_hz": 1000.0,
    "acceleration_unit": "g",
    "channels": ["x"],
    "samples": [[3.0], [4.0]],
    "source_id": "rel-test",
    "source_sha256": "0" * 64,
}
res = extract_window_features(rec, window_size=2, hop_size=2)
assert res["status"] == "SUCCESS"
assert math.isclose(res["windows"][0]["features"]["x"]["mean"], 3.5)
assert math.isclose(res["windows"][0]["features"]["x"]["rms"], math.sqrt(12.5))
assert math.isclose(res["windows"][0]["features"]["x"]["peak_abs"], 4.0)

# Multi-channel separation and trailing sample accounting
rec_3ch = {
    "schema_version": "1.0.0",
    "recording_id": "rec-rel-02",
    "machine_id": "asset-02",
    "session_id": "sess-02",
    "provenance": "synthetic",
    "sample_rate_hz": 1000.0,
    "acceleration_unit": "g",
    "channels": ["x", "y"],
    "samples": [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]],
    "source_id": "rel-test-2",
    "source_sha256": "1" * 64,
}
res_3ch = extract_window_features(rec_3ch, window_size=2, hop_size=2)
assert res_3ch["status"] == "SUCCESS"
assert len(res_3ch["windows"]) == 1
assert res_3ch["trailing_samples"] == 1
assert math.isclose(res_3ch["windows"][0]["features"]["x"]["mean"], 1.5)
assert math.isclose(res_3ch["windows"][0]["features"]["y"]["mean"], 15.0)
'''
        runtime_res = runner([str(python), '-I', '-c', runtime_code], capture_output=True, text=True, env=exec_env, cwd=work)
        if runtime_res.returncode != 0:
            raise RuntimeError(f"installed adi-runtime features check failed: code={runtime_res.returncode}, stderr={runtime_res.stderr}")

        # 8. In-process check for installed adi-app CSV ingestion
        app_ingest_code = '''
import math
import socket
import tempfile
from pathlib import Path
import adi_app
from adi_app import (
    RecordingIngestionError,
    IngestionParseError,
    IngestionMetadataError,
    load_vibration_csv,
)
from adi_runtime import extract_window_features

source = adi_app.__file__
assert 'site-packages' in source, f"adi_app source not in site-packages: {source}"

def deny(*args, **kwargs):
    raise RuntimeError('Network forbidden during offline release verification')
socket.socket.connect = deny
socket.create_connection = deny

# 1. Temporary CSV ingestion example
with tempfile.TemporaryDirectory() as tmp_dir:
    valid_csv = Path(tmp_dir) / "valid_installed_test.csv"
    valid_csv.write_text("x,y\\n3.0,10.0\\n4.0,20.0\\n", encoding="utf-8")

    meta = {
        "schema_version": "1.0.0",
        "recording_id": "rec-rel-installed-01",
        "machine_id": "installed-asset-01",
        "session_id": "sess-rel-01",
        "source_id": "synthetic-installed-test",
        "provenance": "synthetic",
        "sample_rate_hz": 1000.0,
        "acceleration_unit": "g",
    }
    rec = load_vibration_csv(valid_csv, meta)
    assert rec["channels"] == ["x", "y"]
    assert len(rec["samples"]) == 2
    assert rec["source_sha256"] is not None and len(rec["source_sha256"]) == 64

    # Feed into installed adi-runtime extract_window_features
    win_res = extract_window_features(rec, window_size=2, hop_size=2)
    assert win_res["status"] == "SUCCESS"
    assert math.isclose(win_res["windows"][0]["features"]["x"]["mean"], 3.5)
    assert math.isclose(win_res["windows"][0]["features"]["y"]["mean"], 15.0)

# 2. Rejection case: malformed row with missing cell
with tempfile.TemporaryDirectory() as tmp_dir:
    malformed_csv = Path(tmp_dir) / "malformed_installed_test.csv"
    malformed_csv.write_text("x,y\\n1.0,\\n", encoding="utf-8")
    meta_rej = {
        "schema_version": "1.0.0",
        "recording_id": "rec-rej-01",
        "machine_id": "installed-asset-01",
        "session_id": "sess-rel-01",
        "source_id": "synthetic-installed-test",
        "provenance": "synthetic",
        "sample_rate_hz": 1000.0,
        "acceleration_unit": "g",
    }
    try:
        load_vibration_csv(malformed_csv, meta_rej)
        raise AssertionError("Failed to reject malformed CSV in installed adi-app")
    except RecordingIngestionError:
        pass

# 3. Rejection probe: malformed unterminated quote (b'x\\n\"1')
with tempfile.TemporaryDirectory() as tmp_dir:
    quote_csv = Path(tmp_dir) / "quote_test.csv"
    quote_csv.write_bytes(b'x\\n"1')
    try:
        load_vibration_csv(quote_csv, meta)
        raise AssertionError("Failed to reject unterminated quoted field in installed adi-app")
    except IngestionParseError:
        pass

# 4. Rejection probe: oversized integer sample_rate_hz (10**400)
with tempfile.TemporaryDirectory() as tmp_dir:
    valid_csv2 = Path(tmp_dir) / "valid2.csv"
    valid_csv2.write_text("x\\n1.0\\n", encoding="utf-8")
    meta_oversized = dict(meta)
    meta_oversized["sample_rate_hz"] = 10**400
    try:
        load_vibration_csv(valid_csv2, meta_oversized)
        raise AssertionError("Failed to reject oversized integer sample_rate_hz in installed adi-app")
    except IngestionMetadataError:
        pass
'''
        app_ingest_res = runner([str(python), '-I', '-c', app_ingest_code], capture_output=True, text=True, env=exec_env, cwd=work)
        if app_ingest_res.returncode != 0:
            raise RuntimeError(f"installed adi-app ingestion check failed: code={app_ingest_res.returncode}, stderr={app_ingest_res.stderr}")

        # 9. In-process check for installed adi-runtime threshold reference detector
        threshold_code = '''
import socket
import adi_runtime
from adi_common import validate_anomaly_decision
from adi_runtime import (
    evaluate_rms_threshold,
    validate_threshold_configuration,
    ConfigurationError,
    DETECTOR_ID,
    DETECTOR_VERSION,
    EVALUATION_SCOPE,
    REASON_SENSOR_SATURATION_POSSIBLE,
    REASON_SAMPLE_RATE_MISMATCH,
)

source = adi_runtime.__file__
assert 'site-packages' in source, f"adi_runtime source not in site-packages: {source}"

def deny(*args, **kwargs):
    raise RuntimeError('Network forbidden during offline release verification')
socket.socket.connect = deny
socket.create_connection = deny

# 1. Exercise separate windows producing NORMAL, ANOMALY, and INSUFFICIENT_EVIDENCE
# Window size = 5, hop = 5, total 15 samples:
# Window 0 (0..5): samples=1.0 -> RMS=1.0 < 2.0 -> NORMAL
# Window 1 (5..10): contains sample 15.0 (>= full_scale 10.0) -> INSUFFICIENT_EVIDENCE
# Window 2 (10..15): samples=3.0 -> RMS=3.0 >= 2.0 -> ANOMALY
w0 = [[1.0]] * 5
w1 = [[1.0], [1.0], [15.0], [1.0], [1.0]]
w2 = [[3.0]] * 5
samples = w0 + w1 + w2

rec = {
    "schema_version": "1.0.0",
    "recording_id": "rec-rel-thresh-01",
    "machine_id": "mach-01",
    "session_id": "sess-01",
    "source_id": "src-01",
    "provenance": "synthetic",
    "sample_rate_hz": 1000.0,
    "acceleration_unit": "g",
    "channels": ["x"],
    "samples": samples,
    "source_sha256": "0" * 64,
}

cfg = {
    "config_id": "cfg-rel-01",
    "config_version": "1.0.0",
    "window_size": 5,
    "hop_size": 5,
    "expected_sample_rate_hz": 1000.0,
    "expected_acceleration_unit": "g",
    "expected_channels": ["x"],
    "full_scale": 10.0,
    "threshold": 2.0,
}

result = evaluate_rms_threshold(rec, cfg)
assert result["status"] == "COMPLETED"
assert len(result["decisions"]) == 3
assert len(result["window_boundaries"]) == 3
assert result["detector_id"] == DETECTOR_ID
assert result["detector_version"] == DETECTOR_VERSION
assert result["evaluation_scope"] == EVALUATION_SCOPE

# Validate each decision against contract
dec0 = result["decisions"][0]
dec1 = result["decisions"][1]
dec2 = result["decisions"][2]
validate_anomaly_decision(dec0)
validate_anomaly_decision(dec1)
validate_anomaly_decision(dec2)

assert dec0["outcome"] == "NORMAL"
assert dec0["quality_status"] == "PASS"
assert dec0["score"] == 1.0

assert dec1["outcome"] == "INSUFFICIENT_EVIDENCE"
assert dec1["quality_status"] == "INDETERMINATE"
assert dec1["score"] is None
assert dec1["reason_codes"] == [REASON_SENSOR_SATURATION_POSSIBLE]

assert dec2["outcome"] == "ANOMALY"
assert dec2["quality_status"] == "PASS"
assert dec2["score"] == 3.0

# 2. Exercise recording-level abstention on envelope mismatch
rec_mismatch = dict(rec)
rec_mismatch["sample_rate_hz"] = 2000.0
res_mismatch = evaluate_rms_threshold(rec_mismatch, cfg)
assert res_mismatch["status"] == "INSUFFICIENT_EVIDENCE"
assert res_mismatch["window_boundaries"] == []
assert len(res_mismatch["decisions"]) == 1
dec_mismatch = res_mismatch["decisions"][0]
validate_anomaly_decision(dec_mismatch)
assert dec_mismatch["outcome"] == "INSUFFICIENT_EVIDENCE"
assert dec_mismatch["quality_status"] == "FAIL"
assert REASON_SAMPLE_RATE_MISMATCH in dec_mismatch["reason_codes"]

# 3. Reproducer probe: Threshold change flipping outcome strictly produces distinct decision IDs
rec_flip = {
    "schema_version": "1.0.0",
    "recording_id": "rec-flip-rel",
    "machine_id": "mach-01",
    "session_id": "sess-01",
    "source_id": "src-01",
    "provenance": "synthetic",
    "sample_rate_hz": 1000.0,
    "acceleration_unit": "g",
    "channels": ["x"],
    "samples": [[2.0], [2.0]],
    "source_sha256": "0" * 64,
}
cfg_anom = {
    "config_id": "cfg-flip",
    "config_version": "1.0.0",
    "window_size": 2,
    "hop_size": 2,
    "expected_sample_rate_hz": 1000.0,
    "expected_acceleration_unit": "g",
    "expected_channels": ["x"],
    "full_scale": 10.0,
    "threshold": 1.0,
}
res_anom = evaluate_rms_threshold(rec_flip, cfg_anom)
dec_anom = res_anom["decisions"][0]
assert dec_anom["outcome"] == "ANOMALY"

cfg_norm = dict(cfg_anom)
cfg_norm["threshold"] = 3.0
res_norm = evaluate_rms_threshold(rec_flip, cfg_norm)
dec_norm = res_norm["decisions"][0]
assert dec_norm["outcome"] == "NORMAL"

assert dec_anom["decision_id"] != dec_norm["decision_id"], "Decision ID must change when threshold changes outcome"

# 4. Reproducer probe: Boolean alias bypass is strictly rejected
bad_alias_cfg = dict(cfg_anom)
bad_alias_cfg["rms_threshold"] = True
try:
    validate_threshold_configuration(bad_alias_cfg)
    raise AssertionError("Failed to reject boolean alias configuration in installed adi-runtime")
except ConfigurationError:
    pass

# Direct boolean in threshold is strictly rejected
bad_bool_cfg = dict(cfg_anom)
bad_bool_cfg["threshold"] = True
try:
    validate_threshold_configuration(bad_bool_cfg)
    raise AssertionError("Failed to reject boolean threshold in installed adi-runtime")
except ConfigurationError:
    pass

# 5. Reproducer probe: Lossless integer identity above 2**53 distinguishes NORMAL and INSUFFICIENT_EVIDENCE
base_fs = 2**53 + 4
base_th = 2**53 + 8
cfg_large_int = {
    "config_id": "cfg_large_int",
    "config_version": "1.0.0",
    "window_size": 1,
    "hop_size": 1,
    "expected_sample_rate_hz": 1000.0,
    "expected_acceleration_unit": "g",
    "expected_channels": ["x"],
    "full_scale": base_fs,
    "threshold": base_th,
}
rec_large_a = {
    "schema_version": "1.0.0",
    "recording_id": "rec_large_int",
    "machine_id": "mach-01",
    "session_id": "sess-01",
    "source_id": "src-01",
    "provenance": "synthetic",
    "sample_rate_hz": 1000.0,
    "acceleration_unit": "g",
    "channels": ["x"],
    "samples": [[2**53 + 3]],
    "source_sha256": "0" * 64,
}
rec_large_b = dict(rec_large_a)
rec_large_b["samples"] = [[2**53 + 4]]

res_large_a = evaluate_rms_threshold(rec_large_a, cfg_large_int)
res_large_b = evaluate_rms_threshold(rec_large_b, cfg_large_int)

dec_large_a = res_large_a["decisions"][0]
dec_large_b = res_large_b["decisions"][0]

assert dec_large_a["outcome"] == "NORMAL", f"Expected NORMAL, got {dec_large_a['outcome']}"
assert dec_large_b["outcome"] == "INSUFFICIENT_EVIDENCE", f"Expected INSUFFICIENT_EVIDENCE, got {dec_large_b['outcome']}"
assert dec_large_a["decision_id"] != dec_large_b["decision_id"], (
    f"Collision: large int samples produced identical decision ID {dec_large_a['decision_id']}"
)
'''
        threshold_res = runner([str(python), '-I', '-c', threshold_code], capture_output=True, text=True, env=exec_env, cwd=work)
        if threshold_res.returncode != 0:
            raise RuntimeError(f"installed adi-runtime threshold check failed: code={threshold_res.returncode}, stderr={threshold_res.stderr}")

        # 10. In-process check for installed adi-app source inspection
        app_inspection_code = '''
import hashlib
import os
import socket
import sys
import tempfile
from pathlib import Path
import numpy as np
import scipy.io
import adi_app
from adi_app import (
    SourceInspectionError,
    inspect_source,
)

source = adi_app.__file__
assert 'site-packages' in source, f"adi_app source not in site-packages: {source}"

def deny(*args, **kwargs):
    raise RuntimeError('Network forbidden during offline release verification')
socket.socket.connect = deny
socket.create_connection = deny

# 1. Exercise successful inspection of uncompressed Level 5 synthetic vector
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "synth_valid.mat"
    scipy.io.savemat(str(mat_path), {"vib_sig": np.array([[1.0, 2.0, 3.0]], dtype=np.float64)}, format="5")
    rep = inspect_source(mat_path, "vib_sig")
    assert rep["inspection_findings"]["structural_status"] == "STRUCTURAL_SUCCESS"
    assert rep["container_facts"]["numeric_shape"] == [1, 3]

# 2. Exercise rejection: missing target variable produces TARGET_VARIABLE_NOT_FOUND
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "synth_not_found.mat"
    scipy.io.savemat(str(mat_path), {"other_var": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    rep_nf = inspect_source(mat_path, "missing_var")
    assert rep_nf["inspection_findings"]["structural_status"] == "TARGET_VARIABLE_NOT_FOUND"

# 3. Exercise operational rejection: nonexistent path raises SourceInspectionError
with tempfile.TemporaryDirectory() as tmp_dir:
    try:
        inspect_source(Path(tmp_dir) / "nonexistent.mat", "var")
        raise AssertionError("Failed to raise SourceInspectionError on nonexistent file")
    except SourceInspectionError:
        pass

# 4. Exercise numeric tag mutation rejected before SciPy
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "synth_int8.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    raw = bytearray(mat_path.read_bytes())
    raw[176:180] = (1).to_bytes(4, "little")
    mat_path.write_bytes(raw)
    rep_int8 = inspect_source(mat_path, "sig")
    assert rep_int8["inspection_findings"]["structural_status"] == "UNSUPPORTED_STRUCTURE"

# 5. Exercise truncated padding rejected before SciPy
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "trunc_pad.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    raw = bytearray(mat_path.read_bytes())
    raw[132:136] = (61).to_bytes(4, "little")
    mat_path.write_bytes(raw[:197])
    rep_pad = inspect_source(mat_path, "sig")
    assert rep_pad["inspection_findings"]["structural_status"] == "MALFORMED_CONTAINER"

# 6. Exercise worker crash raises SourceInspectionError
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "worker_crash.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    try:
        inspect_source(mat_path, "sig", _worker_cmd=[sys.executable, "-c", "import sys; sys.exit(13)"])
        raise AssertionError("Failed to raise SourceInspectionError on worker exit 13")
    except SourceInspectionError:
        pass

# 7. Exercise variable name tag mutation rejected before SciPy
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "name_not_int8.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    raw = bytearray(mat_path.read_bytes())
    raw[168] = 9
    mat_path.write_bytes(raw)
    rep_name = inspect_source(mat_path, "sig")
    assert rep_name["inspection_findings"]["structural_status"] == "UNSUPPORTED_STRUCTURE"

# 8. Exercise symlink input rejected with SourceInspectionError
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "target.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    symlink_path = Path(tmp_dir) / "symlink.mat"
    symlink_path.symlink_to(mat_path)
    try:
        inspect_source(symlink_path, "sig")
        raise AssertionError("Failed to raise SourceInspectionError on symlink input")
    except SourceInspectionError:
        pass

# 9. Exercise worker closing pipes and sleeping classified as RESOURCE_LIMIT_EXCEEDED
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "sleep.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    rep_sleep = inspect_source(
        mat_path,
        "sig",
        timeout=0.2,
        _worker_cmd=[sys.executable, "-c", "import sys, time; sys.stdout.close(); sys.stderr.close(); time.sleep(2)"]
    )
    assert rep_sleep["inspection_findings"]["structural_status"] == "RESOURCE_LIMIT_EXCEEDED"
    assert rep_sleep["inspection_findings"]["measurement_readiness"] == "BLOCKED_RESOURCE_LIMIT"

# 10. Exercise unhashable worker status raises SourceInspectionError
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "status_list.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    bad_status_code = "import sys, json; sys.stdout.write(json.dumps({'status': [], 'container_format': 'MATLAB_V5', 'available_variables': [], 'notes': []})); sys.exit(0)"
    try:
        inspect_source(mat_path, "sig", _worker_cmd=[sys.executable, "-c", bad_status_code])
        raise AssertionError("Failed to raise SourceInspectionError on status list")
    except SourceInspectionError:
        pass

# 11. Exercise invalid container_format raises SourceInspectionError
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "bad_fmt.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    bad_fmt_code = "import sys, json; sys.stdout.write(json.dumps({'status': 'STRUCTURAL_SUCCESS', 'container_format': 'OTHER', 'available_variables': ['sig'], 'notes': []})); sys.exit(0)"
    try:
        inspect_source(mat_path, "sig", _worker_cmd=[sys.executable, "-c", bad_fmt_code])
        raise AssertionError("Failed to raise SourceInspectionError on invalid container_format")
    except SourceInspectionError:
        pass

# 12. Exercise post-acquisition symlink replacement retains acquisition identity
with tempfile.TemporaryDirectory() as tmp_dir:
    real_p = Path(tmp_dir) / "orig.mat"
    scipy.io.savemat(str(real_p), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    orig_sha256 = hashlib.sha256(real_p.read_bytes()).hexdigest()
    orig_uri = real_p.resolve().as_uri()

    other_p = Path(tmp_dir) / "other.mat"
    scipy.io.savemat(str(other_p), {"sig": np.array([[99.0, 100.0]], dtype=np.float64)}, format="5")

    worker_script = (
        f"import sys, os, json\\n"
        f"from pathlib import Path\\n"
        f"from adi_app.source_inspection import _worker_preflight_and_extract, MAX_SNAPSHOT_BYTES\\n"
        f"p = Path({repr(str(real_p))})\\n"
        f"bak = Path({repr(str(real_p))} + '.bak')\\n"
        f"os.rename(p, bak)\\n"
        f"os.symlink({repr(str(other_p))}, p)\\n"
        f"raw_bytes = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)\\n"
        f"res = _worker_preflight_and_extract(raw_bytes, 'sig')\\n"
        f"sys.stdout.write(json.dumps(res))\\n"
        f"sys.exit(0)\\n"
    )
    rep_swap = inspect_source(real_p, "sig", _worker_cmd=[sys.executable, "-c", worker_script])
    assert rep_swap["source_identity"]["raw_byte_sha256"] == orig_sha256
    assert rep_swap["source_identity"]["source_locator"] == orig_uri

# 13. Exercise unhashable available_variables raises SourceInspectionError
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "vars_list.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    bad_vars_code = "import sys, json; sys.stdout.write(json.dumps({'status': 'TARGET_VARIABLE_NOT_FOUND', 'container_format': 'MATLAB_V5', 'available_variables': [[]], 'notes': []})); sys.exit(0)"
    try:
        inspect_source(mat_path, "sig", _worker_cmd=[sys.executable, "-c", bad_vars_code])
        raise AssertionError("Failed to raise SourceInspectionError on unhashable available_variables")
    except SourceInspectionError:
        pass

# 14. Exercise oversized extrema integer raises SourceInspectionError without OverflowError
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "huge_ext.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    bad_ext_code = "import sys, json; sys.stdout.write(json.dumps({'status': 'STRUCTURAL_SUCCESS', 'container_format': 'MATLAB_V5', 'available_variables': ['sig'], 'notes': [], 'numeric_dtype': 'float64', 'numeric_shape': [1, 2], 'sample_count': 2, 'nonfinite_sample_count': 0, 'observed_raw_extrema': {'min': 0, 'max': 10**400}})); sys.exit(0)"
    try:
        inspect_source(mat_path, "sig", _worker_cmd=[sys.executable, "-c", bad_ext_code])
        raise AssertionError("Failed to raise SourceInspectionError on oversized extrema integer")
    except SourceInspectionError:
        pass

# 15. Exercise parent-directory symlink swap detection and rejection
with tempfile.TemporaryDirectory() as tmp_dir:
    dir_a = Path(tmp_dir) / "dir_a"
    dir_b = Path(tmp_dir) / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    file_a = dir_a / "source.mat"
    file_b = dir_b / "source.mat"
    scipy.io.savemat(str(file_a), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    scipy.io.savemat(str(file_b), {"sig": np.array([[99.0, 100.0]], dtype=np.float64)}, format="5")
    alias = Path(tmp_dir) / "alias"
    os.symlink(dir_a, alias)
    test_path = alias / "source.mat"

    orig_open = os.open
    def mock_open(file, flags, *args, **kwargs):
        if 'alias' in str(file):
            os.unlink(alias)
            os.symlink(dir_b, alias)
        return orig_open(file, flags, *args, **kwargs)

    from unittest.mock import patch
    with patch('adi_app.source_inspection.os.open', side_effect=mock_open):
        try:
            inspect_source(test_path, 'sig')
            raise AssertionError('Failed to raise SourceInspectionError on parent-directory symlink swap')
        except SourceInspectionError:
            pass

# 16. Exercise surrogate variable names, failure success-key leaks, and null byte variable arguments
with tempfile.TemporaryDirectory() as tmp_dir:
    mat_path = Path(tmp_dir) / "check16.mat"
    scipy.io.savemat(str(mat_path), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")

    # 16a. Worker variable name with surrogate code point raises SourceInspectionError
    bad_surrogate_code = "import sys, json; sys.stdout.write(json.dumps({'status': 'TARGET_VARIABLE_NOT_FOUND', 'container_format': 'MATLAB_V5', 'available_variables': ['\\\\ud800'], 'notes': []})); sys.exit(0)"
    try:
        inspect_source(mat_path, "sig", _worker_cmd=[sys.executable, "-c", bad_surrogate_code])
        raise AssertionError("Failed to raise SourceInspectionError on surrogate variable name")
    except SourceInspectionError:
        pass

    # 16b. Worker non-success payload containing success key (e.g. numeric_dtype: null) raises SourceInspectionError
    bad_fail_code = "import sys, json; sys.stdout.write(json.dumps({'status': 'TARGET_VARIABLE_NOT_FOUND', 'container_format': 'MATLAB_V5', 'available_variables': [], 'notes': [], 'numeric_dtype': None})); sys.exit(0)"
    try:
        inspect_source(mat_path, "sig", _worker_cmd=[sys.executable, "-c", bad_fail_code])
        raise AssertionError("Failed to raise SourceInspectionError on failure payload with numeric_dtype key")
    except SourceInspectionError:
        pass

    # 16c. inspect_source with invalid variable name (null byte or surrogate) raises SourceInspectionError
    try:
        inspect_source(mat_path, "sig\\x00")
        raise AssertionError("Failed to raise SourceInspectionError on variable name with null byte")
    except SourceInspectionError:
        pass
    try:
        inspect_source(mat_path, "sig\\ud800")
        raise AssertionError("Failed to raise SourceInspectionError on variable name with surrogate")
    except SourceInspectionError:
        pass

# 17. Exercise invalid path arguments (null byte, surrogate, empty, non-str) and legitimate Unicode paths
with tempfile.TemporaryDirectory() as tmp_dir:
    valid_mat = Path(tmp_dir) / "check17.mat"
    scipy.io.savemat(str(valid_mat), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")

    # 17a. inspect_source with null byte in path raises SourceInspectionError
    try:
        inspect_source("invalid\\x00.mat", "sig")
        raise AssertionError("Failed to raise SourceInspectionError on str path with null byte")
    except SourceInspectionError:
        pass
    try:
        inspect_source(Path("invalid\\x00.mat"), "sig")
        raise AssertionError("Failed to raise SourceInspectionError on Path with null byte")
    except SourceInspectionError:
        pass

    # 17b. inspect_source with surrogate in path raises SourceInspectionError
    try:
        inspect_source("invalid\\ud800.mat", "sig")
        raise AssertionError("Failed to raise SourceInspectionError on str path with surrogate")
    except SourceInspectionError:
        pass
    try:
        inspect_source(Path("invalid\\ud800.mat"), "sig")
        raise AssertionError("Failed to raise SourceInspectionError on Path with surrogate")
    except SourceInspectionError:
        pass

    # 17c. inspect_source with non-str/Path or empty path raises SourceInspectionError
    try:
        inspect_source(None, "sig")
        raise AssertionError("Failed to raise SourceInspectionError on None path")
    except SourceInspectionError:
        pass
    try:
        inspect_source("", "sig")
        raise AssertionError("Failed to raise SourceInspectionError on empty path")
    except SourceInspectionError:
        pass

    # 17d. Legitimate Unicode path succeeds
    unicode_dir = Path(tmp_dir) / "données_α"
    unicode_dir.mkdir()
    unicode_mat = unicode_dir / "vibration_β.mat"
    scipy.io.savemat(str(unicode_mat), {"sig": np.array([[1.0, 2.0]], dtype=np.float64)}, format="5")
    rep_uni = inspect_source(unicode_mat, "sig")
    assert rep_uni["inspection_findings"]["structural_status"] == "STRUCTURAL_SUCCESS"
    assert rep_uni["source_identity"]["source_filename"] == "vibration_β.mat"
'''
        app_inspection_res = runner([str(python), '-I', '-c', app_inspection_code], capture_output=True, text=True, env=exec_env, cwd=work)
        if app_inspection_res.returncode != 0:
            raise RuntimeError(f"installed adi-app source inspection check failed: code={app_inspection_res.returncode}, stderr={app_inspection_res.stderr}")

        # 11. Direct entry-point executable check (exercises env/bin/adi-portfolio)
        if runner is subprocess.run:
            if not executable.is_file():
                raise RuntimeError(f"installed CLI executable not found: {executable}")
        else:
            executable.parent.mkdir(parents=True, exist_ok=True)
            if not executable.exists():
                executable.touch()

        # demo: exit code 0, scaffold in stdout
        demo_cli = runner([str(executable), 'demo'], capture_output=True, text=True, env=exec_env, cwd=work)
        if demo_cli.returncode != 0 or 'scaffold' not in (demo_cli.stdout or '').lower():
            raise RuntimeError(f"direct entry-point demo failed: code={demo_cli.returncode}, stdout={demo_cli.stdout}")

        # benchmark: exit code 1, parseable JSON with status NOT_IMPLEMENTED, nonempty reason, hardware_status UNVERIFIED
        bench_cli = runner([str(executable), 'benchmark'], capture_output=True, text=True, env=exec_env, cwd=work)
        if bench_cli.returncode != 1:
            raise RuntimeError(f"direct entry-point benchmark failed: expected exit 1, got {bench_cli.returncode}, stderr={bench_cli.stderr}")
        try:
            bench_data = json.loads(bench_cli.stdout or '')
        except Exception as e:
            raise RuntimeError(f"direct entry-point benchmark output is not parseable JSON: {bench_cli.stdout}") from e
        if bench_data.get("status") != "NOT_IMPLEMENTED":
            raise RuntimeError(f"direct entry-point benchmark status != NOT_IMPLEMENTED: {bench_data}")
        if not bench_data.get("reason") or not str(bench_data.get("reason")).strip():
            raise RuntimeError(f"direct entry-point benchmark missing nonempty reason: {bench_data}")
        if bench_data.get("hardware_status") != "UNVERIFIED":
            raise RuntimeError(f"direct entry-point benchmark hardware_status != UNVERIFIED: {bench_data}")

        # evaluate: exit code 1, parseable JSON with status NOT_IMPLEMENTED, nonempty reason, hardware_status UNVERIFIED
        eval_cli = runner([str(executable), 'evaluate'], capture_output=True, text=True, env=exec_env, cwd=work)
        if eval_cli.returncode != 1:
            raise RuntimeError(f"direct entry-point evaluate failed: expected exit 1, got {eval_cli.returncode}, stderr={eval_cli.stderr}")
        try:
            eval_data = json.loads(eval_cli.stdout or '')
        except Exception as e:
            raise RuntimeError(f"direct entry-point evaluate output is not parseable JSON: {eval_cli.stdout}") from e
        if eval_data.get("status") != "NOT_IMPLEMENTED":
            raise RuntimeError(f"direct entry-point evaluate status != NOT_IMPLEMENTED: {eval_data}")
        if not eval_data.get("reason") or not str(eval_data.get("reason")).strip():
            raise RuntimeError(f"direct entry-point evaluate missing nonempty reason: {eval_data}")
        if eval_data.get("hardware_status") != "UNVERIFIED":
            raise RuntimeError(f"direct entry-point evaluate hardware_status != UNVERIFIED: {eval_data}")

        # analyze: comprehensive end-to-end installed CLI verification
        cli_test_dir = work / 'cli_test'
        cli_test_dir.mkdir(parents=True, exist_ok=True)
        csv_file = cli_test_dir / 'vibration.csv'
        meta_file = cli_test_dir / 'metadata.json'
        cfg_file = cli_test_dir / 'config.json'

        # 3-window recording: normal (0..5), saturated (5..10), anomaly (10..15)
        w0 = "1.0\n" * 5
        w1 = "1.0\n1.0\n15.0\n1.0\n1.0\n"
        w2 = "3.0\n" * 5
        csv_text = "x\n" + w0 + w1 + w2
        csv_bytes = csv_text.encode("utf-8")
        csv_sha256 = hashlib.sha256(csv_bytes).hexdigest()
        csv_file.write_bytes(csv_bytes)

        meta_dict = {
            "schema_version": "1.0.0",
            "recording_id": "rec-rel-cli-01",
            "machine_id": "mach-01",
            "session_id": "sess-01",
            "source_id": "sensor-01",
            "provenance": "synthetic",
            "sample_rate_hz": 1000.0,
            "acceleration_unit": "g",
        }
        meta_file.write_text(json.dumps(meta_dict), encoding="utf-8")

        cfg_dict = {
            "config_id": "cfg-rel-cli-01",
            "config_version": "1.0.0",
            "window_size": 5,
            "hop_size": 5,
            "expected_sample_rate_hz": 1000.0,
            "expected_acceleration_unit": "g",
            "expected_channels": ["x"],
            "full_scale": 10.0,
            "threshold": 2.0,
        }
        cfg_file.write_text(json.dumps(cfg_dict), encoding="utf-8")

        # 1. Successful execution covering NORMAL, INSUFFICIENT_EVIDENCE (saturation), and ANOMALY
        analyze_cmd = [
            str(executable), 'analyze',
            '--csv', str(csv_file),
            '--metadata', str(meta_file),
            '--config', str(cfg_file),
            '--expected-sha256', csv_sha256,
        ]
        analyze_res = runner(analyze_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if analyze_res.returncode != 0:
            raise RuntimeError(f"installed CLI analyze failed: code={analyze_res.returncode}, stderr={analyze_res.stderr}")
        try:
            analyze_data = json.loads(analyze_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI analyze output is not parseable JSON: {analyze_res.stdout}") from e

        if analyze_data.get("command") != "analyze":
            raise RuntimeError(f"installed CLI analyze command != analyze: {analyze_data}")
        if analyze_data.get("source_sha256") != csv_sha256:
            raise RuntimeError(f"installed CLI analyze source_sha256 mismatch: {analyze_data.get('source_sha256')} vs {csv_sha256}")
        if len(analyze_data.get("decisions", [])) != 3:
            raise RuntimeError(f"installed CLI analyze decisions count != 3: {analyze_data.get('decisions')}")

        dec0, dec1, dec2 = analyze_data["decisions"]
        if dec0.get("outcome") != "NORMAL":
            raise RuntimeError(f"installed CLI analyze window 0 outcome != NORMAL: {dec0}")
        if dec1.get("outcome") != "INSUFFICIENT_EVIDENCE":
            raise RuntimeError(f"installed CLI analyze window 1 outcome != INSUFFICIENT_EVIDENCE: {dec1}")
        if dec2.get("outcome") != "ANOMALY":
            raise RuntimeError(f"installed CLI analyze window 2 outcome != ANOMALY: {dec2}")

        summary = analyze_data.get("summary", {})
        if summary.get("total_decisions") != 3:
            raise RuntimeError(f"installed CLI analyze summary total_decisions != 3: {summary}")
        if summary.get("normal_count") != 1 or summary.get("anomaly_count") != 1 or summary.get("insufficient_evidence_count") != 1:
            raise RuntimeError(f"installed CLI analyze summary counts incorrect: {summary}")
        if summary.get("recording_level_abstention") is not False:
            raise RuntimeError(f"installed CLI analyze recording_level_abstention != False: {summary}")

        # 2. Recording-level abstention check
        meta_mismatch_file = cli_test_dir / 'meta_mismatch.json'
        meta_mismatch_dict = dict(meta_dict)
        meta_mismatch_dict["sample_rate_hz"] = 2000.0
        meta_mismatch_file.write_text(json.dumps(meta_mismatch_dict), encoding="utf-8")

        analyze_ab_cmd = [
            str(executable), 'analyze',
            '--csv', str(csv_file),
            '--metadata', str(meta_mismatch_file),
            '--config', str(cfg_file),
        ]
        analyze_ab_res = runner(analyze_ab_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if analyze_ab_res.returncode != 0:
            raise RuntimeError(f"installed CLI analyze recording abstention failed: code={analyze_ab_res.returncode}, stderr={analyze_ab_res.stderr}")
        try:
            ab_data = json.loads(analyze_ab_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI analyze abstention output is not parseable JSON: {analyze_ab_res.stdout}") from e
        if ab_data.get("status") != "INSUFFICIENT_EVIDENCE":
            raise RuntimeError(f"installed CLI analyze abstention status != INSUFFICIENT_EVIDENCE: {ab_data}")
        if ab_data.get("summary", {}).get("recording_level_abstention") is not True:
            raise RuntimeError(f"installed CLI analyze summary recording_level_abstention != True: {ab_data.get('summary')}")

        # 3. Error case check (exit code 2)
        err_cmd = [
            str(executable), 'analyze',
            '--csv', str(cli_test_dir / 'nonexistent.csv'),
            '--metadata', str(meta_file),
            '--config', str(cfg_file),
        ]
        err_res = runner(err_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if err_res.returncode != 2:
            raise RuntimeError(f"installed CLI analyze error case expected exit 2, got {err_res.returncode}, stderr={err_res.stderr}")
        if err_res.stdout != "":
            raise RuntimeError(f"installed CLI analyze error case stdout was not empty: {err_res.stdout}")
        if not err_res.stderr or "nonexistent.csv" not in err_res.stderr:
            raise RuntimeError(f"installed CLI analyze error case missing stderr diagnostic: {err_res.stderr}")

        # evaluate --suite: comprehensive end-to-end installed CLI verification

        # 1. All-match suite
        suite_pass_file = cli_test_dir / 'suite_pass.json'
        suite_pass_data = {
            "schema_version": "1.0.0",
            "suite_id": "suite_rel_pass",
            "evaluation_scope": "synthetic_behavioral_regression",
            "description": "Installed release all-match synthetic suite",
            "cases": [
                {
                    "case_id": "case_rel_match_01",
                    "description": "3-window mixed evaluation (normal, saturation, anomaly)",
                    "csv_path": "vibration.csv",
                    "metadata_path": "metadata.json",
                    "config_path": "config.json",
                    "expected_csv_sha256": csv_sha256,
                    "expected_result": {
                        "scope": "window",
                        "windows": [
                            {
                                "window_index": 0,
                                "start_sample": 0,
                                "end_sample": 5,
                                "outcome": "NORMAL",
                                "quality_status": "PASS",
                                "reason_codes": []
                            },
                            {
                                "window_index": 1,
                                "start_sample": 5,
                                "end_sample": 10,
                                "outcome": "INSUFFICIENT_EVIDENCE",
                                "quality_status": "INDETERMINATE",
                                "reason_codes": ["SENSOR_SATURATION_POSSIBLE"]
                            },
                            {
                                "window_index": 2,
                                "start_sample": 10,
                                "end_sample": 15,
                                "outcome": "ANOMALY",
                                "quality_status": "PASS",
                                "reason_codes": []
                            }
                        ]
                    }
                }
            ]
        }
        suite_pass_file.write_text(json.dumps(suite_pass_data), encoding="utf-8")

        eval_pass_cmd = [str(executable), 'evaluate', '--suite', str(suite_pass_file)]
        eval_pass_res = runner(eval_pass_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if eval_pass_res.returncode != 0:
            raise RuntimeError(f"installed CLI evaluate all-match suite failed: code={eval_pass_res.returncode}, stderr={eval_pass_res.stderr}")
        try:
            eval_pass_data = json.loads(eval_pass_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI evaluate pass suite output is not parseable JSON: {eval_pass_res.stdout}") from e

        if eval_pass_data.get("status") != "PASS":
            raise RuntimeError(f"installed CLI evaluate pass suite status != PASS: {eval_pass_data.get('status')}")
        if eval_pass_data.get("summary", {}).get("matched_cases") != 1:
            raise RuntimeError(f"installed CLI evaluate pass suite matched_cases != 1: {eval_pass_data.get('summary')}")

        # 2. Controlled mismatch suite (exit code 1)
        suite_mismatch_file = cli_test_dir / 'suite_mismatch.json'
        suite_mismatch_data = dict(suite_pass_data)
        suite_mismatch_data["suite_id"] = "suite_rel_mismatch"
        bad_case = json.loads(json.dumps(suite_pass_data["cases"][0]))
        bad_case["expected_result"]["windows"][0]["outcome"] = "ANOMALY"
        suite_mismatch_data["cases"] = [bad_case]
        suite_mismatch_file.write_text(json.dumps(suite_mismatch_data), encoding="utf-8")

        eval_mismatch_cmd = [str(executable), 'evaluate', '--suite', str(suite_mismatch_file)]
        eval_mismatch_res = runner(eval_mismatch_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if eval_mismatch_res.returncode != 1:
            raise RuntimeError(f"installed CLI evaluate mismatch suite expected exit 1, got {eval_mismatch_res.returncode}, stderr={eval_mismatch_res.stderr}")
        try:
            eval_mismatch_data = json.loads(eval_mismatch_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI evaluate mismatch suite output is not parseable JSON: {eval_mismatch_res.stdout}") from e

        if eval_mismatch_data.get("status") != "FAIL":
            raise RuntimeError(f"installed CLI evaluate mismatch suite status != FAIL: {eval_mismatch_data.get('status')}")
        if eval_mismatch_data.get("summary", {}).get("mismatched_cases") != 1:
            raise RuntimeError(f"installed CLI evaluate mismatch suite mismatched_cases != 1: {eval_mismatch_data.get('summary')}")

        # 3. Controlled error suite (exit code 1)
        suite_error_file = cli_test_dir / 'suite_error.json'
        suite_error_data = dict(suite_pass_data)
        suite_error_data["suite_id"] = "suite_rel_error"
        err_case = json.loads(json.dumps(suite_pass_data["cases"][0]))
        err_case["csv_path"] = "missing_recording.csv"
        suite_error_data["cases"] = [err_case]
        suite_error_file.write_text(json.dumps(suite_error_data), encoding="utf-8")

        eval_error_cmd = [str(executable), 'evaluate', '--suite', str(suite_error_file)]
        eval_error_res = runner(eval_error_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if eval_error_res.returncode != 1:
            raise RuntimeError(f"installed CLI evaluate error suite expected exit 1, got {eval_error_res.returncode}, stderr={eval_error_res.stderr}")
        try:
            eval_error_data = json.loads(eval_error_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI evaluate error suite output is not parseable JSON: {eval_error_res.stdout}") from e

        if eval_error_data.get("status") != "FAIL":
            raise RuntimeError(f"installed CLI evaluate error suite status != FAIL: {eval_error_data.get('status')}")
        if eval_error_data.get("summary", {}).get("error_cases") != 1:
            raise RuntimeError(f"installed CLI evaluate error suite error_cases != 1: {eval_error_data.get('summary')}")

        # 4. Controlled malformed manifest suite (exit code 2, empty stdout, stderr diagnostic)
        suite_malformed_file = cli_test_dir / 'suite_malformed_bool.json'
        suite_malformed_data = dict(suite_pass_data)
        suite_malformed_data["suite_id"] = "suite_rel_malformed"
        bool_case = json.loads(json.dumps(suite_pass_data["cases"][0]))
        bool_case["expected_result"]["windows"][0]["window_index"] = False
        suite_malformed_data["cases"] = [bool_case]
        suite_malformed_file.write_text(json.dumps(suite_malformed_data), encoding="utf-8")

        eval_malformed_cmd = [str(executable), 'evaluate', '--suite', str(suite_malformed_file)]
        eval_malformed_res = runner(eval_malformed_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if eval_malformed_res.returncode != 2:
            raise RuntimeError(f"installed CLI evaluate malformed suite expected exit 2, got {eval_malformed_res.returncode}, stderr={eval_malformed_res.stderr}")
        if eval_malformed_res.stdout != "":
            raise RuntimeError(f"installed CLI evaluate malformed suite stdout was not empty: {eval_malformed_res.stdout}")
        if not eval_malformed_res.stderr or "non-boolean" not in eval_malformed_res.stderr:
            raise RuntimeError(f"installed CLI evaluate malformed suite missing stderr diagnostic: {eval_malformed_res.stderr}")

        # 5. Reconciled mixed MATCH/MISMATCH/ERROR suite accounting
        suite_mixed_file = cli_test_dir / 'suite_mixed.json'
        c_match = json.loads(json.dumps(suite_pass_data["cases"][0]))
        c_match["case_id"] = "case_rel_match"

        c_mismatch = json.loads(json.dumps(suite_pass_data["cases"][0]))
        c_mismatch["case_id"] = "case_rel_mismatch"
        c_mismatch["expected_result"]["windows"][0]["outcome"] = "ANOMALY"

        c_err = json.loads(json.dumps(suite_pass_data["cases"][0]))
        c_err["case_id"] = "case_rel_err"
        c_err["csv_path"] = "nonexistent.csv"

        suite_mixed_data = {
            "schema_version": "1.0.0",
            "suite_id": "suite_rel_mixed",
            "evaluation_scope": "synthetic_behavioral_regression",
            "description": "Installed release mixed-case reconciled accounting suite",
            "cases": [c_match, c_mismatch, c_err],
        }
        suite_mixed_file.write_text(json.dumps(suite_mixed_data), encoding="utf-8")

        eval_mixed_cmd = [str(executable), 'evaluate', '--suite', str(suite_mixed_file)]
        eval_mixed_res = runner(eval_mixed_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if eval_mixed_res.returncode != 1:
            raise RuntimeError(f"installed CLI evaluate mixed suite expected exit 1, got {eval_mixed_res.returncode}, stderr={eval_mixed_res.stderr}")
        try:
            eval_mixed_data = json.loads(eval_mixed_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI evaluate mixed suite output is not parseable JSON: {eval_mixed_res.stdout}") from e

        if eval_mixed_data.get("status") != "FAIL":
            raise RuntimeError(f"installed CLI evaluate mixed suite status != FAIL: {eval_mixed_data.get('status')}")
        mixed_summary = eval_mixed_data.get("summary", {})
        if mixed_summary.get("total_cases") != 3:
            raise RuntimeError(f"installed CLI evaluate mixed total_cases != 3: {mixed_summary}")
        if mixed_summary.get("matched_cases") != 1 or mixed_summary.get("mismatched_cases") != 1 or mixed_summary.get("error_cases") != 1:
            raise RuntimeError(f"installed CLI evaluate mixed case counts mismatch: {mixed_summary}")

        exp_decs = mixed_summary.get("expected_decisions_summary", {})
        act_decs = mixed_summary.get("actual_decisions_summary", {})
        # Expected includes all 3 cases (3 windows each = 9 total expected)
        if exp_decs.get("total_decisions") != 9:
            raise RuntimeError(f"installed CLI evaluate mixed expected total_decisions != 9: {exp_decs}")
        # Actual includes executed decisions from match (3) and mismatch (3) = 6 total (error invents 0)
        if act_decs.get("total_decisions") != 6:
            raise RuntimeError(f"installed CLI evaluate mixed actual total_decisions != 6: {act_decs}")

        # Check each case in mixed suite contains expected_result and actual_window_boundaries
        for c in eval_mixed_data.get("cases", []):
            if "expected_result" not in c or "actual_window_boundaries" not in c:
                raise RuntimeError(f"installed CLI evaluate case missing auditability fields: {c}")

        # inspect-source: comprehensive end-to-end installed CLI verification
        mat_pass_file = cli_test_dir / 'synth_valid.mat'
        mat_comp_file = cli_test_dir / 'synth_comp.mat'
        mat_out_file = cli_test_dir / 'report_out.json'
        mat_existing_file = cli_test_dir / 'report_existing.json'
        mat_existing_file.write_text("existing content", encoding="utf-8")

        try:
            import numpy as np
            import scipy.io
            scipy.io.savemat(str(mat_pass_file), {"sig": np.array([[1.0, 2.0, 3.0]], dtype=np.float64)}, format="5", do_compression=False)
            scipy.io.savemat(str(mat_comp_file), {"sig": np.array([[1.0, 2.0, 3.0]], dtype=np.float64)}, format="5", do_compression=True)
        except Exception:
            pass

        # 1. Successful inspection (exit code 0)
        insp_cmd = [str(executable), 'inspect-source', '--mat', str(mat_pass_file), '--variable', 'sig']
        insp_res = runner(insp_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if insp_res.returncode != 0:
            raise RuntimeError(f"installed CLI inspect-source success failed: code={insp_res.returncode}, stderr={insp_res.stderr}")
        try:
            insp_data = json.loads(insp_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI inspect-source output is not parseable JSON: {insp_res.stdout}") from e
        if insp_data.get("inspection_findings", {}).get("structural_status") != "STRUCTURAL_SUCCESS":
            raise RuntimeError(f"installed CLI inspect-source status != STRUCTURAL_SUCCESS: {insp_data}")

        # 2. Successful inspection with atomic out file
        insp_out_cmd = [str(executable), 'inspect-source', '--mat', str(mat_pass_file), '--variable', 'sig', '--out', str(mat_out_file)]
        insp_out_res = runner(insp_out_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if insp_out_res.returncode != 0:
            raise RuntimeError(f"installed CLI inspect-source --out failed: code={insp_out_res.returncode}, stderr={insp_out_res.stderr}")
        if runner is subprocess.run:
            if not mat_out_file.exists():
                raise RuntimeError(f"installed CLI inspect-source --out file was not created: {mat_out_file}")
            out_file_data = json.loads(mat_out_file.read_text(encoding="utf-8"))
            if out_file_data.get("inspection_findings", {}).get("structural_status") != "STRUCTURAL_SUCCESS":
                raise RuntimeError(f"installed CLI inspect-source --out file content invalid: {out_file_data}")

        # 3. Target variable not found (exit code 1)
        insp_nf_cmd = [str(executable), 'inspect-source', '--mat', str(mat_pass_file), '--variable', 'missing_var']
        insp_nf_res = runner(insp_nf_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if insp_nf_res.returncode != 1:
            raise RuntimeError(f"installed CLI inspect-source target not found expected exit 1, got {insp_nf_res.returncode}, stderr={insp_nf_res.stderr}")
        try:
            insp_nf_data = json.loads(insp_nf_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI inspect-source target not found output is not parseable JSON: {insp_nf_res.stdout}") from e
        if insp_nf_data.get("inspection_findings", {}).get("structural_status") != "TARGET_VARIABLE_NOT_FOUND":
            raise RuntimeError(f"installed CLI inspect-source target not found status != TARGET_VARIABLE_NOT_FOUND: {insp_nf_data}")

        # 4. Compressed MAT rejected (exit code 1)
        insp_comp_cmd = [str(executable), 'inspect-source', '--mat', str(mat_comp_file), '--variable', 'sig']
        insp_comp_res = runner(insp_comp_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if insp_comp_res.returncode != 1:
            raise RuntimeError(f"installed CLI inspect-source compressed expected exit 1, got {insp_comp_res.returncode}, stderr={insp_comp_res.stderr}")
        try:
            insp_comp_data = json.loads(insp_comp_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI inspect-source compressed output is not parseable JSON: {insp_comp_res.stdout}") from e
        if insp_comp_data.get("inspection_findings", {}).get("structural_status") != "UNSUPPORTED_STRUCTURE":
            raise RuntimeError(f"installed CLI inspect-source compressed status != UNSUPPORTED_STRUCTURE: {insp_comp_data}")

        # 5. Output file already exists conflict (exit code 2)
        insp_conflict_cmd = [str(executable), 'inspect-source', '--mat', str(mat_pass_file), '--variable', 'sig', '--out', str(mat_existing_file)]
        insp_conflict_res = runner(insp_conflict_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if insp_conflict_res.returncode != 2:
            raise RuntimeError(f"installed CLI inspect-source out conflict expected exit 2, got {insp_conflict_res.returncode}, stderr={insp_conflict_res.stderr}")
        if insp_conflict_res.stdout != "":
            raise RuntimeError(f"installed CLI inspect-source out conflict stdout was not empty: {insp_conflict_res.stdout}")
        if not insp_conflict_res.stderr or "already exists" not in insp_conflict_res.stderr.lower():
            raise RuntimeError(f"installed CLI inspect-source out conflict missing stderr diagnostic: {insp_conflict_res.stderr}")

        # 6. Stage 12: validate-measurement-evidence installed CLI verification
        cli_ev_file = cli_test_dir / 'cli_evidence.json'
        cli_sig_file = cli_test_dir / 'cli_sig.json'
        cli_adm_out_file = cli_test_dir / 'cli_adm_out.json'

        # Use the authentic inspection report written in step 2 (mat_out_file)
        if mat_out_file.exists():
            insp_report_for_cli = json.loads(mat_out_file.read_text(encoding="utf-8"))
            target_sha256 = insp_report_for_cli["source_identity"]["raw_byte_sha256"]
        else:
            target_sha256 = "0" * 64
            dummy_insp = {
                "schema_version": "1.0.0",
                "inspection_id": "insp_mock_01",
                "inspected_at": "2026-11-19T12:00:00Z",
                "source_identity": {
                    "source_id": "mock_source",
                    "source_locator": "mock://source.mat",
                    "source_filename": "source.mat",
                    "raw_byte_sha256": target_sha256,
                    "raw_byte_size": 100,
                },
                "container_facts": {
                    "container_format": "MATLAB_V5",
                    "available_variables": ["sig"],
                    "target_variable": "sig",
                    "numeric_dtype": "float64",
                    "numeric_shape": [3, 1],
                    "sample_count": 3,
                    "nonfinite_sample_count": 0,
                    "observed_raw_extrema": {"min": 1.0, "max": 3.0},
                },
                "source_documentation": {
                    "sensor_physical_location": {"value": "loc", "evidence_reference": "doc"},
                    "test_rig_description": {"value": "rig", "evidence_reference": "doc"},
                    "nominal_motor_load_hp": {"value": 0.0, "evidence_reference": "doc"},
                    "nominal_motor_speed_rpm": {"value": 1797.0, "evidence_reference": "doc"},
                    "recording_condition_label": {"value": "normal", "evidence_reference": "doc", "scope": "WHOLE_RECORDING_ONLY"},
                },
                "metadata_status_ledger": {
                    "physical_acceleration_unit": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "gap"},
                    "native_sampling_rate_hz": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "gap"},
                    "acquisition_full_scale_limit": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "gap"},
                    "physical_specimen_identity": {"status": "UNKNOWN_UNPROVEN", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "gap"},
                    "legal_usage_grant": {"status": "UNKNOWN_UNSTATED", "resolved_value": None, "evidence_reference": None, "evidence_gap_statement": "gap"},
                },
                "local_annotations": {
                    "precautionary_specimen_group": "SPECIMEN_01",
                    "candidate_partition_role": "held_out_load_test",
                    "provenance_classification": "recorded",
                },
                "inspection_findings": {
                    "structural_status": "STRUCTURAL_SUCCESS",
                    "measurement_readiness": "BLOCKED_UNKNOWN_METADATA",
                    "detector_admission_eligible": False,
                    "diagnostic_notes": ["OK"],
                },
            }
            mat_out_file.write_text(json.dumps(dummy_insp, indent=2), encoding="utf-8")
            insp_report_for_cli = dummy_insp

        cli_doc_ref = {
            "document_title": "PCB 352C33 Spec Sheet",
            "locator_uri": "https://example.com/datasheets/352C33.pdf",
            "document_sha256": "05d1dc0142929ada23a90f34d0688c69472bb41c6863bde9d832112ed1576434",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Section 2.1, Table 1",
            "extracted_text_verbatim": "Sensitivity: 100 mV/g",
        }
        cli_rig_ref = {
            "document_title": "Laboratory Test Rig Documentation",
            "locator_uri": "https://example.com/docs/test_rig_specs.pdf",
            "document_sha256": "7929d21c2357a5ee8dbafcbcab13431cbc32b670b0343dedecd47c10f46d39ef",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Section 1.2",
            "extracted_text_verbatim": "2HP motor test bench with 12 kHz crystal clock.",
        }
        cli_teardown_ref = {
            "document_title": "Test Bench Teardown Logbook",
            "locator_uri": "https://example.com/logs/teardown_log_2026.pdf",
            "document_sha256": "65f9f8f982e5cbfccc25722376e4933975ee932432ba568eb540430d9a4feec4",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Logbook #4, page 12",
            "extracted_text_verbatim": "Bearing specimen verified distinct and serialized.",
        }
        cli_license_ref = {
            "document_title": "Precision Bearing Test Rig Benchmark",
            "locator_uri": "https://example.com/datasheets/benchmark.pdf",
            "document_sha256": "c06f0b4fce8b3d19420526f3f4aeaa744f6a1cf60fda5478345b0307caf24458",
            "retrieval_date": "2026-11-19",
            "passage_citation": "License Section",
            "extracted_text_verbatim": "Creative Commons Attribution CC-BY-4.0 terms.",
        }
        cli_label_ref = {
            "document_title": "Metrology Inspection Certificate",
            "locator_uri": "https://example.com/certs/metrology_01.pdf",
            "document_sha256": "883289be69ae546a5aaf08988f8524ca74ca9bf78d3f46ddad5d48010cb76610",
            "retrieval_date": "2026-11-19",
            "passage_citation": "Certificate #994",
            "extracted_text_verbatim": "Fault label independently metrologized.",
        }

        cli_ev_dict = {
            "schema_version": "1.0.0",
            "evidence_record_id": "ev_rec_cli_rel_01",
            "target_source_sha256": target_sha256,
            "target_variable_name": "sig",
            "created_at": "2026-11-19T12:00:00Z",
            "evidence_curator": {
                "organization": "Analog Devices Edge AI Laboratory",
                "curator_name": "Verification Test Lead",
                "role": "Principal Verification Engineer",
            },
            "sensor_identity": {
                "manufacturer": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "PCB Piezotronics", "confidence_assessment": "VERIFIED_PRIMARY"},
                "model": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "352C33", "confidence_assessment": "VERIFIED_PRIMARY"},
                "sensor_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "PIEZOELECTRIC_IEPE", "confidence_assessment": "VERIFIED_PRIMARY"},
                "measurement_axis": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "RADIAL_VERTICAL", "confidence_assessment": "VERIFIED_PRIMARY"},
                "sensitivity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 100.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "sensitivity_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "mV/g", "confidence_assessment": "VERIFIED_PRIMARY"},
                "measurement_range_peak_g": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 50.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "full_scale_limit_physical": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 50.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "resolution_bits": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
                "frequency_response_bandwidth_3db_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": {"low_cutoff_hz": 0.5, "high_cutoff_hz": 10000.0}, "confidence_assessment": "VERIFIED_PRIMARY"},
                "calibration_certificate": {"available": False, "certificate_number": None, "calibrated_date": None, "traceability_authority": None},
                "mounting_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "MAGNETIC_BASE", "confidence_assessment": "VERIFIED_PRIMARY"},
                "mounting_location_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "Drive end bearing housing", "confidence_assessment": "VERIFIED_PRIMARY"},
            },
            "acquisition_signal_interpretation": {
                "native_sampling_frequency_hz": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": 12000.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "sampling_clock_provenance": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": "INTERNAL_HARDWARE_CRYSTAL", "confidence_assessment": "VERIFIED_PRIMARY"},
                "raw_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "VOLTS", "confidence_assessment": "VERIFIED_PRIMARY"},
                "calibrated_acceleration_unit": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "g", "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_bit_depth": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 16, "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_input_range_volts": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "adc_scaling_factor": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "analog_front_end_gain": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 1.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "duration_seconds": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": 10.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "sample_count": 3,
                "channel_identity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": "sig", "confidence_assessment": "VERIFIED_PRIMARY"},
                "analog_filtering_description": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": "Analog low-pass filter", "confidence_assessment": "VERIFIED_PRIMARY"},
                "anti_aliasing_verified": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "saturation_detection_limit_stored_units": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_doc_ref, "resolved_value": 9.99, "confidence_assessment": "VERIFIED_PRIMARY"},
                "missing_data_sample_count": 0,
                "nonfinite_sample_count": 0,
            },
            "experiment_specimen_identity": {
                "physical_specimen_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_teardown_ref, "resolved_value": "SPECIMEN_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "specimen_distinctness_proven": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_teardown_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "machine_asset_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": "TEST_RIG_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "equipment_type": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": "2HP Induction Motor", "confidence_assessment": "VERIFIED_PRIMARY"},
                "rotational_speed_rpm": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": 1797.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "nominal_load_hp": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "load_percentage": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": 0.0, "confidence_assessment": "VERIFIED_PRIMARY"},
                "operating_regime_classification": "STEADY_STATE_STATIC_LOAD",
                "measurement_session_id": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": "SESSION_01", "confidence_assessment": "VERIFIED_PRIMARY"},
                "repeated_recording_dependency_flag": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_rig_ref, "resolved_value": False, "confidence_assessment": "VERIFIED_PRIMARY"},
                "related_experimental_recordings": [],
                "fault_condition": {
                    "fault_category": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_label_ref, "resolved_value": "BASELINE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "fault_dimension_label": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_label_ref, "resolved_value": "NONE_HEALTHY", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "fault_provenance_method": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_label_ref, "resolved_value": "NATURAL_IN_SERVICE_FAILURE", "confidence_assessment": "VERIFIED_PRIMARY"},
                    "ground_truth_label_authority": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_label_ref, "resolved_value": "Independent Metrology Team", "confidence_assessment": "VERIFIED_PRIMARY"},
                },
            },
            "dataset_legal_provenance": {
                "dataset_name": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": "Precision Benchmark", "confidence_assessment": "VERIFIED_PRIMARY"},
                "publisher_entity": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": "Analog Devices Edge AI Laboratory", "confidence_assessment": "VERIFIED_PRIMARY"},
                "primary_documentation_uris": ["https://example.com/datasheets/benchmark.pdf"],
                "dataset_version": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": "1.0.0", "confidence_assessment": "VERIFIED_PRIMARY"},
                "acquisition_date": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": "2026-11-19", "confidence_assessment": "VERIFIED_PRIMARY"},
                "source_raw_byte_sha256": target_sha256,
                "legal_usage_license": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": "CREATIVE_COMMONS_ATTRIBUTION_CC_BY_4_0", "confidence_assessment": "VERIFIED_PRIMARY"},
                "affirmative_commercial_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "affirmative_research_grant": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "redistribution_permitted": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "attribution_required": {"status": "DOCUMENTED_KNOWN", "evidence_level": 2, "evidence_reference": cli_license_ref, "resolved_value": True, "confidence_assessment": "VERIFIED_PRIMARY"},
                "attribution_text": "Attribution required under CC-BY-4.0 terms.",
                "transformation_derivation_history": [],
            },
        }
        cli_ev_file.write_text(json.dumps(cli_ev_dict, indent=2), encoding="utf-8")

        cli_sig_dict = {
            "schema_version": "1.0.0",
            "source_sha256": target_sha256,
            "target_channel": "sig",
            "sample_count": 3,
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
                "source_sha256": target_sha256,
                "target_channel": "sig",
                "sample_count": 3,
                "rail_tolerance_fraction": 0.01,
                "analyzed_at": "2026-11-20T00:00:00Z",
            },
        }
        cli_sig_file.write_text(json.dumps(cli_sig_dict, indent=2), encoding="utf-8")

        cli_source_artifact = {
            "schema_version": "1.0.0",
            "artifact_format": "ADI_FICTIONAL_SOURCE_V1",
            "units": "g",
            "full_scale_limit": 50.0,
            "channels": {"sig": [1.0, 1.0, 1.0]},
        }
        cli_source_file = cli_test_dir / "cli_source.json"
        cli_source_bytes = json.dumps(
            cli_source_artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        cli_source_file.write_bytes(cli_source_bytes)
        cli_source_sha = hashlib.sha256(cli_source_bytes).hexdigest()
        insp_report_for_cli["source_identity"]["raw_byte_sha256"] = cli_source_sha
        mat_out_file.write_text(json.dumps(insp_report_for_cli, indent=2), encoding="utf-8")
        cli_ev_dict["target_source_sha256"] = cli_source_sha
        cli_ev_dict["dataset_legal_provenance"]["source_raw_byte_sha256"] = cli_source_sha
        cli_ev_file.write_text(json.dumps(cli_ev_dict, indent=2), encoding="utf-8")
        cli_sig_dict["source_sha256"] = cli_source_sha
        cli_sig_dict["data_quality_provenance"]["source_sha256"] = cli_source_sha
        cli_sig_dict["data_quality_provenance"]["rail_tolerance_fraction"] = 0.001
        cli_sig_file.write_text(json.dumps(cli_sig_dict, indent=2), encoding="utf-8")

        # 6a. validate-measurement-evidence success (exit code 0)
        val_cmd = [str(executable), 'validate-measurement-evidence', '--evidence', str(cli_ev_file)]
        val_res = runner(val_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if val_res.returncode != 0:
            raise RuntimeError(f"installed CLI validate-measurement-evidence failed: code={val_res.returncode}, stderr={val_res.stderr}")
        try:
            val_data = json.loads(val_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI validate-measurement-evidence output is not parseable JSON: {val_res.stdout}") from e
        if val_data.get("status") != "VALID":
            raise RuntimeError(f"installed CLI validate-measurement-evidence status != VALID: {val_data}")

        # 6b. simulate-measurement-admission success (exit code 0)
        sim_cmd = [
            str(executable), 'simulate-measurement-admission',
            '--evidence', str(cli_ev_file),
            '--inspection', str(mat_out_file),
            '--signal-integrity', str(cli_sig_file),
            '--source-artifact', str(cli_source_file),
            '--context', 'ACADEMIC_RESEARCH_NONCOMMERCIAL',
            '--out', str(cli_adm_out_file)
        ]
        sim_res = runner(sim_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if sim_res.returncode != 0:
            raise RuntimeError(f"installed CLI simulate-measurement-admission failed: code={sim_res.returncode}, stderr={sim_res.stderr}")
        try:
            sim_data = json.loads(sim_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI simulate-measurement-admission output is not parseable JSON: {sim_res.stdout}") from e
        if sim_data.get("measurement_admission_status") != "ADMITTED_FOR_MEASUREMENT":
            raise RuntimeError(f"installed CLI simulate-measurement-admission status != ADMITTED_FOR_MEASUREMENT: {sim_data}")
        if sim_data.get("evaluation_mode") != "FICTIONAL_TEST_ONLY":
            raise RuntimeError(f"installed CLI simulate-measurement-admission evaluation_mode != FICTIONAL_TEST_ONLY: {sim_data}")
        if sim_data.get("operational_execution_authorized") is not False:
            raise RuntimeError(f"installed CLI simulate-measurement-admission operational_execution_authorized != False: {sim_data}")
        if runner is subprocess.run:
            if not cli_adm_out_file.exists():
                raise RuntimeError(f"installed CLI simulate-measurement-admission --out file was not created: {cli_adm_out_file}")

        # 6c. simulate-measurement-admission overwrite conflict (exit code 2)
        sim_conflict_cmd = [
            str(executable), 'simulate-measurement-admission',
            '--evidence', str(cli_ev_file),
            '--inspection', str(mat_out_file),
            '--signal-integrity', str(cli_sig_file),
            '--source-artifact', str(cli_source_file),
            '--out', str(cli_adm_out_file)
        ]
        sim_conflict_res = runner(sim_conflict_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if sim_conflict_res.returncode != 2:
            raise RuntimeError(f"installed CLI simulate-measurement-admission out conflict expected exit 2, got {sim_conflict_res.returncode}, stderr={sim_conflict_res.stderr}")
        if not sim_conflict_res.stderr or "already exists" not in sim_conflict_res.stderr.lower():
            raise RuntimeError(f"installed CLI simulate-measurement-admission out conflict missing stderr diagnostic: {sim_conflict_res.stderr}")

        # 6d. simulate-measurement-admission blocked case (exit code 1)
        sim_blocked_cmd = [
            str(executable), 'simulate-measurement-admission',
            '--evidence', str(cli_ev_file),
            '--inspection', str(mat_out_file),
            '--source-artifact', str(cli_source_file),
        ]
        sim_blocked_res = runner(sim_blocked_cmd, capture_output=True, text=True, env=exec_env, cwd=work)
        if sim_blocked_res.returncode != 1:
            raise RuntimeError(f"installed CLI simulate-measurement-admission blocked expected exit 1, got {sim_blocked_res.returncode}, stderr={sim_blocked_res.stderr}")
        try:
            blocked_data = json.loads(sim_blocked_res.stdout or '')
        except Exception as e:
            raise RuntimeError(f"installed CLI simulate-measurement-admission blocked output is not parseable JSON: {sim_blocked_res.stdout}") from e
        if blocked_data.get("measurement_admission_status") != "BLOCKED_SIGNAL_INTEGRITY":
            raise RuntimeError(f"installed CLI simulate-measurement-admission blocked status != BLOCKED_SIGNAL_INTEGRITY: {blocked_data}")

        # 9. Complete release report
        report = {
            'packaging_verification': 'PASS',
            'relocated_source_build': 'PASS',
            'source_removed_before_execution': True,
            'dependency_resolution': 'PASS',
            'dependency_check': 'PASS',
            'installed_packages': ['adi-common', 'adi-runtime', 'adi-app'],
            'installed_adi_common_contracts_check': {
                'status': 'PASS',
                'source_in_site_packages': True,
                'packaged_schemas_loaded': [
                    'vibration_recording.schema.v1.json',
                    'anomaly_decision.schema.v1.json',
                    'exploratory_source_inspection.schema.v1.json',
                    'calibration_measurement_evidence.schema.v1.json',
                    'measurement_admission_decision.schema.v1.json'
                ],
                'vibration_recording_validation': 'PASS',
                'anomaly_decision_validation': 'PASS',
                'exploratory_source_inspection_validation': 'PASS',
                'calibration_measurement_evidence_validation': 'PASS',
                'measurement_admission_decision_validation': 'PASS',
                'fail_closed_empty_registry_verified': 'PASS',
                'fictional_test_registry_admission_verified': 'PASS',
                'operational_admission_disabled_guard': 'PASS',
                'exact_sha256_newline_rejection': 'PASS',
                'oversized_integer_binary64_domain_rejection': 'PASS',
                'offline_socket_guard': 'in_process_python_socket_monkeypatch_only'
            },
            'installed_adi_runtime_features_check': {
                'status': 'PASS',
                'source_in_site_packages': True,
                'opposite_sign_extremes_mean_zero': 'PASS',
                'invalid_input_rejection': 'PASS',
                'analytical_reference_verified': 'PASS',
                'multi_channel_separation_verified': 'PASS',
                'trailing_samples_accounting_verified': 'PASS',
                'offline_socket_guard': 'in_process_python_socket_monkeypatch_only'
            },
            'installed_adi_app_ingestion_check': {
                'status': 'PASS',
                'source_in_site_packages': True,
                'valid_csv_ingestion_verified': 'PASS',
                'malformed_csv_rejection_verified': 'PASS',
                'malformed_quote_rejection_verified': 'PASS',
                'oversized_metadata_rejection_verified': 'PASS',
                'runtime_feature_pipeline_verified': 'PASS',
                'offline_socket_guard': 'in_process_python_socket_monkeypatch_only'
            },
            'installed_adi_runtime_threshold_check': {
                'status': 'PASS',
                'source_in_site_packages': True,
                'normal_window_verified': 'PASS',
                'anomaly_window_verified': 'PASS',
                'saturation_abstention_verified': 'PASS',
                'envelope_mismatch_abstention_verified': 'PASS',
                'outcome_flip_distinct_decision_ids_verified': 'PASS',
                'boolean_alias_bypass_rejection_verified': 'PASS',
                'large_int_distinct_decision_ids_verified': 'PASS',
                'traceability_and_contract_validation': 'PASS',
                'offline_socket_guard': 'in_process_python_socket_monkeypatch_only'
            },
            'installed_adi_app_source_inspection_check': {
                'status': 'PASS',
                'source_in_site_packages': True,
                'synthetic_success_verified': 'PASS',
                'target_not_found_verified': 'PASS',
                'nonexistent_file_rejection_verified': 'PASS',
                'offline_socket_guard': 'in_process_python_socket_monkeypatch_only'
            },
            'installed_cli_executable_check': {
                'status': 'PASS',
                'executable_path': str(executable),
                'demo_scaffold_verified': 'PASS',
                'benchmark_not_implemented_json_verified': 'PASS',
                'evaluate_not_implemented_json_verified': 'PASS',
                'analyze_all_three_outcomes_verified': 'PASS',
                'analyze_recording_abstention_verified': 'PASS',
                'analyze_error_exit_2_verified': 'PASS',
                'analyze_source_hashing_verified': 'PASS',
                'evaluate_suite_all_pass_verified': 'PASS',
                'evaluate_suite_mismatch_fails_verified': 'PASS',
                'evaluate_suite_error_fails_verified': 'PASS',
                'evaluate_suite_malformed_manifest_exit_2_verified': 'PASS',
                'evaluate_suite_mixed_accounting_verified': 'PASS',
                'inspect_source_success_exit_0_verified': 'PASS',
                'inspect_source_out_file_verified': 'PASS',
                'inspect_source_target_not_found_exit_1_verified': 'PASS',
                'inspect_source_compressed_exit_1_verified': 'PASS',
                'inspect_source_out_conflict_exit_2_verified': 'PASS',
                'validate_measurement_evidence_exit_0_verified': 'PASS',
                'simulate_measurement_admission_exit_0_verified': 'PASS',
                'simulate_measurement_admission_out_file_verified': 'PASS',
                'simulate_measurement_admission_out_conflict_exit_2_verified': 'PASS',
                'simulate_measurement_admission_blocked_exit_1_verified': 'PASS'
            },
            'functional_validation': {
                'local_analysis_cli': 'IMPLEMENTED_AND_VERIFIED',
                'synthetic_scenario_evaluation': 'IMPLEMENTED_AND_VERIFIED',
                'local_source_inspection': 'IMPLEMENTED_AND_VERIFIED',
                'evidence_validator_and_admission_gate': 'IMPLEMENTED_AND_VERIFIED',
                'learned_neural_model_inference': 'NOT_IMPLEMENTED',
                'latency_energy_benchmarking': 'NOT_IMPLEMENTED',
                'physical_hardware_deployment': 'NOT_IMPLEMENTED'
            },
            'functional_validation_reason': 'Local CSV analysis, synthetic scenario evaluation, uncompressed Level 5 source inspection, and Stage 12 deterministic evidence validation / admission decision engine verified; learned neural inference, hardware deployment, and physical benchmarking remain future stages.'
        }
        return 0, report


def main(runner=subprocess.run):
    exit_code, report = run_release_verification(runner=runner)
    print(json.dumps(report, indent=2))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
