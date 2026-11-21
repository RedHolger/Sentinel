"""Comprehensive unit test suite for ADI Runtime windowing and feature extraction.

Tests fixed-size window extraction, trailing sample accounting,
per-channel time-domain features (mean, RMS, peak), independent analytical
references, numerical stability, boundary edge cases, input immutability,
and strict validation of public helper functions.
Synthetic fixtures are strictly designated as test data, not evaluation evidence.
"""

import copy
from decimal import Decimal
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

root = Path(__file__).resolve().parents[2]
scripts_dir = root / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from verify_portfolio_release import run_release_verification

from adi_common import ContractValidationError
import adi_runtime
from adi_runtime import (
    WindowFeatureError,
    compute_channel_metrics,
    extract_window_features,
    stable_mean,
    stable_peak_abs,
    stable_rms,
)


class TestRuntime(unittest.TestCase):
    """Test suite for ADI Runtime."""

    def setUp(self):
        # Baseline single-channel test recording fixture
        self.fixture_1ch = {
            "schema_version": "1.0.0",
            "recording_id": "rec-test-001",
            "machine_id": "test-rig-01",
            "session_id": "test-sess-01",
            "provenance": "synthetic",
            "sample_rate_hz": 1000.0,
            "acceleration_unit": "g",
            "channels": ["x"],
            "samples": [
                [3.0],
                [4.0],
            ],
            "source_id": "test-fixture-generator",
            "source_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        }

        # Multi-channel test fixture (distinct values across channels x, y, z)
        self.fixture_3ch = {
            "schema_version": "1.0.0",
            "recording_id": "rec-test-003",
            "machine_id": "spindle-test-02",
            "session_id": "test-sess-02",
            "provenance": "recorded",
            "sample_rate_hz": 2000.0,
            "acceleration_unit": "m/s^2",
            "channels": ["x", "y", "z"],
            "samples": [
                [1.0, 10.0, 100.0],
                [2.0, 20.0, 200.0],
                [3.0, 30.0, 300.0],
                [4.0, 40.0, 400.0],
                [5.0, 50.0, 500.0],
            ],
            "source_id": "test-3ch-capture",
            "source_sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
        }

    # -------------------------------------------------------------------------
    # Baseline Package Metadata
    # -------------------------------------------------------------------------

    def test_version_present(self):
        """Preserve existing test asserting package version."""
        self.assertEqual(adi_runtime.__version__, "0.1.0")

    # -------------------------------------------------------------------------
    # Hand-Calculated Independent Reference Tests
    # -------------------------------------------------------------------------

    def test_hand_calculated_single_channel_reference(self):
        """TC-FEAT-01: Verify analytical expectation on [3.0, 4.0]: mean=3.5, rms=sqrt(12.5), peak=4.0."""
        # Window size 2, hop size 2
        res = extract_window_features(self.fixture_1ch, window_size=2, hop_size=2)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["window_count"], 1)
        self.assertEqual(res["trailing_samples"], 0)

        win0 = res["windows"][0]
        self.assertEqual(win0["window_index"], 0)
        self.assertEqual(win0["start_sample"], 0)
        self.assertEqual(win0["end_sample"], 2)

        feat_x = win0["features"]["x"]
        expected_mean = 3.5
        expected_rms = math.sqrt(12.5)  # sqrt((9 + 16)/2)
        expected_peak = 4.0

        self.assertAlmostEqual(feat_x["mean"], expected_mean, places=12)
        self.assertAlmostEqual(feat_x["rms"], expected_rms, places=12)
        self.assertEqual(feat_x["peak_abs"], expected_peak)

    def test_distinct_three_channel_no_channel_mixing(self):
        """TC-FEAT-02: Distinct channel inputs demonstrate absence of channel cross-talk or mixing."""
        # 5 samples, window_size=4, hop_size=4 -> 1 window of 4 samples, 1 trailing
        res = extract_window_features(self.fixture_3ch, window_size=4, hop_size=4)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["window_count"], 1)
        self.assertEqual(res["trailing_samples"], 1)

        win = res["windows"][0]
        # x channel: [1, 2, 3, 4]
        # mean = (1+2+3+4)/4 = 2.5
        # rms = sqrt((1+4+9+16)/4) = sqrt(30/4) = sqrt(7.5) ≈ 2.7386127875258306
        # peak = 4.0
        self.assertAlmostEqual(win["features"]["x"]["mean"], 2.5, places=12)
        self.assertAlmostEqual(win["features"]["x"]["rms"], math.sqrt(7.5), places=12)
        self.assertEqual(win["features"]["x"]["peak_abs"], 4.0)

        # y channel: [10, 20, 30, 40]
        # mean = 25.0, rms = sqrt(750) = 10 * sqrt(7.5), peak = 40.0
        self.assertAlmostEqual(win["features"]["y"]["mean"], 25.0, places=12)
        self.assertAlmostEqual(win["features"]["y"]["rms"], 10.0 * math.sqrt(7.5), places=12)
        self.assertEqual(win["features"]["y"]["peak_abs"], 40.0)

        # z channel: [100, 200, 300, 400]
        # mean = 250.0, rms = 100 * sqrt(7.5), peak = 400.0
        self.assertAlmostEqual(win["features"]["z"]["mean"], 250.0, places=12)
        self.assertAlmostEqual(win["features"]["z"]["rms"], 100.0 * math.sqrt(7.5), places=12)
        self.assertEqual(win["features"]["z"]["peak_abs"], 400.0)

    # -------------------------------------------------------------------------
    # Window Segmentation & Trailing Sample Accounting Tests
    # -------------------------------------------------------------------------

    def test_window_exact_length(self):
        """TC-WIN-01: Sample length equals window length -> 1 complete window, 0 trailing."""
        recording = copy.deepcopy(self.fixture_1ch)
        recording["samples"] = [[1.0], [2.0], [3.0], [4.0]]
        res = extract_window_features(recording, window_size=4, hop_size=4)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["window_count"], 1)
        self.assertEqual(res["trailing_samples"], 0)
        self.assertEqual(res["windows"][0]["start_sample"], 0)
        self.assertEqual(res["windows"][0]["end_sample"], 4)

    def test_window_shorter_than_window_size(self):
        """TC-WIN-02: Total samples shorter than window_size -> INSUFFICIENT_LENGTH status."""
        recording = copy.deepcopy(self.fixture_1ch)
        recording["samples"] = [[1.0], [2.0], [3.0]]  # 3 samples
        res = extract_window_features(recording, window_size=4, hop_size=2)
        self.assertEqual(res["status"], "INSUFFICIENT_LENGTH")
        self.assertEqual(res["window_count"], 0)
        self.assertEqual(res["trailing_samples"], 3)
        self.assertEqual(res["windows"], [])

    def test_window_overlapping_and_trailing_accounting(self):
        """TC-WIN-03: Overlapping windows with explicit trailing remainder."""
        recording = copy.deepcopy(self.fixture_1ch)
        recording["samples"] = [[float(i)] for i in range(11)]
        res = extract_window_features(recording, window_size=4, hop_size=3)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["window_count"], 3)
        self.assertEqual(res["trailing_samples"], 1)

        self.assertEqual(res["windows"][0]["start_sample"], 0)
        self.assertEqual(res["windows"][0]["end_sample"], 4)
        self.assertEqual(res["windows"][1]["start_sample"], 3)
        self.assertEqual(res["windows"][1]["end_sample"], 7)
        self.assertEqual(res["windows"][2]["start_sample"], 6)
        self.assertEqual(res["windows"][2]["end_sample"], 10)

    def test_window_hop_size_one(self):
        """TC-WIN-04: Dense overlapping windows (hop_size=1)."""
        recording = copy.deepcopy(self.fixture_1ch)
        recording["samples"] = [[1.0], [2.0], [3.0], [4.0], [5.0]]
        res = extract_window_features(recording, window_size=3, hop_size=1)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["window_count"], 3)
        self.assertEqual(res["trailing_samples"], 0)
        self.assertEqual([w["start_sample"] for w in res["windows"]], [0, 1, 2])
        self.assertEqual([w["end_sample"] for w in res["windows"]], [3, 4, 5])

    # -------------------------------------------------------------------------
    # Invalid Window / Hop Parameter Validation Tests
    # -------------------------------------------------------------------------

    def test_invalid_window_parameters_raise(self):
        """TC-PARAM-01: Non-positive, boolean, fractional, or hop > window parameters raise WindowFeatureError."""
        for bad_ws in (0, -1, -10):
            with self.assertRaises(WindowFeatureError):
                extract_window_features(self.fixture_1ch, window_size=bad_ws, hop_size=1)

        for bad_hop in (0, -1, -5):
            with self.assertRaises(WindowFeatureError):
                extract_window_features(self.fixture_1ch, window_size=2, hop_size=bad_hop)

        with self.assertRaises(WindowFeatureError):
            extract_window_features(self.fixture_1ch, window_size=True, hop_size=1)
        with self.assertRaises(WindowFeatureError):
            extract_window_features(self.fixture_1ch, window_size=2, hop_size=False)

        with self.assertRaises(WindowFeatureError):
            extract_window_features(self.fixture_1ch, window_size=2.5, hop_size=1)
        with self.assertRaises(WindowFeatureError):
            extract_window_features(self.fixture_1ch, window_size=2, hop_size=1.5)

        with self.assertRaises(WindowFeatureError):
            extract_window_features(self.fixture_1ch, window_size=2, hop_size=3)

    # -------------------------------------------------------------------------
    # Numerical Edge Cases, Overflow Avoidance & Cancellation Robustness
    # -------------------------------------------------------------------------

    def test_numerical_zeros(self):
        """TC-NUM-01: Zero-valued signal yields zero mean, zero RMS, zero peak."""
        metrics = compute_channel_metrics([0.0, 0.0, 0.0])
        self.assertEqual(metrics["mean"], 0.0)
        self.assertEqual(metrics["rms"], 0.0)
        self.assertEqual(metrics["peak_abs"], 0.0)

    def test_numerical_constant(self):
        """TC-NUM-02: Constant signal [c, c, c] yields mean=c, RMS=|c|, peak=|c|."""
        for c in (5.0, -5.0):
            metrics = compute_channel_metrics([c, c, c])
            self.assertEqual(metrics["mean"], c)
            self.assertEqual(metrics["rms"], abs(c))
            self.assertEqual(metrics["peak_abs"], abs(c))

    def test_numerical_alternating_signs(self):
        """TC-NUM-03: Symmetric alternating signs [c, -c] yields mean=0, RMS=|c|, peak=|c|."""
        metrics = compute_channel_metrics([2.5, -2.5, 2.5, -2.5])
        self.assertEqual(metrics["mean"], 0.0)
        self.assertAlmostEqual(metrics["rms"], 2.5, places=12)
        self.assertEqual(metrics["peak_abs"], 2.5)

    def test_numerical_large_amplitudes_naive_square_overflow(self):
        """TC-NUM-04: Large finite values (1e200) whose naive squares (1e400) overflow binary64 are stably computed."""
        large_val = 1e200
        with self.assertRaises(OverflowError):
            _ = large_val ** 2

        metrics = compute_channel_metrics([large_val, large_val])
        self.assertEqual(metrics["mean"], large_val)
        self.assertEqual(metrics["rms"], large_val)
        self.assertEqual(metrics["peak_abs"], large_val)

        metrics_pair = compute_channel_metrics([3e200, 4e200])
        self.assertAlmostEqual(metrics_pair["mean"], 3.5e200)
        self.assertAlmostEqual(metrics_pair["rms"], math.sqrt(12.5) * 1e200)
        self.assertEqual(metrics_pair["peak_abs"], 4e200)

    def test_numerical_large_amplitudes_naive_sum_overflow(self):
        """TC-NUM-05: Values near float_info.max (1.5e308) whose naive sum (3.0e308) overflows are stably averaged."""
        val = 1.5e308
        self.assertEqual(val + val, float("inf"))

        m = stable_mean([val, val])
        self.assertEqual(m, val)

    def test_numerical_small_amplitudes(self):
        """TC-NUM-06: Very small finite values (1e-200) preserve scale without underflowing to zero."""
        small_val = 1e-200
        metrics = compute_channel_metrics([small_val, small_val])
        self.assertEqual(metrics["mean"], small_val)
        self.assertEqual(metrics["rms"], small_val)
        self.assertEqual(metrics["peak_abs"], small_val)

    def test_opposite_extreme_values_both_orders(self):
        """TC-NUM-07: Opposite-sign extreme values [1.5e308, -1.5e308] in both orders yield mean=0.0."""
        # Order 1: [1.5e308, -1.5e308]
        m1 = compute_channel_metrics([1.5e308, -1.5e308])
        self.assertEqual(m1["mean"], 0.0)
        self.assertEqual(m1["rms"], 1.5e308)
        self.assertEqual(m1["peak_abs"], 1.5e308)

        # Order 2: [-1.5e308, 1.5e308]
        m2 = compute_channel_metrics([-1.5e308, 1.5e308])
        self.assertEqual(m2["mean"], 0.0)
        self.assertEqual(m2["rms"], 1.5e308)
        self.assertEqual(m2["peak_abs"], 1.5e308)

        # Full window feature extraction on opposite-sign extremes
        recording = copy.deepcopy(self.fixture_1ch)
        recording["samples"] = [[1.5e308], [-1.5e308]]
        res = extract_window_features(recording, window_size=2, hop_size=2)
        self.assertEqual(res["status"], "SUCCESS")
        feat = res["windows"][0]["features"]["x"]
        self.assertEqual(feat["mean"], 0.0)
        self.assertEqual(feat["rms"], 1.5e308)
        self.assertEqual(feat["peak_abs"], 1.5e308)
        # Guarantee no non-finite value was returned
        self.assertTrue(math.isfinite(feat["mean"]))
        self.assertTrue(math.isfinite(feat["rms"]))
        self.assertTrue(math.isfinite(feat["peak_abs"]))

    def test_repeated_and_alternating_extreme_values(self):
        """TC-NUM-08: Repeated and alternating extremes accumulate without intermediate overflow."""
        # 4 alternating extremes
        m4 = compute_channel_metrics([1.5e308, -1.5e308, 1.5e308, -1.5e308])
        self.assertEqual(m4["mean"], 0.0)
        self.assertEqual(m4["rms"], 1.5e308)
        self.assertEqual(m4["peak_abs"], 1.5e308)

        # 4 alternating extremes reverse
        m4_rev = compute_channel_metrics([-1.5e308, 1.5e308, -1.5e308, 1.5e308])
        self.assertEqual(m4_rev["mean"], 0.0)
        self.assertEqual(m4_rev["rms"], 1.5e308)
        self.assertEqual(m4_rev["peak_abs"], 1.5e308)

        # 3 values with net positive sum: [1.5e308, 1.5e308, -1.5e308] -> sum = 1.5e308, mean = 0.5e308 = 5.0e307
        m3_pos = compute_channel_metrics([1.5e308, 1.5e308, -1.5e308])
        expected_mean_pos = float(Fraction.from_float(1.5e308) / 3)
        self.assertAlmostEqual(m3_pos["mean"], expected_mean_pos, delta=1e292)
        self.assertEqual(m3_pos["mean"], 5.0e307)
        self.assertEqual(m3_pos["peak_abs"], 1.5e308)

        # 3 values with net negative sum: [-1.5e308, -1.5e308, 1.5e308] -> mean = -5.0e307
        m3_neg = compute_channel_metrics([-1.5e308, -1.5e308, 1.5e308])
        self.assertEqual(m3_neg["mean"], -5.0e307)
        self.assertEqual(m3_neg["peak_abs"], 1.5e308)

    def test_cancellation_sensitive_values(self):
        """TC-NUM-09: Cancellation-sensitive [1e16, 1.0, -1e16] correctly yields mean = 1/3."""
        # Forward order: [1e16, 1.0, -1e16]
        m_fwd = compute_channel_metrics([1e16, 1.0, -1e16])
        expected_frac = float(Fraction(1, 3))  # 0.3333333333333333
        expected_dec = float(Decimal(1) / Decimal(3))
        self.assertEqual(expected_frac, expected_dec)
        self.assertAlmostEqual(m_fwd["mean"], expected_frac, places=15)
        self.assertEqual(m_fwd["mean"], 1.0 / 3.0)

        # Reverse order: [-1e16, 1.0, 1e16]
        m_rev = compute_channel_metrics([-1e16, 1.0, 1e16])
        self.assertAlmostEqual(m_rev["mean"], expected_frac, places=15)
        self.assertEqual(m_rev["mean"], 1.0 / 3.0)

        # Middle swap: [1.0, 1e16, -1e16]
        m_mid = compute_channel_metrics([1.0, 1e16, -1e16])
        self.assertAlmostEqual(m_mid["mean"], expected_frac, places=15)

    # -------------------------------------------------------------------------
    # Public Helper Validation Tests (Option B: Explicit Validation)
    # -------------------------------------------------------------------------

    def test_public_helper_empty_sequence_raises(self):
        """TC-HELP-01: Empty sequence raises WindowFeatureError across all public helpers."""
        for fn in (compute_channel_metrics, stable_mean, stable_rms, stable_peak_abs):
            with self.assertRaises(WindowFeatureError) as ctx:
                fn([])
            self.assertIn("must not be empty", str(ctx.exception))

    def test_public_helper_boolean_raises(self):
        """TC-HELP-02: Boolean samples raise WindowFeatureError across public helpers."""
        for bad_bool in ([True], [False], [1.0, True, 2.0], [False, 0.5]):
            with self.assertRaises(WindowFeatureError) as ctx:
                compute_channel_metrics(bad_bool)
            self.assertIn("boolean", str(ctx.exception).lower())

            with self.assertRaises(WindowFeatureError):
                stable_mean(bad_bool)

            with self.assertRaises(WindowFeatureError):
                stable_rms(bad_bool)

            with self.assertRaises(WindowFeatureError):
                stable_peak_abs(bad_bool)

    def test_public_helper_nan_and_inf_raises(self):
        """TC-HELP-03: NaN and Infinity raise WindowFeatureError across public helpers."""
        for bad_val in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(WindowFeatureError) as ctx:
                compute_channel_metrics([bad_val])
            self.assertIn("non-finite", str(ctx.exception).lower())

            with self.assertRaises(WindowFeatureError):
                stable_mean([1.0, bad_val])

            with self.assertRaises(WindowFeatureError):
                stable_rms([bad_val, 2.0])

            with self.assertRaises(WindowFeatureError):
                stable_peak_abs([bad_val])

    def test_public_helper_oversized_integer_raises(self):
        """TC-HELP-04: Oversized integers exceeding binary64 domain raise WindowFeatureError."""
        for huge_int in (10**400, -(10**400)):
            with self.assertRaises(WindowFeatureError) as ctx:
                compute_channel_metrics([huge_int])
            self.assertIn("exceeds finite ieee 754 binary64", str(ctx.exception).lower())

            with self.assertRaises(WindowFeatureError):
                stable_mean([huge_int])

    def test_public_helper_non_numeric_type_raises(self):
        """TC-HELP-05: Non-numeric types (str, dict, None) raise WindowFeatureError."""
        for bad_type in (["hello"], [None], [{"key": 1.0}], "not_a_list", 42):
            with self.assertRaises(WindowFeatureError):
                compute_channel_metrics(bad_type)

    def test_public_helper_input_immutability(self):
        """TC-HELP-06: Public helpers do not mutate their input sample list."""
        samples = [3.0, 4.0, 5.0]
        backup = list(samples)
        _ = compute_channel_metrics(samples)
        _ = stable_mean(samples)
        _ = stable_rms(samples)
        _ = stable_peak_abs(samples)
        self.assertEqual(samples, backup)

    # -------------------------------------------------------------------------
    # Contract Enforcement & Immutability Tests
    # -------------------------------------------------------------------------

    def test_rejection_of_invalid_recording_contract(self):
        """TC-INT-01: Invalid recording contracts are rejected with ContractValidationError."""
        bad_rec = copy.deepcopy(self.fixture_1ch)
        bad_rec["acceleration_unit"] = "invalid_unit"
        with self.assertRaises(ContractValidationError):
            extract_window_features(bad_rec, window_size=2, hop_size=1)

        bad_sha = copy.deepcopy(self.fixture_1ch)
        bad_sha["source_sha256"] = "a" * 64 + "\n"
        with self.assertRaises(ContractValidationError):
            extract_window_features(bad_sha, window_size=2, hop_size=1)

    def test_input_immutability(self):
        """TC-INT-02: Input recording payload is completely unmodified after feature extraction."""
        recording = copy.deepcopy(self.fixture_3ch)
        backup = copy.deepcopy(recording)
        extract_window_features(recording, window_size=3, hop_size=2)
        self.assertEqual(recording, backup)

    def test_metadata_and_units_preservation(self):
        """TC-INT-03: Result preserves recording metadata, channel order, and units."""
        res = extract_window_features(self.fixture_3ch, window_size=2, hop_size=2)
        self.assertEqual(res["recording_id"], "rec-test-003")
        self.assertEqual(res["provenance"], "recorded")
        self.assertEqual(res["sample_rate_hz"], 2000.0)
        self.assertEqual(res["acceleration_unit"], "m/s^2")
        self.assertEqual(res["channels"], ["x", "y", "z"])
        self.assertEqual(res["execution_environment"], "HOST_CPU")
        self.assertEqual(res["schema_version"], "1.0.0")

    # -------------------------------------------------------------------------
    # Deterministic Dependency Gate & Release Enforcement Tests
    # -------------------------------------------------------------------------

    def test_deterministic_gate_install_failure_prevents_release_success(self):
        """TC-GATE-01: Controlled test: dependency installation failure yields nonzero exit and BLOCKED status."""
        def mock_runner(cmd, **kwargs):
            cmd_strs = [str(c) for c in cmd]
            if "uv" in cmd_strs and "install" in cmd_strs:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=1,
                    stdout="",
                    stderr="error: No solution found when resolving dependencies:\n  Because adi-runtime==0.1.0 depends on numpy>=1.24.0 which is not available in offline cache",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        exit_code, report = run_release_verification(runner=mock_runner)
        self.assertEqual(exit_code, 1, "Installation failure must produce nonzero exit code")
        self.assertNotEqual(report.get("packaging_verification"), "PASS")
        self.assertEqual(report.get("packaging_verification"), "BLOCKED")
        self.assertEqual(report.get("dependency_resolution"), "FAILED")
        self.assertEqual(report.get("failed_operation"), "uv pip install --offline")
        self.assertIn("numpy", report.get("identified_missing_packages", []))
        self.assertIn("numpy", report.get("blocker_reason", "").lower())
        self.assertIn("No solution found", report.get("diagnostic_output", ""))
        self.assertIn("relocated_source_build", report.get("executed_checks", []))
        self.assertIn("installed_adi_runtime_features_check", report.get("pending_checks", []))

    def test_deterministic_gate_pip_check_failure_prevents_release_success(self):
        """TC-GATE-02: Controlled test: uv pip check failure yields nonzero exit and FAILED status."""
        def mock_runner(cmd, **kwargs):
            cmd_strs = [str(c) for c in cmd]
            if "uv" in cmd_strs and "check" in cmd_strs:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=1,
                    stdout="Found 1 incompatibility\nThe package `adi-runtime` requires `numpy>=1.24.0`, but it is not installed",
                    stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        exit_code, report = run_release_verification(runner=mock_runner)
        self.assertEqual(exit_code, 1, "pip check failure must produce nonzero exit code")
        self.assertNotEqual(report.get("packaging_verification"), "PASS")
        self.assertEqual(report.get("packaging_verification"), "FAILED")
        self.assertEqual(report.get("dependency_check"), "FAILED")
        self.assertEqual(report.get("failed_operation"), "uv pip check")
        self.assertIn("incompatibility", report.get("diagnostic_output", ""))
        self.assertIn("installed_adi_runtime_features_check", report.get("pending_checks", []))

    def test_deterministic_gate_success_executes_all_checks(self):
        """TC-GATE-03: Controlled test: successful install and pip check executes all verification checks before PASS."""
        executed_commands = []
        def mock_runner(cmd, **kwargs):
            cmd_strs = [str(c) for c in cmd]
            executed_commands.append(cmd_strs)
            if any(str(c).endswith('adi-portfolio') for c in cmd):
                if 'demo' in cmd_strs:
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="[DEMO SCAFFOLD] Mock demo active", stderr="")
                elif 'benchmark' in cmd_strs:
                    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout='{"status": "NOT_IMPLEMENTED", "reason": "Benchmark suite not implemented", "hardware_status": "UNVERIFIED"}', stderr="")
                elif 'evaluate' in cmd_strs:
                    if '--suite' in cmd_strs:
                        if any('suite_pass.json' in s for s in cmd_strs):
                            suite_pass_payload = {
                                "command": "evaluate",
                                "status": "PASS",
                                "summary": {
                                    "total_cases": 1,
                                    "matched_cases": 1,
                                    "mismatched_cases": 0,
                                    "error_cases": 0
                                }
                            }
                            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(suite_pass_payload), stderr="")
                        elif any('suite_mismatch.json' in s for s in cmd_strs):
                            suite_mismatch_payload = {
                                "command": "evaluate",
                                "status": "FAIL",
                                "summary": {
                                    "total_cases": 1,
                                    "matched_cases": 0,
                                    "mismatched_cases": 1,
                                    "error_cases": 0
                                }
                            }
                            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=json.dumps(suite_mismatch_payload), stderr="")
                        elif any('suite_error.json' in s for s in cmd_strs):
                            suite_error_payload = {
                                "command": "evaluate",
                                "status": "FAIL",
                                "summary": {
                                    "total_cases": 1,
                                    "matched_cases": 0,
                                    "mismatched_cases": 0,
                                    "error_cases": 1
                                }
                            }
                            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=json.dumps(suite_error_payload), stderr="")
                        elif any('suite_malformed_bool.json' in s for s in cmd_strs):
                            return subprocess.CompletedProcess(args=cmd, returncode=2, stdout="", stderr="adi-portfolio evaluate error: 'window_index' must be a non-boolean non-negative integer")
                        elif any('suite_mixed.json' in s for s in cmd_strs):
                            suite_mixed_payload = {
                                "command": "evaluate",
                                "status": "FAIL",
                                "summary": {
                                    "total_cases": 3,
                                    "matched_cases": 1,
                                    "mismatched_cases": 1,
                                    "error_cases": 1,
                                    "expected_decisions_summary": {
                                        "total_decisions": 9,
                                        "normal_count": 3,
                                        "anomaly_count": 3,
                                        "insufficient_evidence_count": 3,
                                    },
                                    "actual_decisions_summary": {
                                        "total_decisions": 6,
                                        "normal_count": 2,
                                        "anomaly_count": 2,
                                        "insufficient_evidence_count": 2,
                                    }
                                },
                                "cases": [
                                    {"expected_result": {}, "actual_window_boundaries": []},
                                    {"expected_result": {}, "actual_window_boundaries": []},
                                    {"expected_result": {}, "actual_window_boundaries": []}
                                ]
                            }
                            return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=json.dumps(suite_mixed_payload), stderr="")
                    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout='{"status": "NOT_IMPLEMENTED", "reason": "Evaluation suite not implemented", "hardware_status": "UNVERIFIED"}', stderr="")
                elif 'analyze' in cmd_strs:
                    if any('nonexistent.csv' in s for s in cmd_strs):
                        return subprocess.CompletedProcess(args=cmd, returncode=2, stdout="", stderr="adi-portfolio analyze error: CSV file not found: nonexistent.csv")
                    elif any('meta_mismatch.json' in s for s in cmd_strs):
                        ab_payload = {
                            "command": "analyze",
                            "status": "INSUFFICIENT_EVIDENCE",
                            "summary": {"recording_level_abstention": True, "total_decisions": 1}
                        }
                        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(ab_payload), stderr="")
                    else:
                        exp_sha = "mock_sha"
                        for idx, arg in enumerate(cmd_strs):
                            if arg == '--expected-sha256' and idx + 1 < len(cmd_strs):
                                exp_sha = cmd_strs[idx + 1]
                        success_payload = {
                            "command": "analyze",
                            "source_sha256": exp_sha,
                            "decisions": [
                                {"outcome": "NORMAL"},
                                {"outcome": "INSUFFICIENT_EVIDENCE"},
                                {"outcome": "ANOMALY"}
                            ],
                            "summary": {
                                "total_decisions": 3,
                                "normal_count": 1,
                                "anomaly_count": 1,
                                "insufficient_evidence_count": 1,
                                "recording_level_abstention": False
                            }
                        }
                        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(success_payload), stderr="")
                elif 'inspect-source' in cmd_strs:
                    if any('report_existing.json' in s for s in cmd_strs):
                        return subprocess.CompletedProcess(
                            args=cmd,
                            returncode=2,
                            stdout="",
                            stderr="adi-portfolio inspect-source error: Output file already exists and will not be overwritten: report_existing.json",
                        )
                    elif any('missing_var' in s for s in cmd_strs):
                        nf_payload = {
                            "command": "inspect-source",
                            "inspection_findings": {"structural_status": "TARGET_VARIABLE_NOT_FOUND"},
                        }
                        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=json.dumps(nf_payload), stderr="")
                    elif any('synth_comp.mat' in s for s in cmd_strs):
                        comp_payload = {
                            "command": "inspect-source",
                            "inspection_findings": {"structural_status": "UNSUPPORTED_STRUCTURE"},
                        }
                        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=json.dumps(comp_payload), stderr="")
                    else:
                        succ_payload = {
                            "command": "inspect-source",
                            "inspection_findings": {"structural_status": "STRUCTURAL_SUCCESS"},
                        }
                        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(succ_payload), stderr="")
                elif 'validate-measurement-evidence' in cmd_strs:
                    val_payload = {
                        "command": "validate-measurement-evidence",
                        "status": "VALID",
                        "schema_version": "1.0.0",
                        "evidence_record_id": "ev_rec_cli_rel_01",
                        "target_channel": "sig",
                    }
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(val_payload), stderr="")
                elif 'simulate-measurement-admission' in cmd_strs:
                    if '--signal-integrity' not in cmd_strs:
                        blocked_payload = {
                            "measurement_admission_status": "BLOCKED_SIGNAL_INTEGRITY",
                            "precedence_gate_reached": "SIGNAL_INTEGRITY",
                        }
                        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=json.dumps(blocked_payload), stderr="")
                    if any('cli_adm_out.json' in s for s in cmd_strs) and '--out' in cmd_strs:
                        if hasattr(mock_runner, '_adm_out_called'):
                            return subprocess.CompletedProcess(
                                args=cmd,
                                returncode=2,
                                stdout="",
                                stderr="adi-portfolio simulate-measurement-admission error: Output file already exists and will not be overwritten: cli_adm_out.json",
                            )
                        mock_runner._adm_out_called = True
                    succ_adm_payload = {
                        "measurement_admission_status": "ADMITTED_FOR_MEASUREMENT",
                        "evaluation_mode": "FICTIONAL_TEST_ONLY",
                        "operational_execution_authorized": False,
                    }
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=json.dumps(succ_adm_payload), stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        exit_code, report = run_release_verification(runner=mock_runner)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report.get("packaging_verification"), "PASS")
        self.assertEqual(report.get("dependency_resolution"), "PASS")
        self.assertEqual(report.get("dependency_check"), "PASS")
        self.assertEqual(report.get("installed_adi_common_contracts_check", {}).get("status"), "PASS")
        self.assertEqual(report.get("installed_adi_runtime_features_check", {}).get("status"), "PASS")
        self.assertEqual(report.get("installed_adi_app_ingestion_check", {}).get("status"), "PASS")
        self.assertEqual(report.get("installed_adi_runtime_threshold_check", {}).get("status"), "PASS")
        self.assertEqual(report.get("installed_adi_app_source_inspection_check", {}).get("status"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("status"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("evaluate_suite_all_pass_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("evaluate_suite_mismatch_fails_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("evaluate_suite_error_fails_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("evaluate_suite_malformed_manifest_exit_2_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("evaluate_suite_mixed_accounting_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("inspect_source_success_exit_0_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("inspect_source_out_file_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("inspect_source_target_not_found_exit_1_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("inspect_source_compressed_exit_1_verified"), "PASS")
        self.assertEqual(report.get("installed_cli_executable_check", {}).get("inspect_source_out_conflict_exit_2_verified"), "PASS")
        self.assertEqual(report.get("functional_validation", {}).get("local_analysis_cli"), "IMPLEMENTED_AND_VERIFIED")
        self.assertEqual(report.get("functional_validation", {}).get("synthetic_scenario_evaluation"), "IMPLEMENTED_AND_VERIFIED")
        self.assertEqual(report.get("functional_validation", {}).get("local_source_inspection"), "IMPLEMENTED_AND_VERIFIED")
        self.assertEqual(report.get("functional_validation", {}).get("learned_neural_model_inference"), "NOT_IMPLEMENTED")

        # Verify that all verification checks were executed in sequence before reporting PASS
        cmd_flat = " ".join(" ".join(c) for c in executed_commands)
        self.assertIn("uv build", cmd_flat)
        self.assertIn("uv pip install", cmd_flat)
        self.assertIn("uv pip check", cmd_flat)
        self.assertIn("adi-portfolio demo", cmd_flat)
        self.assertIn("adi-portfolio benchmark", cmd_flat)
        self.assertIn("adi-portfolio evaluate", cmd_flat)
        self.assertIn("adi-portfolio analyze", cmd_flat)
        self.assertIn("adi-portfolio evaluate --suite", cmd_flat)
        self.assertIn("adi-portfolio inspect-source", cmd_flat)

    def test_deterministic_cli_arbitrary_crash_fails_verification(self):
        """TC-GATE-04: Controlled test: arbitrary crash returning exit 1 without expected JSON fails verification."""
        def mock_runner(cmd, **kwargs):
            cmd_strs = [str(c) for c in cmd]
            if any(str(c).endswith('adi-portfolio') for c in cmd):
                if 'benchmark' in cmd_strs:
                    return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="Traceback: Segmentation fault or unexpected crash", stderr="Error")
                elif 'demo' in cmd_strs:
                    return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="scaffold", stderr="")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        with self.assertRaises(RuntimeError) as ctx:
            run_release_verification(runner=mock_runner)
        self.assertIn("not parseable JSON", str(ctx.exception))

    def test_dependency_failure_prevents_release_success(self):
        """TC-GATE-00: Compatibility alias delegating to deterministic installation failure gate test."""
        self.test_deterministic_gate_install_failure_prevents_release_success()


if __name__ == "__main__":
    unittest.main()
