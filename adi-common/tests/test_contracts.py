"""Comprehensive contract and semantic validation tests for ADI Common.

Covers schema packaging, vibration recording invariants, anomaly decision invariants,
finite binary64 numeric domain, exact SHA-256 digest syntax, boundary conditions,
failure modes, input immutability, and dependency fail-closed enforcement.
"""

import copy
import math
from pathlib import Path
import subprocess
import sys
import unittest

import adi_common
from adi_common import (
    ContractValidationError,
    load_schema,
    validate_anomaly_decision,
    validate_vibration_recording,
)


class TestContracts(unittest.TestCase):
    """Test suite for ADI Common contracts and validators."""

    def setUp(self):
        self.valid_recording_1ch = {
            "schema_version": "1.0.0",
            "recording_id": "rec-2026-001",
            "machine_id": "pump-west-04",
            "session_id": "sess-shift1-002",
            "provenance": "synthetic",
            "sample_rate_hz": 4000.0,
            "acceleration_unit": "g",
            "channels": ["x"],
            "samples": [
                [0.012],
                [0.015],
                [-0.008],
                [0.021],
            ],
            "source_id": "synth-gen-v1-sine-sweep",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        }

        self.valid_recording_3ch = {
            "schema_version": "1.0.0",
            "recording_id": "rec-2026-002",
            "machine_id": "cnc-spindle-01",
            "session_id": "sess-baseline-001",
            "provenance": "recorded",
            "sample_rate_hz": 8000.0,
            "acceleration_unit": "m/s^2",
            "channels": ["x", "y", "z"],
            "samples": [
                [0.12, -0.05, 9.81],
                [0.15, -0.04, 9.80],
                [0.11, -0.06, 9.82],
            ],
            "source_id": "raw-sensor-adxl355-capture-001",
            "source_sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
        }

        self.valid_decision_normal = {
            "schema_version": "1.0.0",
            "decision_id": "dec-2026-1001",
            "recording_ref": "rec-2026-001",
            "detector_id": "iso-10816-spectral-energy",
            "detector_version": "0.1.0",
            "outcome": "NORMAL",
            "execution_environment": "HOST_CPU",
            "quality_status": "PASS",
            "reason_codes": [],
            "score": 0.42,
            "threshold": 0.85,
        }

        self.valid_decision_anomaly = {
            "schema_version": "1.0.0",
            "decision_id": "dec-2026-1002",
            "recording_ref": "rec-2026-002",
            "detector_id": "iso-10816-spectral-energy",
            "detector_version": "0.1.0",
            "outcome": "ANOMALY",
            "execution_environment": "HOST_CPU",
            "quality_status": "PASS",
            "reason_codes": ["BEARING_INNER_RACE_PEAK"],
            "score": 1.25,
            "threshold": 0.85,
        }

        self.valid_decision_insufficient = {
            "schema_version": "1.0.0",
            "decision_id": "dec-2026-1003",
            "recording_ref": "rec-2026-001",
            "detector_id": "iso-10816-spectral-energy",
            "detector_version": "0.1.0",
            "outcome": "INSUFFICIENT_EVIDENCE",
            "execution_environment": "HOST_CPU",
            "quality_status": "INDETERMINATE",
            "reason_codes": ["SENSOR_CLIPPING_DETECTED", "RECORDING_DURATION_UNDER_100MS"],
            "score": None,
            "threshold": None,
        }

    # -------------------------------------------------------------------------
    # Baseline Package Metadata & Packaging Tests
    # -------------------------------------------------------------------------

    def test_version_present(self):
        """Preserve existing test asserting package version."""
        self.assertEqual(adi_common.__version__, "0.1.0")

    def test_load_schema_package_resources(self):
        """TC-PKG-01: Verify schemas load via importlib.resources from package."""
        rec_schema = load_schema("vibration_recording.schema.v1.json")
        self.assertEqual(rec_schema.get("title"), "VibrationRecordingV1")
        self.assertEqual(rec_schema.get("required"), [
            "schema_version",
            "recording_id",
            "machine_id",
            "session_id",
            "provenance",
            "sample_rate_hz",
            "acceleration_unit",
            "channels",
            "samples",
            "source_id",
            "source_sha256",
        ])

        dec_schema = load_schema("anomaly_decision.schema.v1.json")
        self.assertEqual(dec_schema.get("title"), "AnomalyDecisionV1")
        self.assertEqual(dec_schema.get("required"), [
            "schema_version",
            "decision_id",
            "recording_ref",
            "detector_id",
            "detector_version",
            "outcome",
            "execution_environment",
            "quality_status",
            "reason_codes",
            "score",
            "threshold",
        ])

    def test_load_schema_missing_file_raises(self):
        """Verify load_schema raises FileNotFoundError for non-existent schemas."""
        with self.assertRaises(FileNotFoundError):
            load_schema("nonexistent_schema.json")

    def test_duplicate_schema_copies_synchronized(self):
        """Verify that duplicate schema copies in repo root and src remain byte-synchronized."""
        repo_root = Path(__file__).resolve().parents[2]
        rec_src = repo_root / "adi-common/src/adi_common/schemas/vibration_recording.schema.v1.json"
        rec_root = repo_root / "adi-common/schemas/vibration_recording.schema.v1.json"
        dec_src = repo_root / "adi-common/src/adi_common/schemas/anomaly_decision.schema.v1.json"
        dec_root = repo_root / "adi-common/schemas/anomaly_decision.schema.v1.json"

        self.assertEqual(rec_src.read_bytes(), rec_root.read_bytes(), "Recording schema copies divergence")
        self.assertEqual(dec_src.read_bytes(), dec_root.read_bytes(), "Decision schema copies divergence")

    def test_missing_jsonschema_dependency_fails_closed(self):
        """TC-DEP-01: Verify missing jsonschema dependency causes clear ImportError, not degraded validation."""
        code = """
import sys
sys.modules['jsonschema'] = None
try:
    import adi_common.validators
    raise AssertionError("Importing adi_common.validators without jsonschema must fail")
except (ImportError, ModuleNotFoundError) as exc:
    print("SUCCESS: Failed closed when required dependency 'jsonschema' is missing")
"""
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Subprocess check failed: stdout={proc.stdout}, stderr={proc.stderr}")
        self.assertIn("SUCCESS: Failed closed", proc.stdout)

    # -------------------------------------------------------------------------
    # Vibration Recording Tests
    # -------------------------------------------------------------------------

    def test_vibration_recording_valid_1ch(self):
        """TC-REC-01: Valid single-channel recording passes validation."""
        result = validate_vibration_recording(self.valid_recording_1ch)
        self.assertEqual(result["recording_id"], "rec-2026-001")
        self.assertEqual(result["channels"], ["x"])
        self.assertEqual(result["acceleration_unit"], "g")

    def test_vibration_recording_valid_3ch(self):
        """TC-REC-02: Valid 3-channel recording with m/s^2 passes validation."""
        result = validate_vibration_recording(self.valid_recording_3ch)
        self.assertEqual(result["recording_id"], "rec-2026-002")
        self.assertEqual(result["channels"], ["x", "y", "z"])
        self.assertEqual(result["acceleration_unit"], "m/s^2")

    def test_vibration_recording_unsupported_schema_version(self):
        """TC-REC-03: Unsupported schema version is rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        payload["schema_version"] = "2.0.0"
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("Unsupported schema version", str(ctx.exception))

    def test_vibration_recording_empty_identifiers(self):
        """TC-REC-04: Empty string identifiers are rejected."""
        for field in ("recording_id", "machine_id", "session_id", "source_id"):
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload[field] = ""
            with self.assertRaises(ContractValidationError) as ctx:
                validate_vibration_recording(payload)
            self.assertIn(f"Field '{field}' must be a nonempty string", str(ctx.exception))

            payload[field] = "   "
            with self.assertRaises(ContractValidationError):
                validate_vibration_recording(payload)

    def test_vibration_recording_invalid_provenance(self):
        """TC-REC-05: Non-whitelisted provenance classification is rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        payload["provenance"] = "streamed"
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("provenance", str(ctx.exception))

    def test_vibration_recording_non_positive_sample_rate(self):
        """TC-REC-06: Zero and negative sample rates are rejected."""
        for bad_rate in (0, 0.0, -100.0, -0.001):
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload["sample_rate_hz"] = bad_rate
            with self.assertRaises(ContractValidationError) as ctx:
                validate_vibration_recording(payload)
            self.assertIn("positive", str(ctx.exception))

    def test_vibration_recording_non_finite_sample_rate(self):
        """TC-REC-07: NaN and Infinite sample rates are rejected."""
        for bad_rate in (float("nan"), float("inf"), float("-inf")):
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload["sample_rate_hz"] = bad_rate
            with self.assertRaises(ContractValidationError) as ctx:
                validate_vibration_recording(payload)
            self.assertIn("finite", str(ctx.exception))

    def test_vibration_recording_oversized_integer_sample_rate(self):
        """TC-REC-07b: Oversized positive and negative integers in sample_rate_hz raise ContractValidationError, not OverflowError."""
        for large_val in (10**400, -(10**400)):
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload["sample_rate_hz"] = large_val
            with self.assertRaises(ContractValidationError) as ctx:
                validate_vibration_recording(payload)
            self.assertEqual(ctx.exception.field, "sample_rate_hz")
            self.assertIn("binary64 numeric domain", str(ctx.exception))

    def test_vibration_recording_boolean_sample_rate(self):
        """TC-REC-08: Boolean values masquerading as numeric sample rate are rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        payload["sample_rate_hz"] = True
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("numeric", str(ctx.exception))

    def test_vibration_recording_unsupported_unit(self):
        """TC-REC-09: Non-whitelisted acceleration units are rejected."""
        for bad_unit in ("cm/s^2", "m/s", "G", "gal", ""):
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload["acceleration_unit"] = bad_unit
            with self.assertRaises(ContractValidationError) as ctx:
                validate_vibration_recording(payload)
            self.assertIn("acceleration_unit", str(ctx.exception))

    def test_vibration_recording_duplicate_channels(self):
        """TC-REC-10: Duplicate channel entries are rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        payload["channels"] = ["x", "x"]
        payload["samples"] = [[0.1, 0.2]]
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("Duplicate channel", str(ctx.exception))

    def test_vibration_recording_invalid_channel_name(self):
        """TC-REC-11: Channel names outside ('x', 'y', 'z') are rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        payload["channels"] = ["w"]
        payload["samples"] = [[0.1]]
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("channels", str(ctx.exception).lower())

    def test_vibration_recording_empty_samples(self):
        """TC-REC-12: Empty sample matrices are rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        payload["samples"] = []
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("nonempty", str(ctx.exception))

    def test_vibration_recording_ragged_samples(self):
        """TC-REC-13: Ragged sample matrices with unequal row lengths are rejected."""
        payload = copy.deepcopy(self.valid_recording_3ch)
        payload["samples"] = [
            [0.1, 0.2, 0.3],
            [0.4, 0.5],  # Missing 3rd channel
            [0.6, 0.7, 0.8],
        ]
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("does not match channel count", str(ctx.exception))

    def test_vibration_recording_channel_count_mismatch(self):
        """TC-REC-14: Samples whose row length does not match channel count are rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)  # channels: ['x'] (len 1)
        payload["samples"] = [[0.1, 0.2], [0.3, 0.4]]    # rows length 2
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("does not match channel count", str(ctx.exception))

    def test_vibration_recording_boolean_sample_value(self):
        """TC-REC-15: Booleans masquerading as numeric sample values are rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        payload["samples"] = [[True], [0.02]]
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("Boolean value not allowed", str(ctx.exception))

    def test_vibration_recording_non_finite_sample_value(self):
        """TC-REC-16: NaN and Infinite sample values are rejected."""
        for bad_val in (float("nan"), float("inf"), float("-inf")):
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload["samples"] = [[bad_val], [0.02]]
            with self.assertRaises(ContractValidationError) as ctx:
                validate_vibration_recording(payload)
            self.assertIn("finite", str(ctx.exception))

    def test_vibration_recording_oversized_integer_sample_value(self):
        """TC-REC-16b: Oversized positive and negative integers in samples raise ContractValidationError, not OverflowError."""
        for large_val in (10**400, -(10**400)):
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload["samples"] = [[large_val], [0.02]]
            with self.assertRaises(ContractValidationError) as ctx:
                validate_vibration_recording(payload)
            self.assertEqual(ctx.exception.field, "samples[0][0]")
            self.assertIn("binary64 numeric domain", str(ctx.exception))

    def test_vibration_recording_exact_sha256_rejection(self):
        """TC-REC-17: Malformed, whitespace-padded, newline-terminated, or invalid length SHA-256 strings are rejected."""
        bad_hashes = [
            "a" * 64 + "\n",                        # Reproducer: 64 hex chars with trailing newline (len 65)
            "\n" + "a" * 64,                        # Leading newline
            "a" * 32 + "\n" + "a" * 31,             # Embedded newline
            " " + "a" * 63,                         # Leading space
            "a" * 63 + " ",                         # Trailing space
            "a" * 63,                               # Too short (63 chars)
            "a" * 65,                               # Too long (65 chars)
            "g" * 64,                               # Non-hex character 'g'
            "Z" * 64,                               # Non-hex character 'Z'
            "",                                     # Empty string
            123456,                                 # Non-string integer
            None,                                   # None
        ]
        for bad_sha in bad_hashes:
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload["source_sha256"] = bad_sha
            with self.assertRaises(ContractValidationError) as ctx:
                validate_vibration_recording(payload)
            self.assertIn("source_sha256", str(ctx.exception).lower())

    def test_vibration_recording_exact_sha256_acceptance(self):
        """TC-REC-17b: Exactly 64 uppercase, lowercase, or mixed hex characters are accepted."""
        good_hashes = [
            "a" * 64,
            "A" * 64,
            "0123456789abcdef" * 4,
            "0123456789ABCDEF" * 4,
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ]
        for good_sha in good_hashes:
            payload = copy.deepcopy(self.valid_recording_1ch)
            payload["source_sha256"] = good_sha
            result = validate_vibration_recording(payload)
            self.assertEqual(result["source_sha256"], good_sha)

    def test_vibration_recording_unknown_fields(self):
        """TC-REC-18: Unknown/extraneous fields are strictly rejected."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        payload["unsupported_field"] = "unexpected_data"
        with self.assertRaises(ContractValidationError) as ctx:
            validate_vibration_recording(payload)
        self.assertIn("unknown_fields", str(ctx.exception))

    def test_vibration_recording_immutability(self):
        """TC-REC-19: Validation does not mutate the input payload."""
        payload = copy.deepcopy(self.valid_recording_1ch)
        original = copy.deepcopy(payload)
        validate_vibration_recording(payload)
        self.assertEqual(payload, original)

    # -------------------------------------------------------------------------
    # Anomaly Decision Tests
    # -------------------------------------------------------------------------

    def test_anomaly_decision_valid_normal(self):
        """TC-DEC-01: Valid NORMAL decision with score < threshold passes."""
        result = validate_anomaly_decision(self.valid_decision_normal)
        self.assertEqual(result["outcome"], "NORMAL")
        self.assertEqual(result["quality_status"], "PASS")
        self.assertLess(result["score"], result["threshold"])

    def test_anomaly_decision_valid_anomaly(self):
        """TC-DEC-02: Valid ANOMALY decision with score > threshold passes."""
        result = validate_anomaly_decision(self.valid_decision_anomaly)
        self.assertEqual(result["outcome"], "ANOMALY")
        self.assertEqual(result["quality_status"], "PASS")
        self.assertGreater(result["score"], result["threshold"])

    def test_anomaly_decision_boundary_equality(self):
        """TC-DEC-03: Boundary condition score == threshold is valid ANOMALY."""
        payload = copy.deepcopy(self.valid_decision_anomaly)
        payload["score"] = 0.85
        payload["threshold"] = 0.85
        result = validate_anomaly_decision(payload)
        self.assertEqual(result["outcome"], "ANOMALY")
        self.assertEqual(result["score"], result["threshold"])

    def test_anomaly_decision_valid_insufficient_evidence(self):
        """TC-DEC-04: Valid INSUFFICIENT_EVIDENCE with null score/threshold passes."""
        result = validate_anomaly_decision(self.valid_decision_insufficient)
        self.assertEqual(result["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertIsNone(result["score"])
        self.assertIsNone(result["threshold"])
        self.assertGreaterEqual(len(result["reason_codes"]), 1)

    def test_anomaly_decision_unsupported_schema_version(self):
        """TC-DEC-05: Unsupported schema version is rejected."""
        payload = copy.deepcopy(self.valid_decision_normal)
        payload["schema_version"] = "2.0.0"
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("Unsupported schema version", str(ctx.exception))

    def test_anomaly_decision_empty_identifiers(self):
        """TC-DEC-06: Empty string identifiers are rejected."""
        for field in ("decision_id", "recording_ref", "detector_id", "detector_version"):
            payload = copy.deepcopy(self.valid_decision_normal)
            payload[field] = ""
            with self.assertRaises(ContractValidationError) as ctx:
                validate_anomaly_decision(payload)
            self.assertIn(f"Field '{field}' must be a nonempty string", str(ctx.exception))

    def test_anomaly_decision_contradictory_normal(self):
        """TC-DEC-07: Contradictory NORMAL with score >= threshold is rejected."""
        payload = copy.deepcopy(self.valid_decision_normal)
        payload["score"] = 0.95
        payload["threshold"] = 0.85
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("Contradictory decision", str(ctx.exception))

        # Also test equality for NORMAL (score == threshold is ANOMALY, not NORMAL)
        payload["score"] = 0.85
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("Contradictory decision", str(ctx.exception))

    def test_anomaly_decision_contradictory_anomaly(self):
        """TC-DEC-08: Contradictory ANOMALY with score < threshold is rejected."""
        payload = copy.deepcopy(self.valid_decision_anomaly)
        payload["score"] = 0.50
        payload["threshold"] = 0.85
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("Contradictory decision", str(ctx.exception))

    def test_anomaly_decision_non_pass_quality_status_for_decisions(self):
        """TC-DEC-09: NORMAL and ANOMALY with non-PASS quality status are rejected."""
        for bad_qs in ("FAIL", "INDETERMINATE"):
            # NORMAL
            payload_n = copy.deepcopy(self.valid_decision_normal)
            payload_n["quality_status"] = bad_qs
            with self.assertRaises(ContractValidationError) as ctx:
                validate_anomaly_decision(payload_n)
            self.assertIn("requires quality_status 'PASS'", str(ctx.exception))

            # ANOMALY
            payload_a = copy.deepcopy(self.valid_decision_anomaly)
            payload_a["quality_status"] = bad_qs
            with self.assertRaises(ContractValidationError) as ctx:
                validate_anomaly_decision(payload_a)
            self.assertIn("requires quality_status 'PASS'", str(ctx.exception))

    def test_anomaly_decision_unmeasured_hardware_target(self):
        """TC-DEC-10: Claiming unmeasured hardware targets is rejected."""
        for bad_env in ("MAX78000", "MAX78002", "SHARC", "EMBEDDED_ARM", "NPU"):
            payload = copy.deepcopy(self.valid_decision_normal)
            payload["execution_environment"] = bad_env
            with self.assertRaises(ContractValidationError) as ctx:
                validate_anomaly_decision(payload)
            self.assertIn("Unsupported or unmeasured execution environment", str(ctx.exception))

    def test_anomaly_decision_insufficient_evidence_with_scores(self):
        """TC-DEC-11: INSUFFICIENT_EVIDENCE with non-null score or threshold is rejected."""
        payload = copy.deepcopy(self.valid_decision_insufficient)
        payload["score"] = 0.5
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("requires score to be null/None", str(ctx.exception))

        payload = copy.deepcopy(self.valid_decision_insufficient)
        payload["threshold"] = 0.8
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("requires threshold to be null/None", str(ctx.exception))

    def test_anomaly_decision_insufficient_evidence_without_reasons(self):
        """TC-DEC-12: INSUFFICIENT_EVIDENCE with empty reason_codes list is rejected."""
        payload = copy.deepcopy(self.valid_decision_insufficient)
        payload["reason_codes"] = []
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("requires at least one explanatory reason code", str(ctx.exception))

    def test_anomaly_decision_boolean_score_or_threshold(self):
        """TC-DEC-13: Boolean values masquerading as numeric score/threshold are rejected."""
        payload = copy.deepcopy(self.valid_decision_normal)
        payload["score"] = True
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("numeric", str(ctx.exception))

        payload = copy.deepcopy(self.valid_decision_normal)
        payload["threshold"] = False
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("numeric", str(ctx.exception))

    def test_anomaly_decision_non_finite_score_or_threshold(self):
        """TC-DEC-14: NaN and Infinite scores or thresholds are rejected."""
        for bad_val in (float("nan"), float("inf"), float("-inf")):
            payload = copy.deepcopy(self.valid_decision_normal)
            payload["score"] = bad_val
            with self.assertRaises(ContractValidationError) as ctx:
                validate_anomaly_decision(payload)
            self.assertIn("finite", str(ctx.exception))

            payload = copy.deepcopy(self.valid_decision_normal)
            payload["threshold"] = bad_val
            with self.assertRaises(ContractValidationError) as ctx:
                validate_anomaly_decision(payload)
            self.assertIn("finite", str(ctx.exception))

    def test_anomaly_decision_oversized_integer_score_and_threshold(self):
        """TC-DEC-14b: Oversized positive and negative integers in score and threshold raise ContractValidationError, not OverflowError."""
        for large_val in (10**400, -(10**400)):
            # score
            payload = copy.deepcopy(self.valid_decision_normal)
            payload["score"] = large_val
            with self.assertRaises(ContractValidationError) as ctx:
                validate_anomaly_decision(payload)
            self.assertEqual(ctx.exception.field, "score")
            self.assertIn("binary64 numeric domain", str(ctx.exception))

            # threshold
            payload = copy.deepcopy(self.valid_decision_normal)
            payload["threshold"] = large_val
            with self.assertRaises(ContractValidationError) as ctx:
                validate_anomaly_decision(payload)
            self.assertEqual(ctx.exception.field, "threshold")
            self.assertIn("binary64 numeric domain", str(ctx.exception))

    def test_anomaly_decision_unknown_fields(self):
        """TC-DEC-15: Unknown/extraneous fields are strictly rejected."""
        payload = copy.deepcopy(self.valid_decision_normal)
        payload["hardware_accelerator_model"] = "MAX78000"
        with self.assertRaises(ContractValidationError) as ctx:
            validate_anomaly_decision(payload)
        self.assertIn("unknown_fields", str(ctx.exception))

    def test_anomaly_decision_immutability(self):
        """TC-DEC-16: Validation does not mutate the input payload."""
        payload = copy.deepcopy(self.valid_decision_normal)
        original = copy.deepcopy(payload)
        validate_anomaly_decision(payload)
        self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
