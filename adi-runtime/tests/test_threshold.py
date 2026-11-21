"""Unit tests for Stage 04: RMS Threshold Anomaly Reference Detector.

Validates independent synthetic expectations for below/above/equal threshold,
distinct channels proving max-RMS scoring, positive/negative full-scale equality
and exceedance, saturated and valid windows together, operating envelope mismatches,
zero signal, tail accounting, repeatability, immutability, invalid configurations,
decision contract validation, and CSV integration.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Dict, List
import unittest

from adi_app import load_vibration_csv
from adi_common import ContractValidationError, validate_anomaly_decision, validate_vibration_recording
from adi_runtime import (
    DETECTOR_ID,
    DETECTOR_VERSION,
    EVALUATION_SCOPE,
    REASON_CHANNELS_MISMATCH,
    REASON_INSUFFICIENT_LENGTH,
    REASON_SAMPLE_RATE_MISMATCH,
    REASON_SENSOR_SATURATION_POSSIBLE,
    REASON_UNIT_MISMATCH,
    ConfigurationError,
    compute_canonical_configuration_fingerprint,
    compute_canonical_recording_fingerprint,
    derive_decision_id,
    evaluate_rms_threshold,
    extract_window_features,
    validate_threshold_configuration,
)


def _make_valid_recording(
    samples: List[List[Any]],
    *,
    channels: List[str] | None = None,
    sample_rate_hz: float | int = 1000.0,
    acceleration_unit: str = "g",
    recording_id: str = "rec_test_001",
) -> Dict[str, Any]:
    """Helper to construct a valid VibrationRecordingV1 dictionary."""
    if channels is None:
        channels = ["x", "y", "z"][: len(samples[0])] if samples else ["x"]
    raw_bytes = json.dumps(samples).encode("utf-8")
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    rec = {
        "schema_version": "1.0.0",
        "recording_id": recording_id,
        "machine_id": "mach_001",
        "session_id": "sess_001",
        "source_id": "src_001",
        "provenance": "synthetic",
        "sample_rate_hz": sample_rate_hz,
        "acceleration_unit": acceleration_unit,
        "channels": list(channels),
        "samples": [list(row) for row in samples],
        "source_sha256": sha256,
    }
    return validate_vibration_recording(rec)


def _make_valid_config(
    *,
    config_id: str = "cfg_test_001",
    config_version: str = "1.0.0",
    window_size: int = 10,
    hop_size: int = 10,
    expected_sample_rate_hz: float = 1000.0,
    expected_acceleration_unit: str = "g",
    expected_channels: List[str] | None = None,
    full_scale: float = 10.0,
    threshold: float = 2.0,
) -> Dict[str, Any]:
    """Helper to construct a valid detector configuration dictionary."""
    if expected_channels is None:
        expected_channels = ["x"]
    return {
        "config_id": config_id,
        "config_version": config_version,
        "window_size": window_size,
        "hop_size": hop_size,
        "expected_sample_rate_hz": expected_sample_rate_hz,
        "expected_acceleration_unit": expected_acceleration_unit,
        "expected_channels": list(expected_channels),
        "full_scale": full_scale,
        "threshold": threshold,
    }


class TestThresholdReference(unittest.TestCase):
    """Test suite for RMS threshold anomaly detector reference."""

    def test_score_below_threshold_produces_normal(self) -> None:
        """TC-THRESH-01: Window with max-RMS strictly below threshold produces NORMAL."""
        # 10 samples of constant 1.0 -> RMS = 1.0. Threshold = 2.0 -> NORMAL
        samples = [[1.0]] * 10
        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10, threshold=2.0, full_scale=10.0)

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(result["decisions"]), 1)
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)

        self.assertEqual(dec["outcome"], "NORMAL")
        self.assertEqual(dec["quality_status"], "PASS")
        self.assertEqual(dec["score"], 1.0)
        self.assertEqual(dec["threshold"], 2.0)
        self.assertEqual(dec["reason_codes"], [])
        self.assertEqual(dec["execution_environment"], "HOST_CPU")

    def test_score_above_threshold_produces_anomaly(self) -> None:
        """TC-THRESH-02: Window with max-RMS strictly above threshold produces ANOMALY."""
        # 10 samples of constant 3.0 -> RMS = 3.0. Threshold = 2.0 -> ANOMALY
        samples = [[3.0]] * 10
        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10, threshold=2.0, full_scale=10.0)

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(result["decisions"]), 1)
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)

        self.assertEqual(dec["outcome"], "ANOMALY")
        self.assertEqual(dec["quality_status"], "PASS")
        self.assertEqual(dec["score"], 3.0)
        self.assertEqual(dec["threshold"], 2.0)
        self.assertEqual(dec["reason_codes"], [])

    def test_score_equal_to_threshold_produces_anomaly(self) -> None:
        """TC-THRESH-03: Window with max-RMS exactly equal to threshold produces ANOMALY."""
        # Contract rule: score >= threshold => ANOMALY
        samples = [[2.0]] * 10
        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10, threshold=2.0, full_scale=10.0)

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "COMPLETED")
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)

        self.assertEqual(dec["outcome"], "ANOMALY")
        self.assertEqual(dec["quality_status"], "PASS")
        self.assertEqual(dec["score"], 2.0)
        self.assertEqual(dec["threshold"], 2.0)

    def test_multi_channel_max_rms_scoring(self) -> None:
        """TC-THRESH-04: Multi-channel scoring strictly selects maximum per-channel RMS."""
        # 3 channels: x has RMS=0.5, y has RMS=2.5, z has RMS=1.0. Max-RMS = 2.5
        # With threshold = 2.0, score (2.5) >= threshold => ANOMALY
        samples_1 = [[0.5, 2.5, 1.0]] * 10
        rec_1 = _make_valid_recording(samples_1, channels=["x", "y", "z"])
        cfg_1 = _make_valid_config(
            window_size=10,
            hop_size=10,
            expected_channels=["x", "y", "z"],
            threshold=2.0,
            full_scale=10.0,
        )

        result_1 = evaluate_rms_threshold(rec_1, cfg_1)
        dec_1 = result_1["decisions"][0]
        validate_anomaly_decision(dec_1)
        self.assertEqual(dec_1["outcome"], "ANOMALY")
        self.assertAlmostEqual(dec_1["score"], 2.5, places=9)

        # Permute channels: x has RMS=2.5, y has RMS=0.5, z has RMS=1.0. Max-RMS still = 2.5
        samples_2 = [[2.5, 0.5, 1.0]] * 10
        rec_2 = _make_valid_recording(samples_2, channels=["x", "y", "z"])
        result_2 = evaluate_rms_threshold(rec_2, cfg_1)
        dec_2 = result_2["decisions"][0]
        validate_anomaly_decision(dec_2)
        self.assertEqual(dec_2["outcome"], "ANOMALY")
        self.assertAlmostEqual(dec_2["score"], 2.5, places=9)

    def test_positive_full_scale_equality_produces_abstention(self) -> None:
        """TC-THRESH-05: Sample equal to +full_scale flags possible saturation abstention."""
        # Sample reaches exactly full_scale (10.0) -> abs(val) >= full_scale -> INSUFFICIENT_EVIDENCE
        samples = [[1.0]] * 9 + [[10.0]]
        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10, full_scale=10.0, threshold=2.0)

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "COMPLETED")
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)

        self.assertEqual(dec["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec["quality_status"], "INDETERMINATE")
        self.assertIsNone(dec["score"])
        self.assertIsNone(dec["threshold"])
        self.assertEqual(dec["reason_codes"], [REASON_SENSOR_SATURATION_POSSIBLE])

    def test_positive_full_scale_exceedance_produces_abstention(self) -> None:
        """TC-THRESH-06: Sample exceeding +full_scale flags possible saturation abstention."""
        samples = [[1.0]] * 9 + [[15.0]]
        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10, full_scale=10.0, threshold=2.0)

        result = evaluate_rms_threshold(rec, cfg)
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)

        self.assertEqual(dec["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec["quality_status"], "INDETERMINATE")
        self.assertEqual(dec["reason_codes"], [REASON_SENSOR_SATURATION_POSSIBLE])

    def test_negative_full_scale_equality_produces_abstention(self) -> None:
        """TC-THRESH-07: Sample equal to -full_scale flags possible saturation abstention."""
        samples = [[1.0]] * 9 + [[-10.0]]
        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10, full_scale=10.0, threshold=2.0)

        result = evaluate_rms_threshold(rec, cfg)
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)

        self.assertEqual(dec["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec["quality_status"], "INDETERMINATE")
        self.assertEqual(dec["reason_codes"], [REASON_SENSOR_SATURATION_POSSIBLE])

    def test_negative_full_scale_exceedance_produces_abstention(self) -> None:
        """TC-THRESH-08: Sample exceeding -full_scale flags possible saturation abstention."""
        samples = [[1.0]] * 9 + [[-25.0]]
        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10, full_scale=10.0, threshold=2.0)

        result = evaluate_rms_threshold(rec, cfg)
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)

        self.assertEqual(dec["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec["quality_status"], "INDETERMINATE")
        self.assertEqual(dec["reason_codes"], [REASON_SENSOR_SATURATION_POSSIBLE])

    def test_saturated_and_valid_windows_together(self) -> None:
        """TC-THRESH-09: Sequence with normal, saturated, and anomalous windows."""
        # 3 windows of length 5 (15 samples total, hop=5):
        # Window 0 (0..5): samples=1.0 -> RMS=1.0 < 2.0 -> NORMAL
        # Window 1 (5..10): samples contain 12.0 (>= 10.0) -> INSUFFICIENT_EVIDENCE
        # Window 2 (10..15): samples=3.0 -> RMS=3.0 >= 2.0 -> ANOMALY
        w0 = [[1.0]] * 5
        w1 = [[1.0], [1.0], [12.0], [1.0], [1.0]]
        w2 = [[3.0]] * 5
        samples = w0 + w1 + w2

        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=5, hop_size=5, full_scale=10.0, threshold=2.0)

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(result["decisions"]), 3)
        self.assertEqual(len(result["window_boundaries"]), 3)

        dec0, dec1, dec2 = result["decisions"]
        validate_anomaly_decision(dec0)
        validate_anomaly_decision(dec1)
        validate_anomaly_decision(dec2)

        self.assertEqual(dec0["outcome"], "NORMAL")
        self.assertEqual(dec0["quality_status"], "PASS")

        self.assertEqual(dec1["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec1["quality_status"], "INDETERMINATE")
        self.assertEqual(dec1["reason_codes"], [REASON_SENSOR_SATURATION_POSSIBLE])

        self.assertEqual(dec2["outcome"], "ANOMALY")
        self.assertEqual(dec2["quality_status"], "PASS")

    def test_operating_envelope_sample_rate_mismatch(self) -> None:
        """TC-THRESH-10: Incompatible sample rate produces recording-level abstention."""
        samples = [[1.0]] * 10
        rec = _make_valid_recording(samples, sample_rate_hz=2000.0)
        cfg = _make_valid_config(expected_sample_rate_hz=1000.0)

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["window_boundaries"], [])  # Must not invent boundaries
        self.assertEqual(result["trailing_samples"], 10)
        self.assertEqual(len(result["decisions"]), 1)

        dec = result["decisions"][0]
        validate_anomaly_decision(dec)
        self.assertEqual(dec["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec["quality_status"], "FAIL")
        self.assertEqual(dec["reason_codes"], [REASON_SAMPLE_RATE_MISMATCH])

    def test_operating_envelope_unit_mismatch(self) -> None:
        """TC-THRESH-11: Incompatible acceleration unit produces recording-level abstention."""
        samples = [[1.0]] * 10
        rec = _make_valid_recording(samples, acceleration_unit="m/s^2")
        cfg = _make_valid_config(expected_acceleration_unit="g")

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["window_boundaries"], [])
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)
        self.assertEqual(dec["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec["quality_status"], "FAIL")
        self.assertEqual(dec["reason_codes"], [REASON_UNIT_MISMATCH])

    def test_operating_envelope_channels_mismatch_and_reordering(self) -> None:
        """TC-THRESH-12: Channel set mismatch or order mismatch produces recording-level abstention."""
        # 1. Channel count mismatch (recording has ['x', 'y'], config expects ['x', 'y', 'z'])
        samples_2ch = [[1.0, 2.0]] * 10
        rec_2ch = _make_valid_recording(samples_2ch, channels=["x", "y"])
        cfg_3ch = _make_valid_config(expected_channels=["x", "y", "z"])

        res1 = evaluate_rms_threshold(rec_2ch, cfg_3ch)
        self.assertEqual(res1["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(res1["window_boundaries"], [])
        self.assertEqual(res1["decisions"][0]["reason_codes"], [REASON_CHANNELS_MISMATCH])

        # 2. Channel order mismatch (recording has ['y', 'x'], config expects ['x', 'y'])
        rec_yx = _make_valid_recording(samples_2ch, channels=["y", "x"])
        cfg_xy = _make_valid_config(expected_channels=["x", "y"])

        res2 = evaluate_rms_threshold(rec_yx, cfg_xy)
        self.assertEqual(res2["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(res2["window_boundaries"], [])
        self.assertEqual(res2["decisions"][0]["reason_codes"], [REASON_CHANNELS_MISMATCH])

    def test_operating_envelope_insufficient_length(self) -> None:
        """TC-THRESH-13: Recording shorter than window_size produces recording-level abstention."""
        samples_5 = [[1.0]] * 5
        rec = _make_valid_recording(samples_5, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10)

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["window_boundaries"], [])
        self.assertEqual(result["trailing_samples"], 5)
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)
        self.assertEqual(dec["reason_codes"], [REASON_INSUFFICIENT_LENGTH])

    def test_operating_envelope_multiple_mismatches_all_recorded(self) -> None:
        """TC-THRESH-14: Multiple simultaneous mismatches are all captured in reason codes."""
        # Rate mismatch (2000 vs 1000) AND unit mismatch (m/s^2 vs g)
        samples = [[1.0]] * 10
        rec = _make_valid_recording(samples, sample_rate_hz=2000.0, acceleration_unit="m/s^2")
        cfg = _make_valid_config(expected_sample_rate_hz=1000.0, expected_acceleration_unit="g")

        result = evaluate_rms_threshold(rec, cfg)
        dec = result["decisions"][0]
        validate_anomaly_decision(dec)
        self.assertIn(REASON_SAMPLE_RATE_MISMATCH, dec["reason_codes"])
        self.assertIn(REASON_UNIT_MISMATCH, dec["reason_codes"])

    def test_zero_signal_is_valid(self) -> None:
        """TC-THRESH-15: All-zero signal is evaluated normally and not rejected as invalid."""
        samples = [[0.0]] * 10
        rec = _make_valid_recording(samples, channels=["x"])

        # With threshold = 1.0, score = 0.0 < 1.0 -> NORMAL
        cfg_normal = _make_valid_config(threshold=1.0)
        res_normal = evaluate_rms_threshold(rec, cfg_normal)
        dec_normal = res_normal["decisions"][0]
        validate_anomaly_decision(dec_normal)
        self.assertEqual(dec_normal["outcome"], "NORMAL")
        self.assertEqual(dec_normal["quality_status"], "PASS")
        self.assertEqual(dec_normal["score"], 0.0)

        # With threshold = 0.0, score = 0.0 >= 0.0 -> ANOMALY
        cfg_anomaly = _make_valid_config(threshold=0.0)
        res_anomaly = evaluate_rms_threshold(rec, cfg_anomaly)
        dec_anomaly = res_anomaly["decisions"][0]
        validate_anomaly_decision(dec_anomaly)
        self.assertEqual(dec_anomaly["outcome"], "ANOMALY")
        self.assertEqual(dec_anomaly["quality_status"], "PASS")
        self.assertEqual(dec_anomaly["score"], 0.0)

    def test_trailing_samples_accounting(self) -> None:
        """TC-THRESH-16: Uncovered trailing samples are correctly counted."""
        # 25 samples, window_size=10, hop_size=10 -> windows at 0..10, 10..20; 5 trailing
        samples = [[1.0]] * 25
        rec = _make_valid_recording(samples, channels=["x"])
        cfg = _make_valid_config(window_size=10, hop_size=10)

        result = evaluate_rms_threshold(rec, cfg)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(len(result["window_boundaries"]), 2)
        self.assertEqual(result["trailing_samples"], 5)
        self.assertEqual(result["window_boundaries"][0]["start_sample"], 0)
        self.assertEqual(result["window_boundaries"][0]["end_sample"], 10)
        self.assertEqual(result["window_boundaries"][1]["start_sample"], 10)
        self.assertEqual(result["window_boundaries"][1]["end_sample"], 20)

    def test_repeatability_and_deterministic_decision_ids(self) -> None:
        """TC-THRESH-17: Executing evaluation repeatedly yields identical deterministic results."""
        samples = [[1.5]] * 20
        rec = _make_valid_recording(samples, recording_id="rec_deterministic_01")
        cfg = _make_valid_config(config_id="cfg_det_01", window_size=10, hop_size=10)

        res1 = evaluate_rms_threshold(rec, cfg)
        res2 = evaluate_rms_threshold(rec, cfg)

        self.assertEqual(res1, res2)
        # Verify deterministic versioned decision ID format bound to content
        id0 = res1["decisions"][0]["decision_id"]
        id1 = res1["decisions"][1]["decision_id"]
        self.assertTrue(id0.startswith("dec_v1_w0_0_10_"), f"Unexpected ID prefix: {id0}")
        self.assertTrue(id1.startswith("dec_v1_w1_10_20_"), f"Unexpected ID prefix: {id1}")
        self.assertNotEqual(id0, id1)

    def test_decision_id_changes_when_threshold_flips_outcome(self) -> None:
        """TC-THRESH-21: Changing threshold to flip outcome strictly changes the decision ID."""
        # Supervisor reproducer: samples [[2.0], [2.0]]
        samples = [[2.0], [2.0]]
        rec = _make_valid_recording(samples, channels=["x"], recording_id="rec_flip")
        cfg_anom = _make_valid_config(
            config_id="cfg_flip", window_size=2, hop_size=2, full_scale=10.0, threshold=1.0
        )
        res_anom = evaluate_rms_threshold(rec, cfg_anom)
        dec_anom = res_anom["decisions"][0]
        self.assertEqual(dec_anom["outcome"], "ANOMALY")

        cfg_norm = dict(cfg_anom)
        cfg_norm["threshold"] = 3.0
        res_norm = evaluate_rms_threshold(rec, cfg_norm)
        dec_norm = res_norm["decisions"][0]
        self.assertEqual(dec_norm["outcome"], "NORMAL")

        # Crucial check: Decision IDs must be distinct despite identical recording and window offsets
        self.assertNotEqual(
            dec_anom["decision_id"],
            dec_norm["decision_id"],
            "Decision ID must change when threshold changes outcome",
        )

    def test_decision_id_changes_when_source_sha256_changes(self) -> None:
        """TC-THRESH-22: Changing source_sha256 strictly changes the decision ID."""
        samples = [[1.0]] * 10
        rec1 = _make_valid_recording(samples, recording_id="rec_sha")
        cfg = _make_valid_config(config_id="cfg_sha", window_size=10, hop_size=10)

        res1 = evaluate_rms_threshold(rec1, cfg)
        id1 = res1["decisions"][0]["decision_id"]

        rec2 = dict(rec1)
        rec2["source_sha256"] = "f" * 64
        res2 = evaluate_rms_threshold(rec2, cfg)
        id2 = res2["decisions"][0]["decision_id"]

        self.assertNotEqual(id1, id2, "Decision ID must change when source_sha256 changes")

    def test_decision_id_changes_when_config_version_or_full_scale_changes(self) -> None:
        """TC-THRESH-23: Changing config_version or full_scale strictly changes the decision ID."""
        samples = [[1.0]] * 10
        rec = _make_valid_recording(samples)
        cfg_base = _make_valid_config(window_size=10, hop_size=10, config_version="1.0.0", full_scale=10.0)

        id_base = evaluate_rms_threshold(rec, cfg_base)["decisions"][0]["decision_id"]

        # Version change
        cfg_v2 = dict(cfg_base)
        cfg_v2["config_version"] = "1.0.1"
        id_v2 = evaluate_rms_threshold(rec, cfg_v2)["decisions"][0]["decision_id"]
        self.assertNotEqual(id_base, id_v2, "Decision ID must change when config_version changes")

        # Full-scale change
        cfg_fs = dict(cfg_base)
        cfg_fs["full_scale"] = 15.0
        id_fs = evaluate_rms_threshold(rec, cfg_fs)["decisions"][0]["decision_id"]
        self.assertNotEqual(id_base, id_fs, "Decision ID must change when full_scale changes")

    def test_decision_id_changes_when_sample_content_or_metadata_changes(self) -> None:
        """TC-THRESH-24: Changing sample content or metadata changes ID even with reused IDs."""
        rec1 = _make_valid_recording([[1.0]] * 10, recording_id="reused_rec_id")
        cfg = _make_valid_config(config_id="reused_cfg_id", window_size=10, hop_size=10)
        id1 = evaluate_rms_threshold(rec1, cfg)["decisions"][0]["decision_id"]

        # Same rec_id and cfg_id, but different sample values
        rec2 = _make_valid_recording([[1.01]] * 10, recording_id="reused_rec_id")
        id2 = evaluate_rms_threshold(rec2, cfg)["decisions"][0]["decision_id"]
        self.assertNotEqual(id1, id2, "Decision ID must change when sample values change")

        # Same rec_id and cfg_id, but different session_id
        rec3 = dict(rec1)
        rec3["session_id"] = "different_session"
        id3 = evaluate_rms_threshold(rec3, cfg)["decisions"][0]["decision_id"]
        self.assertNotEqual(id1, id3, "Decision ID must change when recording metadata changes")

    def test_decision_id_user_identifiers_with_underscores_no_tuple_collisions(self) -> None:
        """TC-THRESH-25: Underscores in user identifiers cannot create tuple collisions."""
        # rec_id="a_b", cfg_id="c" vs rec_id="a", cfg_id="b_c"
        # Naive delimiter concatenation dec_{rec}_{cfg} would both produce dec_a_b_c
        samples = [[1.0]] * 10
        rec_ab = _make_valid_recording(samples, recording_id="a_b")
        cfg_c = _make_valid_config(config_id="c", window_size=10, hop_size=10)
        id_ab_c = evaluate_rms_threshold(rec_ab, cfg_c)["decisions"][0]["decision_id"]

        rec_a = _make_valid_recording(samples, recording_id="a")
        cfg_bc = _make_valid_config(config_id="b_c", window_size=10, hop_size=10)
        id_a_bc = evaluate_rms_threshold(rec_a, cfg_bc)["decisions"][0]["decision_id"]

        self.assertNotEqual(
            id_ab_c,
            id_a_bc,
            "Structured hashing must prevent delimiter tuple collisions with underscores",
        )

    def test_decision_id_dictionary_insertion_order_invariant(self) -> None:
        """TC-THRESH-26: Dictionary insertion order does not alter decision IDs."""
        samples = [[1.0]] * 10
        rec = _make_valid_recording(samples)
        cfg = _make_valid_config(window_size=10, hop_size=10)

        # Reverse key insertion order for config
        cfg_rev = {k: cfg[k] for k in reversed(list(cfg.keys()))}
        # Reverse key insertion order for recording
        rec_rev = {k: rec[k] for k in reversed(list(rec.keys()))}

        res_std = evaluate_rms_threshold(rec, cfg)
        res_rev = evaluate_rms_threshold(rec_rev, cfg_rev)

        self.assertEqual(
            res_std["decisions"][0]["decision_id"],
            res_rev["decisions"][0]["decision_id"],
            "Decision ID must be invariant to dictionary key insertion order",
        )

    def test_configuration_alias_bypass_rejection(self) -> None:
        """TC-THRESH-27: Aliases and boolean bypass combinations are strictly rejected."""
        base_cfg = _make_valid_config()

        # 1. Exact supervisor reproducer: canonical threshold=1 alongside rms_threshold=True
        bad_bypass = dict(base_cfg)
        bad_bypass["threshold"] = 1
        bad_bypass["rms_threshold"] = True
        with self.assertRaises(ConfigurationError) as ctx:
            validate_threshold_configuration(bad_bypass)
        self.assertIn("unknown field", str(ctx.exception).lower())

        # 2. Unrequested aliases rejected as unknown fields
        for alias_key in ("rms_threshold", "sample_rate_hz", "acceleration_unit", "channels", "version"):
            bad_alias = dict(base_cfg)
            bad_alias[alias_key] = 123
            with self.assertRaises(ConfigurationError) as ctx:
                validate_threshold_configuration(bad_alias)
            self.assertIn("unknown field", str(ctx.exception).lower())

        # 3. Direct boolean in threshold
        bad_bool_thresh = dict(base_cfg)
        bad_bool_thresh["threshold"] = True
        with self.assertRaises(ConfigurationError) as ctx:
            validate_threshold_configuration(bad_bool_thresh)
        self.assertIn("boolean", str(ctx.exception).lower())

    def test_reproducer_integers_above_2_pow_53_distinct_ids(self) -> None:
        """TC-THRESH-28: Lossless recording identity for samples > 2**53.

        Exact supervisor reproducer:
        - single channel x
        - window_size=1, hop_size=1
        - full_scale=2**53 + 4
        - threshold=2**53 + 8
        Sample A: [[2**53 + 3]] -> NORMAL
        Sample B: [[2**53 + 4]] -> INSUFFICIENT_EVIDENCE (due to saturation)
        Both must produce distinct decision IDs.
        """
        fs = 2**53 + 4
        th = 2**53 + 8
        cfg = _make_valid_config(
            config_id="cfg_reproducer_large_int",
            window_size=1,
            hop_size=1,
            full_scale=fs,
            threshold=th,
        )
        rec_a = _make_valid_recording([[2**53 + 3]], channels=["x"], recording_id="rec_large_int")
        rec_b = _make_valid_recording([[2**53 + 4]], channels=["x"], recording_id="rec_large_int")

        res_a = evaluate_rms_threshold(rec_a, cfg)
        res_b = evaluate_rms_threshold(rec_b, cfg)

        dec_a = res_a["decisions"][0]
        dec_b = res_b["decisions"][0]

        self.assertEqual(dec_a["outcome"], "NORMAL")
        self.assertEqual(dec_b["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertIn(REASON_SENSOR_SATURATION_POSSIBLE, dec_b["reason_codes"])

        self.assertNotEqual(
            dec_a["decision_id"],
            dec_b["decision_id"],
            "Lossless recording fingerprint must distinguish 2**53+3 from 2**53+4",
        )

    def test_adjacent_integers_above_2_pow_53_mutually_distinguishable(self) -> None:
        """TC-THRESH-29: Adjacent integers above 2**53 produce distinct canonical fingerprints and decision IDs."""
        base_int = 2**53
        test_integers = [base_int + i for i in range(10)]
        cfg = _make_valid_config(
            config_id="cfg_adj_large",
            window_size=1,
            hop_size=1,
            full_scale=base_int + 100,
            threshold=base_int + 50,
        )

        seen_fingerprints = set()
        seen_decision_ids = set()

        for val in test_integers:
            rec = _make_valid_recording([[val]], channels=["x"], recording_id="rec_adj_test")
            res = evaluate_rms_threshold(rec, cfg)
            dec = res["decisions"][0]

            rec_fp = compute_canonical_recording_fingerprint(rec)

            self.assertNotIn(rec_fp, seen_fingerprints, f"Fingerprint collision for integer {val}")
            self.assertNotIn(dec["decision_id"], seen_decision_ids, f"Decision ID collision for integer {val}")

            seen_fingerprints.add(rec_fp)
            seen_decision_ids.add(dec["decision_id"])

        self.assertEqual(len(seen_fingerprints), len(test_integers))
        self.assertEqual(len(seen_decision_ids), len(test_integers))

        # Also verify dictionary key reordering on large-int recording preserves decision ID
        rec_large = _make_valid_recording([[base_int + 3]], channels=["x"], recording_id="rec_order_test")
        rec_large_rev = {k: rec_large[k] for k in reversed(list(rec_large.keys()))}
        res_orig = evaluate_rms_threshold(rec_large, cfg)
        res_rev = evaluate_rms_threshold(rec_large_rev, cfg)
        self.assertEqual(
            res_orig["decisions"][0]["decision_id"],
            res_rev["decisions"][0]["decision_id"],
            "Key reordering on large integer recording must preserve decision ID",
        )

    def test_int_and_float_distinct_identity_policy(self) -> None:
        """TC-THRESH-30: Policy distinction between int and float representations in canonical fingerprinting."""
        cfg_int = _make_valid_config(expected_sample_rate_hz=1000, full_scale=10, threshold=2)
        cfg_flt = _make_valid_config(expected_sample_rate_hz=1000.0, full_scale=10.0, threshold=2.0)

        fp_cfg_int = compute_canonical_configuration_fingerprint(cfg_int)
        fp_cfg_flt = compute_canonical_configuration_fingerprint(cfg_flt)
        self.assertNotEqual(fp_cfg_int, fp_cfg_flt, "Config with int vs float parameters must produce distinct fingerprints")

        rec_int = _make_valid_recording([[1]], sample_rate_hz=1000, recording_id="rec_type_test")
        rec_flt = _make_valid_recording([[1.0]], sample_rate_hz=1000.0, recording_id="rec_type_test")

        fp_rec_int = compute_canonical_recording_fingerprint(rec_int)
        fp_rec_flt = compute_canonical_recording_fingerprint(rec_flt)
        self.assertNotEqual(fp_rec_int, fp_rec_flt, "Recording with int vs float samples must produce distinct fingerprints")

        id_int = evaluate_rms_threshold(rec_int, cfg_int)["decisions"][0]["decision_id"]
        id_flt = evaluate_rms_threshold(rec_flt, cfg_flt)["decisions"][0]["decision_id"]
        self.assertNotEqual(id_int, id_flt, "Decisions derived from int vs float inputs must produce distinct IDs")

    def test_caller_immutability(self) -> None:
        """TC-THRESH-18: Caller recording and configuration are never mutated."""
        samples = [[1.0, 2.0]] * 15
        rec = _make_valid_recording(samples, channels=["x", "y"])
        cfg = _make_valid_config(expected_channels=["x", "y"], window_size=10, hop_size=5)

        rec_copy = copy.deepcopy(rec)
        cfg_copy = copy.deepcopy(cfg)

        _ = evaluate_rms_threshold(rec, cfg)

        self.assertEqual(rec, rec_copy)
        self.assertEqual(cfg, cfg_copy)

    def test_invalid_configurations_rejected(self) -> None:
        """TC-THRESH-19: Comprehensive validation of invalid configuration schemas."""
        base_cfg = _make_valid_config()

        # 1. Missing required field
        for key in ("config_id", "window_size", "full_scale", "threshold"):
            bad = copy.deepcopy(base_cfg)
            del bad[key]
            with self.assertRaises(ConfigurationError):
                validate_threshold_configuration(bad)

        # 2. Unknown field
        bad_unknown = copy.deepcopy(base_cfg)
        bad_unknown["extra_field"] = 42
        with self.assertRaises(ConfigurationError):
            validate_threshold_configuration(bad_unknown)

        # 3. Booleans masquerading as numbers
        for key in ("window_size", "hop_size", "expected_sample_rate_hz", "full_scale", "threshold"):
            bad_bool = copy.deepcopy(base_cfg)
            bad_bool[key] = True
            with self.assertRaises(ConfigurationError):
                validate_threshold_configuration(bad_bool)

        # 4. Invalid window parameters
        bad_hop = copy.deepcopy(base_cfg)
        bad_hop["window_size"] = 10
        bad_hop["hop_size"] = 20  # hop > window
        with self.assertRaises(ConfigurationError):
            validate_threshold_configuration(bad_hop)

        bad_zero_win = copy.deepcopy(base_cfg)
        bad_zero_win["window_size"] = 0
        with self.assertRaises(ConfigurationError):
            validate_threshold_configuration(bad_zero_win)

        # 5. Non-finite values
        for bad_val in (float("nan"), float("inf"), float("-inf")):
            bad_nan = copy.deepcopy(base_cfg)
            bad_nan["threshold"] = bad_val
            with self.assertRaises(ConfigurationError):
                validate_threshold_configuration(bad_nan)

        # 6. Oversized integers (prevent OverflowError leak)
        for key in ("expected_sample_rate_hz", "full_scale", "threshold"):
            bad_huge = copy.deepcopy(base_cfg)
            bad_huge[key] = 10**400
            with self.assertRaises(ConfigurationError):
                validate_threshold_configuration(bad_huge)

        # 7. Non-positive full_scale and negative threshold
        bad_fs = copy.deepcopy(base_cfg)
        bad_fs["full_scale"] = 0.0
        with self.assertRaises(ConfigurationError):
            validate_threshold_configuration(bad_fs)

        bad_thresh = copy.deepcopy(base_cfg)
        bad_thresh["threshold"] = -1.0
        with self.assertRaises(ConfigurationError):
            validate_threshold_configuration(bad_thresh)

        # 8. Unsupported and duplicate channels
        bad_ch = copy.deepcopy(base_cfg)
        bad_ch["expected_channels"] = ["x", "invalid"]
        with self.assertRaises(ConfigurationError):
            validate_threshold_configuration(bad_ch)

        bad_dup = copy.deepcopy(base_cfg)
        bad_dup["expected_channels"] = ["x", "x"]
        with self.assertRaises(ConfigurationError):
            validate_threshold_configuration(bad_dup)

    def test_end_to_end_csv_to_decisions_integration(self) -> None:
        """TC-THRESH-20: Full pipeline: CSV file on disk -> load -> features -> threshold evaluation."""
        csv_text = "x,y\n0.5,1.0\n0.6,1.2\n0.5,1.1\n0.4,0.9\n0.5,1.0\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "vibration.csv"
            csv_path.write_text(csv_text, encoding="utf-8")

            metadata = {
                "schema_version": "1.0.0",
                "recording_id": "rec_csv_pipeline_01",
                "machine_id": "machine_alpha",
                "session_id": "sess_01",
                "source_id": "sensor_front",
                "provenance": "recorded",
                "sample_rate_hz": 1000.0,
                "acceleration_unit": "g",
            }

            recording = load_vibration_csv(csv_path, metadata)
            self.assertEqual(recording["recording_id"], "rec_csv_pipeline_01")
            self.assertEqual(len(recording["samples"]), 5)

            # Extract window features (Stage 02)
            features = extract_window_features(recording, window_size=5, hop_size=5)
            self.assertEqual(features["status"], "SUCCESS")

            # Evaluate against threshold reference (Stage 04)
            cfg = {
                "config_id": "pipeline_cfg_01",
                "config_version": "1.0.0",
                "window_size": 5,
                "hop_size": 5,
                "expected_sample_rate_hz": 1000.0,
                "expected_acceleration_unit": "g",
                "expected_channels": ["x", "y"],
                "full_scale": 16.0,
                "threshold": 0.8,
            }

            eval_result = evaluate_rms_threshold(recording, cfg)
            self.assertEqual(eval_result["status"], "COMPLETED")
            self.assertEqual(len(eval_result["decisions"]), 1)

            dec = eval_result["decisions"][0]
            validate_anomaly_decision(dec)
            # Channel y RMS is ~1.04, which exceeds threshold 0.8 -> ANOMALY
            self.assertEqual(dec["outcome"], "ANOMALY")
            self.assertEqual(dec["quality_status"], "PASS")
            self.assertGreater(dec["score"], 0.8)


if __name__ == "__main__":
    unittest.main()
