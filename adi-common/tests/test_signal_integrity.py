"""Unit tests for deterministic signal integrity analysis."""

from __future__ import annotations

import unittest

from adi_common.admission import analyze_signal_integrity


class TestSignalIntegrityAnalysis(unittest.TestCase):
    """Test suite for analyze_signal_integrity."""

    def test_clean_waveform_passes(self) -> None:
        """Waveform with samples well below rail passes integrity check."""
        samples = [0.1, -0.2, 0.5, -0.4, 0.3, 0.0] * 1000
        res = analyze_signal_integrity(
            samples=samples,
            source_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            target_channel="DE_time",
            full_scale_limit=10.0,
            full_scale_units="g",
        )
        self.assertEqual(res["verification_status"], "PASS")
        self.assertEqual(res["rail_sample_count"], 0)
        self.assertEqual(res["longest_contiguous_rail_run"], 0)
        self.assertEqual(res["nonfinite_sample_count"], 0)
        self.assertEqual(res["clipping_ratio"], 0.0)

    def test_sustained_rail_saturation_rejected(self) -> None:
        """Waveform with >= 3 consecutive rail samples is detected as SUSTAINED_RAIL_SATURATION."""
        samples = [1.0, 2.0] + [9.999, 9.999, 9.999] + [1.0, 2.0] * 500
        res = analyze_signal_integrity(
            samples=samples,
            source_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            target_channel="DE_time",
            full_scale_limit=10.0,
        )
        self.assertEqual(res["verification_status"], "SUSTAINED_RAIL_SATURATION")
        self.assertGreaterEqual(res["longest_contiguous_rail_run"], 3)
        self.assertEqual(res["rail_sample_count"], 3)

    def test_excessive_clipping_ratio_rejected(self) -> None:
        """Waveform with clipping ratio > 0.01% (even with isolated spikes) is rejected."""
        # 10,000 samples, 0.01% allowance is 1 sample. 5 rail samples exceeds allowance.
        samples = [1.0] * 10000
        # Inject 5 isolated rail samples separated by clean samples
        samples[100] = 9.999
        samples[200] = -9.999
        samples[300] = 9.999
        samples[400] = -9.999
        samples[500] = 9.999
        res = analyze_signal_integrity(
            samples=samples,
            source_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            target_channel="DE_time",
            full_scale_limit=10.0,
        )
        self.assertEqual(res["verification_status"], "EXCESSIVE_CLIPPING")
        self.assertEqual(res["rail_sample_count"], 5)
        self.assertEqual(res["longest_contiguous_rail_run"], 1)
        self.assertGreater(res["clipping_ratio"], 0.0001)

    def test_nonfinite_samples_detected(self) -> None:
        """Waveform with NaN or Infinity values is detected as NONFINITE_DETECTED."""
        samples = [0.1, 0.2, float("nan"), 0.4, float("inf"), -0.2] * 100
        res = analyze_signal_integrity(
            samples=samples,
            source_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            target_channel="DE_time",
            full_scale_limit=10.0,
        )
        self.assertEqual(res["verification_status"], "NONFINITE_DETECTED")
        self.assertEqual(res["nonfinite_sample_count"], 200)

    def test_unknown_full_scale(self) -> None:
        """When physical full scale limit is unknown or None, clipping cannot be computed."""
        samples = [0.1, 0.2, 0.3] * 100
        res = analyze_signal_integrity(
            samples=samples,
            source_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            target_channel="DE_time",
            full_scale_limit=None,
        )
        self.assertEqual(res["verification_status"], "UNKNOWN_FULL_SCALE")
        self.assertIsNone(res["rail_sample_count"])
        self.assertIsNone(res["longest_contiguous_rail_run"])
        self.assertIsNone(res["clipping_ratio"])
        self.assertEqual(res["nonfinite_sample_count"], 0)

    def test_nan_with_unknown_full_scale_reports_correct_nonfinite_count(self) -> None:
        """NaN and Inf with unknown full scale report correct nonfinite count and NONFINITE_DETECTED."""
        samples = [0.5, float("nan"), float("inf")]
        res = analyze_signal_integrity(
            samples=samples,
            source_digest="a" * 64,
            target_channel="DE_time",
            full_scale_limit=None,
        )
        self.assertEqual(res["nonfinite_sample_count"], 2)
        self.assertEqual(res["verification_status"], "NONFINITE_DETECTED")
        self.assertIsNone(res["rail_sample_count"])
        self.assertIsNone(res["longest_contiguous_rail_run"])
        self.assertIsNone(res["clipping_ratio"])

    def test_nested_sample_structure_rejected(self) -> None:
        """Nested sample structures (lists/tuples) raise TypeError rather than converting to zero."""
        with self.assertRaises(TypeError):
            analyze_signal_integrity(
                samples=[0.5, [1.0]],
                source_digest="a" * 64,
                target_channel="DE_time",
                full_scale_limit=10.0,
            )

    def test_non_numeric_and_boolean_samples_rejected(self) -> None:
        """Strings and boolean samples raise TypeError."""
        with self.assertRaises(TypeError):
            analyze_signal_integrity(
                samples=[0.5, "corrupted", 0.1],
                source_digest="a" * 64,
                target_channel="DE_time",
                full_scale_limit=10.0,
            )
        with self.assertRaises(TypeError):
            analyze_signal_integrity(
                samples=[0.5, True, 0.1],
                source_digest="a" * 64,
                target_channel="DE_time",
                full_scale_limit=10.0,
            )

    def test_invalid_rail_tolerance_rejected(self) -> None:
        """Rail tolerance outside (0, 1) raises ValueError."""
        with self.assertRaises(ValueError):
            analyze_signal_integrity(
                samples=[0.1, 0.2],
                source_digest="a" * 64,
                target_channel="DE_time",
                full_scale_limit=10.0,
                rail_tolerance=0.0,
            )
        with self.assertRaises(ValueError):
            analyze_signal_integrity(
                samples=[0.1, 0.2],
                source_digest="a" * 64,
                target_channel="DE_time",
                full_scale_limit=10.0,
                rail_tolerance=1.5,
            )

    def test_invalid_full_scale_limit_rejected(self) -> None:
        """Non-numeric full_scale_limit raises TypeError."""
        with self.assertRaises(TypeError):
            analyze_signal_integrity(
                samples=[0.1, 0.2],
                source_digest="a" * 64,
                target_channel="DE_time",
                full_scale_limit="invalid",
            )

    def test_conservative_precedence_nonfinite_over_clipping(self) -> None:
        """Nonfinite values take precedence over rail saturation."""
        samples = [0.1, float("nan")] + [9.999] * 5 + [0.1]
        res = analyze_signal_integrity(
            samples=samples,
            source_digest="a" * 64,
            target_channel="DE_time",
            full_scale_limit=10.0,
        )
        self.assertEqual(res["verification_status"], "NONFINITE_DETECTED")
        self.assertEqual(res["nonfinite_sample_count"], 1)


if __name__ == "__main__":
    unittest.main()
