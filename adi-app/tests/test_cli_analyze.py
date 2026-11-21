"""Subprocess and integration test suite for Stage 05: adi-portfolio analyze CLI.

Verifies:
- End-to-end synthetic CSV with NORMAL, ANOMALY, and saturated windows
- Recording-level envelope mismatch abstention
- Threshold equality producing ANOMALY
- Expected SHA-256 digest match and mismatch semantics
- Rejection of missing files, malformed JSON, duplicate keys, nonstandard constants, non-object JSON
- Rejection of malformed CSV, empty CSV, and bare CR line endings
- Rejection of invalid configuration schemas and boolean aliases
- Verification of exit code 2 on errors with absence of stdout
- Repeatability (byte-for-byte identical JSON output across runs)
- Caller input immutability (CSV, metadata, config unchanged on disk)
- Decision contract validation and independent count reconciliation
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Tuple
import unittest

from adi_common import validate_anomaly_decision
from adi_app.portfolio import main


def _run_cli_subprocess(args: List[str]) -> Tuple[int, str, str]:
    """Execute adi-portfolio via python -m adi_app.portfolio in a separate subprocess."""
    cmd = [sys.executable, "-m", "adi_app.portfolio"] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


class TestCLIAnalyze(unittest.TestCase):
    """Test suite for adi-portfolio analyze command."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

        # Standard valid metadata
        self.metadata: Dict[str, Any] = {
            "schema_version": "1.0.0",
            "recording_id": "rec_test_cli_01",
            "machine_id": "mach_cli_01",
            "session_id": "sess_cli_01",
            "source_id": "sensor_front",
            "provenance": "synthetic",
            "sample_rate_hz": 1000.0,
            "acceleration_unit": "g",
        }

        # Standard valid configuration (window 5, hop 5, full_scale 10.0, threshold 2.0)
        self.config: Dict[str, Any] = {
            "config_id": "cfg_test_cli_01",
            "config_version": "1.0.0",
            "window_size": 5,
            "hop_size": 5,
            "expected_sample_rate_hz": 1000.0,
            "expected_acceleration_unit": "g",
            "expected_channels": ["x"],
            "full_scale": 10.0,
            "threshold": 2.0,
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_files(
        self,
        csv_content: str | bytes,
        metadata_dict: Any = None,
        config_dict: Any = None,
        *,
        metadata_raw: str | None = None,
        config_raw: str | None = None,
    ) -> Tuple[Path, Path, Path]:
        """Helper to write CSV, metadata, and config to temporary directory."""
        csv_path = self.dir_path / "data.csv"
        if isinstance(csv_content, bytes):
            csv_path.write_bytes(csv_content)
        else:
            csv_path.write_text(csv_content, encoding="utf-8")

        meta_path = self.dir_path / "metadata.json"
        if metadata_raw is not None:
            meta_path.write_text(metadata_raw, encoding="utf-8")
        else:
            meta_path.write_text(
                json.dumps(metadata_dict if metadata_dict is not None else self.metadata),
                encoding="utf-8",
            )

        cfg_path = self.dir_path / "config.json"
        if config_raw is not None:
            cfg_path.write_text(config_raw, encoding="utf-8")
        else:
            cfg_path.write_text(
                json.dumps(config_dict if config_dict is not None else self.config),
                encoding="utf-8",
            )

        return csv_path, meta_path, cfg_path

    # =========================================================================
    # 1. Success Cases & Subprocess Verification
    # =========================================================================

    def test_end_to_end_normal_anomaly_saturation_windows_subprocess(self) -> None:
        """TC-CLI-01: CSV with distinct NORMAL, saturated (abstention), and ANOMALY windows."""
        # Window size 5, hop 5. Total 15 samples:
        # Window 0 (0..5): samples=1.0 -> RMS=1.0 < 2.0 -> NORMAL
        # Window 1 (5..10): contains sample 15.0 (>= full_scale 10.0) -> INSUFFICIENT_EVIDENCE
        # Window 2 (10..15): samples=3.0 -> RMS=3.0 >= 2.0 -> ANOMALY
        w0 = "1.0\n" * 5
        w1 = "1.0\n1.0\n15.0\n1.0\n1.0\n"
        w2 = "3.0\n" * 5
        csv_text = "x\n" + w0 + w1 + w2

        csv_p, meta_p, cfg_p = self._write_files(csv_text)

        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )

        self.assertEqual(code, 0, f"Expected exit code 0, got {code}. Stderr: {stderr}")
        self.assertEqual(stderr, "")

        doc = json.loads(stdout)
        self.assertEqual(doc["command"], "analyze")
        self.assertEqual(doc["schema_version"], "1.0.0")
        self.assertEqual(doc["status"], "COMPLETED")
        self.assertEqual(doc["execution_environment"], "HOST_CPU")
        self.assertEqual(doc["evaluation_scope"], "uncalibrated_engineering_reference")
        self.assertEqual(len(doc["window_boundaries"]), 3)
        self.assertEqual(len(doc["decisions"]), 3)

        dec0, dec1, dec2 = doc["decisions"]
        validate_anomaly_decision(dec0)
        validate_anomaly_decision(dec1)
        validate_anomaly_decision(dec2)

        # Window 0: NORMAL
        self.assertEqual(dec0["outcome"], "NORMAL")
        self.assertEqual(dec0["quality_status"], "PASS")
        self.assertEqual(dec0["score"], 1.0)

        # Window 1: INSUFFICIENT_EVIDENCE (Saturation)
        self.assertEqual(dec1["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec1["quality_status"], "INDETERMINATE")
        self.assertIsNone(dec1["score"])
        self.assertIn("SENSOR_SATURATION_POSSIBLE", dec1["reason_codes"])

        # Window 2: ANOMALY
        self.assertEqual(dec2["outcome"], "ANOMALY")
        self.assertEqual(dec2["quality_status"], "PASS")
        self.assertEqual(dec2["score"], 3.0)

        # Count summary reconciliation
        summary = doc["summary"]
        self.assertEqual(summary["total_decisions"], 3)
        self.assertEqual(summary["normal_count"], 1)
        self.assertEqual(summary["anomaly_count"], 1)
        self.assertEqual(summary["insufficient_evidence_count"], 1)
        self.assertFalse(summary["recording_level_abstention"])
        self.assertEqual(
            summary["window_level_counts"],
            {"normal": 1, "anomaly": 1, "insufficient_evidence": 1},
        )
        self.assertEqual(
            summary["total_decisions"],
            summary["normal_count"] + summary["anomaly_count"] + summary["insufficient_evidence_count"],
        )

    def test_recording_level_mismatch_abstention_subprocess(self) -> None:
        """TC-CLI-02: Recording-level operational envelope mismatch abstention."""
        csv_text = "x\n1.0\n1.0\n1.0\n1.0\n1.0\n"
        bad_meta = dict(self.metadata)
        bad_meta["sample_rate_hz"] = 500.0  # config expects 1000.0

        csv_p, meta_p, cfg_p = self._write_files(csv_text, metadata_dict=bad_meta)

        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )

        self.assertEqual(code, 0, f"Abstention must return exit code 0. Stderr: {stderr}")
        self.assertEqual(stderr, "")

        doc = json.loads(stdout)
        self.assertEqual(doc["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(doc["window_boundaries"], [])
        self.assertEqual(len(doc["decisions"]), 1)

        dec = doc["decisions"][0]
        validate_anomaly_decision(dec)
        self.assertEqual(dec["outcome"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(dec["quality_status"], "FAIL")
        self.assertIn("SAMPLE_RATE_MISMATCH", dec["reason_codes"])

        summary = doc["summary"]
        self.assertEqual(summary["total_decisions"], 1)
        self.assertEqual(summary["normal_count"], 0)
        self.assertEqual(summary["anomaly_count"], 0)
        self.assertEqual(summary["insufficient_evidence_count"], 1)
        self.assertTrue(summary["recording_level_abstention"])
        self.assertEqual(
            summary["window_level_counts"],
            {"normal": 0, "anomaly": 0, "insufficient_evidence": 0},
        )

    def test_threshold_equality_produces_anomaly_subprocess(self) -> None:
        """TC-CLI-03: Score exactly equal to threshold produces ANOMALY decision."""
        # 5 samples of 2.0 -> RMS = 2.0. Config threshold = 2.0.
        csv_text = "x\n2.0\n2.0\n2.0\n2.0\n2.0\n"
        csv_p, meta_p, cfg_p = self._write_files(csv_text)

        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )

        self.assertEqual(code, 0)
        doc = json.loads(stdout)
        self.assertEqual(len(doc["decisions"]), 1)
        self.assertEqual(doc["decisions"][0]["outcome"], "ANOMALY")
        self.assertEqual(doc["decisions"][0]["score"], 2.0)
        self.assertEqual(doc["summary"]["anomaly_count"], 1)

    def test_expected_sha256_match_and_mismatch_subprocess(self) -> None:
        """TC-CLI-04: --expected-sha256 option succeeds on match and fails on mismatch."""
        csv_bytes = b"x\n1.0\n1.0\n1.0\n1.0\n1.0\n"
        actual_sha256 = hashlib.sha256(csv_bytes).hexdigest()

        csv_p, meta_p, cfg_p = self._write_files(csv_bytes)

        # 1. Matching SHA-256 succeeds
        code, stdout, stderr = _run_cli_subprocess(
            [
                "analyze",
                "--csv",
                str(csv_p),
                "--metadata",
                str(meta_p),
                "--config",
                str(cfg_p),
                "--expected-sha256",
                actual_sha256,
            ]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        doc = json.loads(stdout)
        self.assertEqual(doc["source_sha256"], actual_sha256)

        # 2. Mismatched SHA-256 exits with code 2, empty stdout, diagnostic on stderr
        wrong_sha256 = "0" * 64
        code_mismatch, stdout_mismatch, stderr_mismatch = _run_cli_subprocess(
            [
                "analyze",
                "--csv",
                str(csv_p),
                "--metadata",
                str(meta_p),
                "--config",
                str(cfg_p),
                "--expected-sha256",
                wrong_sha256,
            ]
        )
        self.assertEqual(code_mismatch, 2)
        self.assertEqual(stdout_mismatch, "")
        self.assertIn("does not match expected", stderr_mismatch)

        # 3. Invalid syntax (not 64 hex characters) exits with code 2
        code_syntax, stdout_syntax, stderr_syntax = _run_cli_subprocess(
            [
                "analyze",
                "--csv",
                str(csv_p),
                "--metadata",
                str(meta_p),
                "--config",
                str(cfg_p),
                "--expected-sha256",
                "invalid_hex_string",
            ]
        )
        self.assertEqual(code_syntax, 2)
        self.assertEqual(stdout_syntax, "")
        self.assertIn("Invalid expected_sha256 syntax", stderr_syntax)

    # =========================================================================
    # 2. Strict File & JSON Validation Error Cases (Exit 2)
    # =========================================================================

    def test_missing_files_exit_code_2(self) -> None:
        """TC-CLI-05: Missing CSV, metadata, or config files produce exit 2 and clear stderr."""
        csv_p, meta_p, cfg_p = self._write_files("x\n1.0\n")

        # Missing CSV
        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(self.dir_path / "nonexistent.csv"), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("nonexistent.csv", stderr)

        # Missing Metadata
        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(self.dir_path / "nonexistent_meta.json"), "--config", str(cfg_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("nonexistent_meta.json", stderr)

        # Missing Config
        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(self.dir_path / "nonexistent_cfg.json")]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("nonexistent_cfg.json", stderr)

    def test_malformed_json_syntax_error_exit_code_2(self) -> None:
        """TC-CLI-06: Malformed JSON syntax in metadata or config produces exit 2 with line/col."""
        csv_p, meta_p, cfg_p = self._write_files("x\n1.0\n", metadata_raw='{"schema_version": "1.0.0", bad_json}')

        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Malformed JSON in metadata", stderr)
        self.assertIn(str(meta_p), stderr)

    def test_duplicate_keys_in_json_rejected_exit_code_2(self) -> None:
        """TC-CLI-07: Duplicate keys in metadata or configuration JSON produce exit 2."""
        dup_json = '{"config_id": "c1", "config_id": "c2", "window_size": 5}'
        csv_p, meta_p, cfg_p = self._write_files("x\n1.0\n", config_raw=dup_json)

        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Duplicate key 'config_id'", stderr)
        self.assertIn(str(cfg_p), stderr)

    def test_nonstandard_json_constants_rejected_exit_code_2(self) -> None:
        """TC-CLI-08: Nonstandard constants (NaN, Infinity, -Infinity) produce exit 2."""
        for const in ("NaN", "Infinity", "-Infinity"):
            bad_json = f'{{"config_id": "c1", "config_version": "1.0.0", "threshold": {const}}}'
            csv_p, meta_p, cfg_p = self._write_files("x\n1.0\n", config_raw=bad_json)

            code, stdout, stderr = _run_cli_subprocess(
                ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
            )
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn(f"Nonstandard JSON constant '{const}' is forbidden", stderr)

    def test_non_object_top_level_json_rejected_exit_code_2(self) -> None:
        """TC-CLI-09: Top-level JSON arrays or primitives produce exit 2."""
        for non_obj in ('[{"schema_version": "1.0.0"}]', '"a string"', "123", "true"):
            csv_p, meta_p, cfg_p = self._write_files("x\n1.0\n", metadata_raw=non_obj)

            code, stdout, stderr = _run_cli_subprocess(
                ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
            )
            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("must be an object", stderr)

    def test_empty_json_file_exit_code_2(self) -> None:
        """TC-CLI-10: Empty JSON files (0 bytes) produce exit 2."""
        csv_p, meta_p, cfg_p = self._write_files("x\n1.0\n", metadata_raw="")

        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("empty (0 bytes)", stderr)

    # =========================================================================
    # 3. CSV & Ingestion Error Cases (Exit 2)
    # =========================================================================

    def test_invalid_csv_parsing_exit_code_2(self) -> None:
        """TC-CLI-11: Malformed CSV (mismatched columns, bare CR, unterminated quotes) produce exit 2."""
        # Row with missing column
        csv_p, meta_p, cfg_p = self._write_files("x,y\n1.0,\n")
        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("CSV parse error", stderr)

        # Unterminated quote
        csv_p2, meta_p2, cfg_p2 = self._write_files(b'x\n"1')
        code2, stdout2, stderr2 = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p2), "--metadata", str(meta_p2), "--config", str(cfg_p2)]
        )
        self.assertEqual(code2, 2)
        self.assertEqual(stdout2, "")
        self.assertIn("CSV parse error", stderr2)

        # Bare CR
        csv_p3, meta_p3, cfg_p3 = self._write_files(b"x\r1.0\n")
        code3, stdout3, stderr3 = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p3), "--metadata", str(meta_p3), "--config", str(cfg_p3)]
        )
        self.assertEqual(code3, 2)
        self.assertEqual(stdout3, "")
        self.assertIn("Bare CR", stderr3)

    def test_invalid_configuration_schema_exit_code_2(self) -> None:
        """TC-CLI-12: Invalid configuration schema (unknown keys, boolean aliases) produce exit 2."""
        bad_cfg = dict(self.config)
        bad_cfg["rms_threshold"] = True  # unrequested alias / boolean bypass

        csv_p, meta_p, cfg_p = self._write_files("x\n1.0\n", config_dict=bad_cfg)
        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Disallowed unknown field", stderr)

    def test_missing_required_cli_arguments_exit_code_2(self) -> None:
        """TC-CLI-13: Missing required CLI arguments produce exit 2."""
        csv_p, meta_p, cfg_p = self._write_files("x\n1.0\n")

        # Missing --config
        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")

        # Bare command without subcommand
        code2, stdout2, stderr2 = _run_cli_subprocess([])
        self.assertEqual(code2, 2)

    # =========================================================================
    # 4. Repeatability & Input Immutability
    # =========================================================================

    def test_repeatability_and_identical_output_bytes(self) -> None:
        """TC-CLI-14: Running the same analysis twice produces byte-for-byte identical output."""
        csv_text = "x\n1.0\n2.0\n1.5\n0.8\n1.2\n"
        csv_p, meta_p, cfg_p = self._write_files(csv_text)

        args = ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        code1, stdout1, stderr1 = _run_cli_subprocess(args)
        code2, stdout2, stderr2 = _run_cli_subprocess(args)

        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        self.assertEqual(stdout1, stdout2, "Analysis output must be byte-for-byte deterministic across runs")

    def test_input_files_remain_unchanged_on_disk(self) -> None:
        """TC-CLI-15: CLI is strictly read-only and never modifies input files on disk."""
        csv_text = "x\n1.0\n2.0\n1.5\n0.8\n1.2\n"
        csv_p, meta_p, cfg_p = self._write_files(csv_text)

        csv_before = csv_p.read_bytes()
        meta_before = meta_p.read_bytes()
        cfg_before = cfg_p.read_bytes()

        code, stdout, stderr = _run_cli_subprocess(
            ["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)]
        )
        self.assertEqual(code, 0)

        self.assertEqual(csv_p.read_bytes(), csv_before, "CSV file modified by analyze command")
        self.assertEqual(meta_p.read_bytes(), meta_before, "Metadata file modified by analyze command")
        self.assertEqual(cfg_p.read_bytes(), cfg_before, "Config file modified by analyze command")

    # =========================================================================
    # 5. In-Process main() Direct Execution
    # =========================================================================

    def test_in_process_main_returns_expected_exit_codes(self) -> None:
        """TC-CLI-16: Calling main(argv) in-process returns expected exit codes without raising."""
        import contextlib
        import io

        csv_text = "x\n1.0\n2.0\n1.5\n0.8\n1.2\n"
        csv_p, meta_p, cfg_p = self._write_files(csv_text)

        # Successful call returns 0
        buf_out = io.StringIO()
        buf_err = io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            code = main(["analyze", "--csv", str(csv_p), "--metadata", str(meta_p), "--config", str(cfg_p)])
        self.assertEqual(code, 0)
        self.assertIn('"command": "analyze"', buf_out.getvalue())
        self.assertEqual(buf_err.getvalue(), "")

        # Error call returns 2
        buf_err2 = io.StringIO()
        buf_out2 = io.StringIO()
        with contextlib.redirect_stdout(buf_out2), contextlib.redirect_stderr(buf_err2):
            code_err = main(["analyze", "--csv", "nonexistent.csv", "--metadata", str(meta_p), "--config", str(cfg_p)])
        self.assertEqual(code_err, 2)
        self.assertEqual(buf_out2.getvalue(), "")
        self.assertIn("nonexistent.csv", buf_err2.getvalue())


if __name__ == "__main__":
    unittest.main()
