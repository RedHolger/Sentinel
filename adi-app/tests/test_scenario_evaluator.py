"""Subprocess and integration tests for Stage 06: scenario evaluation suite.

Verifies:
- All-match synthetic scenario suites via real subprocess (exit 0)
- Deliberately wrong expectation producing MISMATCH (exit 1)
- Missing or corrupt CSV producing ERROR (exit 1)
- Digest mismatch producing ERROR (exit 1)
- Recorded provenance rejection producing ERROR (exit 1)
- Mixed match/mismatch/error accounting with strict denominator retention
- All-abstention suite accounting
- Empty suite rejection (exit 2)
- Duplicate case ID rejection (exit 2)
- Malformed suite JSON and duplicate keys rejection (exit 2)
- Relative path resolution from unrelated working directory
- Repeatability (byte-for-byte identical output across runs)
- Caller input immutability (manifest, CSV, metadata, config unchanged on disk)
- Preserved legacy evaluate without --suite (exit 1, NOT_IMPLEMENTED)
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Tuple
import unittest

from adi_app.portfolio import main


def _run_cli_subprocess(args: List[str], cwd: Path | None = None) -> Tuple[int, str, str]:
    """Execute adi-portfolio via python -m adi_app.portfolio in a separate subprocess."""
    cmd = [sys.executable, "-m", "adi_app.portfolio"] + args
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return res.returncode, res.stdout, res.stderr


class TestScenarioEvaluator(unittest.TestCase):
    """Test suite for adi-portfolio evaluate --suite command."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

        # Baseline checked-in suite path
        self.fixtures_dir = Path(__file__).resolve().parent / "fixtures" / "scenarios"
        self.checked_in_suite_path = self.fixtures_dir / "suite_all_pass.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # =========================================================================
    # 1. Success Suite & Checked-in Fixtures
    # =========================================================================

    def test_checked_in_fixtures_all_pass_subprocess(self) -> None:
        """TC-SCEN-01: Checked-in synthetic suite passes with exit 0 and all cases MATCH."""
        code, stdout, stderr = _run_cli_subprocess(
            ["evaluate", "--suite", str(self.checked_in_suite_path)]
        )

        self.assertEqual(code, 0, f"Expected exit code 0, got {code}. Stderr: {stderr}")
        self.assertEqual(stderr, "")

        doc = json.loads(stdout)
        self.assertEqual(doc["command"], "evaluate")
        self.assertEqual(doc["schema_version"], "1.0.0")
        self.assertEqual(doc["status"], "PASS")
        self.assertEqual(doc["evaluation_scope"], "synthetic_behavioral_regression")
        self.assertEqual(doc["execution_environment"], "HOST_CPU")
        self.assertTrue(len(doc["suite_sha256"]) == 64)

        summary = doc["summary"]
        self.assertEqual(summary["total_cases"], 7)
        self.assertEqual(summary["matched_cases"], 7)
        self.assertEqual(summary["mismatched_cases"], 0)
        self.assertEqual(summary["error_cases"], 0)

        decs_summary = summary["actual_decisions_summary"]
        self.assertEqual(decs_summary["total_decisions"], 9)
        self.assertEqual(decs_summary["normal_count"], 2)
        self.assertEqual(decs_summary["anomaly_count"], 3)
        self.assertEqual(decs_summary["insufficient_evidence_count"], 4)
        self.assertEqual(decs_summary["recording_level_decisions"], 2)
        self.assertEqual(decs_summary["window_level_decisions"], 7)

        # Invariant checks
        self.assertEqual(
            decs_summary["total_decisions"],
            decs_summary["normal_count"] + decs_summary["anomaly_count"] + decs_summary["insufficient_evidence_count"],
        )
        self.assertEqual(
            decs_summary["total_decisions"],
            decs_summary["recording_level_decisions"] + decs_summary["window_level_decisions"],
        )

        # Expected decisions summary checks
        exp_summary = summary["expected_decisions_summary"]
        self.assertEqual(exp_summary["total_decisions"], 9)
        self.assertEqual(exp_summary["normal_count"], 2)
        self.assertEqual(exp_summary["anomaly_count"], 3)
        self.assertEqual(exp_summary["insufficient_evidence_count"], 4)
        self.assertEqual(exp_summary["recording_level_decisions"], 2)
        self.assertEqual(exp_summary["window_level_decisions"], 7)
        self.assertEqual(exp_summary["recording_level_abstention_count"], 2)
        self.assertEqual(exp_summary["window_abstention_count"], 2)

        # Reconciled breakdowns by scope and outcome
        self.assertEqual(exp_summary["by_scope"]["recording_envelope"], 2)
        self.assertEqual(exp_summary["by_scope"]["window"], 7)
        self.assertEqual(decs_summary["by_scope"]["recording_envelope"], 2)
        self.assertEqual(decs_summary["by_scope"]["window"], 7)

        for case in doc["cases"]:
            self.assertEqual(case["status"], "MATCH")
            self.assertEqual(case["details"], [])
            self.assertIsNotNone(case["input_hashes"]["csv_sha256"])
            self.assertIsNotNone(case["input_hashes"]["metadata_sha256"])
            self.assertIsNotNone(case["input_hashes"]["config_sha256"])
            self.assertIn("expected_result", case)
            self.assertIn("actual_window_boundaries", case)
            if case["actual_scope"] == "window":
                self.assertGreater(len(case["actual_window_boundaries"]), 0)
            elif case["actual_scope"] == "recording_envelope":
                self.assertEqual(case["actual_window_boundaries"], [])

    # =========================================================================
    # 2. Mismatch, Error, and Provenance Handling
    # =========================================================================

    def test_deliberately_wrong_expectation_produces_mismatch_and_exit_1(self) -> None:
        """TC-SCEN-02: Deliberately wrong expectation produces MISMATCH and exit 1."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        # Flip case 1 expected outcome from NORMAL to ANOMALY
        suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
        suite_data["cases"][0]["expected_result"]["windows"][0]["outcome"] = "ANOMALY"

        bad_suite_path = self.fixtures_dir / "temp_mismatch_suite.json"
        try:
            bad_suite_path.write_text(json.dumps(suite_data, indent=2), encoding="utf-8")

            code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(bad_suite_path)])
            self.assertEqual(code, 1, f"Mismatch must produce exit 1, got {code}")
            self.assertEqual(stderr, "")

            doc = json.loads(stdout)
            self.assertEqual(doc["status"], "FAIL")
            self.assertEqual(doc["summary"]["total_cases"], 1)
            self.assertEqual(doc["summary"]["matched_cases"], 0)
            self.assertEqual(doc["summary"]["mismatched_cases"], 1)
            self.assertEqual(doc["summary"]["error_cases"], 0)

            case_res = doc["cases"][0]
            self.assertEqual(case_res["status"], "MISMATCH")
            self.assertTrue(any("outcome expected 'ANOMALY', got 'NORMAL'" in d for d in case_res["details"]))
        finally:
            if bad_suite_path.exists():
                bad_suite_path.unlink()

    def test_missing_csv_produces_error_and_exit_1(self) -> None:
        """TC-SCEN-03: Missing CSV file produces ERROR status and exit 1."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
        suite_data["cases"][0]["csv_path"] = "missing_recording.csv"

        suite_path = self.fixtures_dir / "temp_missing_csv_suite.json"
        try:
            suite_path.write_text(json.dumps(suite_data, indent=2), encoding="utf-8")

            code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(suite_path)])
            self.assertEqual(code, 1)
            self.assertEqual(stderr, "")

            doc = json.loads(stdout)
            self.assertEqual(doc["status"], "FAIL")
            self.assertEqual(doc["summary"]["error_cases"], 1)
            self.assertEqual(doc["cases"][0]["status"], "ERROR")
            self.assertTrue(any("CSV file not found" in d for d in doc["cases"][0]["details"]))
            self.assertIsNone(doc["cases"][0]["input_hashes"]["csv_sha256"])
        finally:
            if suite_path.exists():
                suite_path.unlink()

    def test_digest_mismatch_produces_error_and_exit_1(self) -> None:
        """TC-SCEN-04: Mismatched CSV expected SHA-256 produces ERROR and exit 1."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
        suite_data["cases"][0]["expected_csv_sha256"] = "f" * 64

        suite_path = self.fixtures_dir / "temp_digest_mismatch_suite.json"
        try:
            suite_path.write_text(json.dumps(suite_data, indent=2), encoding="utf-8")

            code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(suite_path)])
            self.assertEqual(code, 1)
            self.assertEqual(stderr, "")

            doc = json.loads(stdout)
            self.assertEqual(doc["status"], "FAIL")
            self.assertEqual(doc["summary"]["error_cases"], 1)
            self.assertEqual(doc["cases"][0]["status"], "ERROR")
            self.assertTrue(any("does not match expected" in d for d in doc["cases"][0]["details"]))
        finally:
            if suite_path.exists():
                suite_path.unlink()

    def test_recorded_provenance_rejected_as_error_and_exit_1(self) -> None:
        """TC-SCEN-05: Recording with recorded provenance is rejected as ERROR."""
        # Create metadata with provenance="recorded"
        rec_meta = dict(
            schema_version="1.0.0",
            recording_id="rec_real_01",
            machine_id="m1",
            session_id="s1",
            source_id="src1",
            provenance="recorded",
            sample_rate_hz=1000.0,
            acceleration_unit="g",
        )
        meta_path = self.fixtures_dir / "temp_meta_recorded.json"
        suite_path = self.fixtures_dir / "temp_recorded_suite.json"
        try:
            meta_path.write_text(json.dumps(rec_meta, indent=2), encoding="utf-8")

            suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
            suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
            suite_data["cases"][0]["metadata_path"] = "temp_meta_recorded.json"
            suite_path.write_text(json.dumps(suite_data, indent=2), encoding="utf-8")

            code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(suite_path)])
            self.assertEqual(code, 1)
            self.assertEqual(stderr, "")

            doc = json.loads(stdout)
            self.assertEqual(doc["status"], "FAIL")
            self.assertEqual(doc["summary"]["error_cases"], 1)
            self.assertEqual(doc["cases"][0]["status"], "ERROR")
            self.assertTrue(any("Recording provenance must be 'synthetic'" in d for d in doc["cases"][0]["details"]))
        finally:
            if meta_path.exists():
                meta_path.unlink()
            if suite_path.exists():
                suite_path.unlink()

    def test_mixed_match_mismatch_error_accounting(self) -> None:
        """TC-SCEN-06: Accurate accounting across mixed MATCH, MISMATCH, and ERROR cases."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))

        case_match = copy.deepcopy(suite_data["cases"][0])
        case_match["case_id"] = "case_match"

        case_mismatch = copy.deepcopy(suite_data["cases"][1])
        case_mismatch["case_id"] = "case_mismatch"
        case_mismatch["expected_result"]["windows"][0]["outcome"] = "NORMAL"  # will mismatch ANOMALY

        case_error = copy.deepcopy(suite_data["cases"][2])
        case_error["case_id"] = "case_error"
        case_error["csv_path"] = "nonexistent.csv"

        mixed_suite = {
            "schema_version": "1.0.0",
            "suite_id": "suite_mixed_accounting",
            "evaluation_scope": "synthetic_behavioral_regression",
            "description": "Suite testing accurate multi-case denominator reconciliation",
            "cases": [case_match, case_mismatch, case_error],
        }

        suite_path = self.fixtures_dir / "temp_mixed_suite.json"
        try:
            suite_path.write_text(json.dumps(mixed_suite, indent=2), encoding="utf-8")

            code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(suite_path)])
            self.assertEqual(code, 1)
            self.assertEqual(stderr, "")

            doc = json.loads(stdout)
            self.assertEqual(doc["status"], "FAIL")
            summary = doc["summary"]
            self.assertEqual(summary["total_cases"], 3)
            self.assertEqual(summary["matched_cases"], 1)
            self.assertEqual(summary["mismatched_cases"], 1)
            self.assertEqual(summary["error_cases"], 1)
            self.assertEqual(summary["total_cases"], summary["matched_cases"] + summary["mismatched_cases"] + summary["error_cases"])

            statuses = [c["status"] for c in doc["cases"]]
            self.assertEqual(statuses, ["MATCH", "MISMATCH", "ERROR"])

            # Verify expected vs actual decision reconciliation
            exp_decs = summary["expected_decisions_summary"]
            act_decs = summary["actual_decisions_summary"]

            # Expected includes all 3 cases (1 + 1 + 1 = 3)
            self.assertEqual(exp_decs["total_decisions"], 3)
            self.assertEqual(exp_decs["normal_count"], 2)
            self.assertEqual(exp_decs["anomaly_count"], 1)
            self.assertEqual(exp_decs["insufficient_evidence_count"], 0)
            self.assertEqual(exp_decs["window_level_decisions"], 3)
            self.assertEqual(exp_decs["recording_level_decisions"], 0)

            # Actual includes only executed decisions from case 0 and 1 (1 + 1 = 2); ERROR case invents 0 decisions
            self.assertEqual(act_decs["total_decisions"], 2)
            self.assertEqual(act_decs["normal_count"], 1)
            self.assertEqual(act_decs["anomaly_count"], 1)
            self.assertEqual(act_decs["insufficient_evidence_count"], 0)
            self.assertEqual(act_decs["window_level_decisions"], 2)
            self.assertEqual(act_decs["recording_level_decisions"], 0)

            # Verify each case report contains expected_result and actual_window_boundaries
            for case in doc["cases"]:
                self.assertIn("expected_result", case)
                self.assertIn("actual_window_boundaries", case)
                if case["status"] == "ERROR":
                    self.assertEqual(case["actual_decisions"], [])
                    self.assertEqual(case["actual_window_boundaries"], [])
                    self.assertIsNone(case["actual_scope"])
                else:
                    self.assertGreater(len(case["actual_decisions"]), 0)
                    self.assertGreater(len(case["actual_window_boundaries"]), 0)
        finally:
            if suite_path.exists():
                suite_path.unlink()

    def test_all_abstention_suite_accounting(self) -> None:
        """TC-SCEN-07: All-abstention suite reports accurate counts without claiming diagnostic accuracy."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        # Cases 5 and 6 are recording-level abstention cases
        case_mismatch = copy.deepcopy(suite_data["cases"][5])
        case_short = copy.deepcopy(suite_data["cases"][6])

        abst_suite = {
            "schema_version": "1.0.0",
            "suite_id": "suite_all_abstention",
            "evaluation_scope": "synthetic_behavioral_regression",
            "description": "Suite containing only abstention scenarios",
            "cases": [case_mismatch, case_short],
        }

        suite_path = self.fixtures_dir / "temp_abst_suite.json"
        try:
            suite_path.write_text(json.dumps(abst_suite, indent=2), encoding="utf-8")

            code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(suite_path)])
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")

            doc = json.loads(stdout)
            self.assertEqual(doc["status"], "PASS")
            summary = doc["summary"]
            self.assertEqual(summary["total_cases"], 2)
            self.assertEqual(summary["matched_cases"], 2)
            self.assertEqual(summary["mismatched_cases"], 0)
            self.assertEqual(summary["error_cases"], 0)

            # Reconcile decisions: 0 normal, 0 anomaly, 2 insufficient_evidence
            decs = summary["actual_decisions_summary"]
            self.assertEqual(decs["total_decisions"], 2)
            self.assertEqual(decs["normal_count"], 0)
            self.assertEqual(decs["anomaly_count"], 0)
            self.assertEqual(decs["insufficient_evidence_count"], 2)
            self.assertEqual(decs["recording_level_decisions"], 2)
            self.assertEqual(decs["window_level_decisions"], 0)
        finally:
            if suite_path.exists():
                suite_path.unlink()

    # =========================================================================
    # 3. Manifest Validation & Error Cases (Exit 2)
    # =========================================================================

    def test_empty_suite_rejected_exit_2(self) -> None:
        """TC-SCEN-08: Suite with empty cases list produces exit 2."""
        empty_suite = {
            "schema_version": "1.0.0",
            "suite_id": "suite_empty",
            "evaluation_scope": "synthetic_behavioral_regression",
            "cases": [],
        }
        p = self.dir_path / "empty_suite.json"
        p.write_text(json.dumps(empty_suite), encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("must be a nonempty list", stderr)

    def test_duplicate_case_ids_rejected_exit_2(self) -> None:
        """TC-SCEN-09: Duplicate case_id values produce exit 2."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        case_copy = copy.deepcopy(suite_data["cases"][0])
        suite_data["cases"] = [suite_data["cases"][0], case_copy]

        p = self.dir_path / "dup_case_suite.json"
        p.write_text(json.dumps(suite_data), encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("duplicate case_id", stderr)

    def test_malformed_suite_json_rejected_exit_2(self) -> None:
        """TC-SCEN-10: Malformed JSON syntax in suite produces exit 2 with line/col."""
        p = self.dir_path / "malformed_suite.json"
        p.write_text('{"schema_version": "1.0.0", bad_syntax}', encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Malformed JSON in suite manifest", stderr)

    def test_duplicate_keys_in_suite_rejected_exit_2(self) -> None:
        """TC-SCEN-11: Duplicate keys in suite JSON produce exit 2."""
        p = self.dir_path / "dup_key_suite.json"
        p.write_text('{"suite_id": "s1", "suite_id": "s2", "cases": []}', encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Duplicate key 'suite_id'", stderr)

    def test_nonstandard_constants_in_suite_rejected_exit_2(self) -> None:
        """TC-SCEN-12: Nonstandard constants (NaN, Infinity) in suite produce exit 2."""
        p = self.dir_path / "nan_suite.json"
        p.write_text('{"suite_id": NaN, "cases": []}', encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Nonstandard JSON constant 'NaN'", stderr)

    def test_missing_suite_file_exit_2(self) -> None:
        """TC-SCEN-13: Missing suite file path produces exit 2."""
        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(self.dir_path / "nonexistent.json")])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("nonexistent.json", stderr)

    def test_boolean_in_window_index_rejected_exit_2(self) -> None:
        """TC-SCEN-18: Boolean window_index (e.g. False) is strictly rejected with exit 2 before execution."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
        suite_data["cases"][0]["expected_result"]["windows"][0]["window_index"] = False

        p = self.dir_path / "bool_window_index.json"
        p.write_text(json.dumps(suite_data), encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("non-boolean", stderr)

    def test_boolean_in_start_sample_rejected_exit_2(self) -> None:
        """TC-SCEN-19: Boolean start_sample (e.g. False) is strictly rejected with exit 2 before execution."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
        suite_data["cases"][0]["expected_result"]["windows"][0]["start_sample"] = False

        p = self.dir_path / "bool_start_sample.json"
        p.write_text(json.dumps(suite_data), encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("non-boolean", stderr)

    def test_boolean_in_end_sample_rejected_exit_2(self) -> None:
        """TC-SCEN-20: Boolean end_sample (e.g. True) is strictly rejected with exit 2 before execution."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
        suite_data["cases"][0]["expected_result"]["windows"][0]["end_sample"] = True

        p = self.dir_path / "bool_end_sample.json"
        p.write_text(json.dumps(suite_data), encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("non-boolean", stderr)

    def test_null_csv_path_rejected_exit_2(self) -> None:
        """TC-SCEN-21: Null csv_path produces ScenarioSuiteError (exit 2, empty stdout) without leaking TypeError."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
        suite_data["cases"][0]["csv_path"] = None

        p = self.dir_path / "null_csv_path.json"
        p.write_text(json.dumps(suite_data), encoding="utf-8")

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("must be a nonempty string", stderr)
        self.assertNotIn("TypeError", stderr)

    def test_invalid_path_types_and_empty_paths_rejected_exit_2(self) -> None:
        """TC-SCEN-22: Non-string and empty string path fields produce exit 2."""
        for field in ("csv_path", "metadata_path", "config_path"):
            for bad_val in ("", "   ", 123, False):
                suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
                suite_data["cases"] = [copy.deepcopy(suite_data["cases"][0])]
                suite_data["cases"][0][field] = bad_val

                p = self.dir_path / f"bad_{field}.json"
                p.write_text(json.dumps(suite_data), encoding="utf-8")

                code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(p)])
                self.assertEqual(code, 2, f"Expected exit 2 for {field}={bad_val!r}, got {code}")
                self.assertEqual(stdout, "")
                self.assertIn("must be a nonempty string", stderr)

    def test_scope_incompatible_fields_rejected_exit_2(self) -> None:
        """TC-SCEN-23: Scope-incompatible fields in expected_result produce exit 2."""
        # 1. recording_envelope scope containing forbidden windows field
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        case_env = copy.deepcopy(suite_data["cases"][5])  # recording_envelope case
        case_env["expected_result"]["windows"] = []
        p1 = self.dir_path / "env_with_windows.json"
        p1.write_text(json.dumps({"schema_version": "1.0.0", "suite_id": "s", "evaluation_scope": "synthetic_behavioral_regression", "cases": [case_env]}), encoding="utf-8")

        code1, stdout1, stderr1 = _run_cli_subprocess(["evaluate", "--suite", str(p1)])
        self.assertEqual(code1, 2)
        self.assertEqual(stdout1, "")
        self.assertIn("forbids keys", stderr1)

        # 2. window scope containing forbidden envelope fields (outcome, quality_status, reason_codes)
        case_win = copy.deepcopy(suite_data["cases"][0])
        case_win["expected_result"]["outcome"] = "NORMAL"
        p2 = self.dir_path / "win_with_outcome.json"
        p2.write_text(json.dumps({"schema_version": "1.0.0", "suite_id": "s", "evaluation_scope": "synthetic_behavioral_regression", "cases": [case_win]}), encoding="utf-8")

        code2, stdout2, stderr2 = _run_cli_subprocess(["evaluate", "--suite", str(p2)])
        self.assertEqual(code2, 2)
        self.assertEqual(stdout2, "")
        self.assertIn("forbids envelope keys", stderr2)

    def test_missing_or_empty_abstention_reasons_rejected_exit_2(self) -> None:
        """TC-SCEN-24: Abstention expectations without non-empty reason codes produce exit 2."""
        # 1. recording_envelope with empty reason_codes
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        case_env = copy.deepcopy(suite_data["cases"][5])
        case_env["expected_result"]["reason_codes"] = []
        p1 = self.dir_path / "empty_env_reasons.json"
        p1.write_text(json.dumps({"schema_version": "1.0.0", "suite_id": "s", "evaluation_scope": "synthetic_behavioral_regression", "cases": [case_env]}), encoding="utf-8")

        code1, stdout1, stderr1 = _run_cli_subprocess(["evaluate", "--suite", str(p1)])
        self.assertEqual(code1, 2)
        self.assertEqual(stdout1, "")
        self.assertIn("nonempty list of reason_codes", stderr1)

        # 2. window abstention with empty reason_codes
        case_sat = copy.deepcopy(suite_data["cases"][3])  # saturation window case
        case_sat["expected_result"]["windows"][0]["reason_codes"] = []
        p2 = self.dir_path / "empty_win_reasons.json"
        p2.write_text(json.dumps({"schema_version": "1.0.0", "suite_id": "s", "evaluation_scope": "synthetic_behavioral_regression", "cases": [case_sat]}), encoding="utf-8")

        code2, stdout2, stderr2 = _run_cli_subprocess(["evaluate", "--suite", str(p2)])
        self.assertEqual(code2, 2)
        self.assertEqual(stdout2, "")
        self.assertIn("requires nonempty reason_codes", stderr2)

    def test_contradictory_outcome_quality_rejected_exit_2(self) -> None:
        """TC-SCEN-25: Contradictory outcome and quality_status combinations produce exit 2."""
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))

        # NORMAL with quality_status FAIL
        case_bad1 = copy.deepcopy(suite_data["cases"][0])
        case_bad1["expected_result"]["windows"][0]["quality_status"] = "FAIL"
        p1 = self.dir_path / "normal_fail.json"
        p1.write_text(json.dumps({"schema_version": "1.0.0", "suite_id": "s", "evaluation_scope": "synthetic_behavioral_regression", "cases": [case_bad1]}), encoding="utf-8")

        code1, stdout1, stderr1 = _run_cli_subprocess(["evaluate", "--suite", str(p1)])
        self.assertEqual(code1, 2)
        self.assertEqual(stdout1, "")
        self.assertIn("requires quality_status 'PASS'", stderr1)

        # INSUFFICIENT_EVIDENCE with quality_status PASS
        case_bad2 = copy.deepcopy(suite_data["cases"][3])
        case_bad2["expected_result"]["windows"][0]["quality_status"] = "PASS"
        p2 = self.dir_path / "abst_pass.json"
        p2.write_text(json.dumps({"schema_version": "1.0.0", "suite_id": "s", "evaluation_scope": "synthetic_behavioral_regression", "cases": [case_bad2]}), encoding="utf-8")

        code2, stdout2, stderr2 = _run_cli_subprocess(["evaluate", "--suite", str(p2)])
        self.assertEqual(code2, 2)
        self.assertEqual(stdout2, "")
        self.assertIn("cannot have quality_status 'PASS'", stderr2)

    def test_single_snapshot_csv_ingestion_and_read_count(self) -> None:
        """TC-SCEN-26: Evaluator reads CSV path exactly once and parses the identical initial byte snapshot."""
        from adi_app.scenario_evaluator import evaluate_scenario_suite
        from adi_app.ingestion import parse_vibration_csv_bytes

        # Verify parse_vibration_csv_bytes works standalone
        meta = {
            "schema_version": "1.0.0",
            "recording_id": "rec_bytes_01",
            "machine_id": "m1",
            "session_id": "s1",
            "source_id": "src1",
            "provenance": "synthetic",
            "sample_rate_hz": 1000.0,
            "acceleration_unit": "g",
        }
        raw_csv = b"x\n1.0\n2.0\n"
        rec = parse_vibration_csv_bytes(raw_csv, meta)
        self.assertEqual(rec["channels"], ["x"])
        self.assertEqual(rec["samples"], [[1.0], [2.0]])
        self.assertEqual(rec["source_sha256"], hashlib.sha256(raw_csv).hexdigest())

        # Now test single read count during evaluation:
        # Instrument Path.read_bytes with counter
        orig_read_bytes = Path.read_bytes
        csv_read_counter = {"count": 0}

        def counting_read_bytes(path_obj):
            if str(path_obj).endswith(".csv"):
                csv_read_counter["count"] += 1
            return orig_read_bytes(path_obj)

        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        single_case_suite = {
            "schema_version": "1.0.0",
            "suite_id": "suite_single_read_test",
            "evaluation_scope": "synthetic_behavioral_regression",
            "description": "Suite testing single read of CSV file",
            "cases": [copy.deepcopy(suite_data["cases"][0])],
        }
        suite_path = self.fixtures_dir / "temp_single_read_suite.json"
        suite_path.write_text(json.dumps(single_case_suite), encoding="utf-8")

        try:
            Path.read_bytes = counting_read_bytes
            exit_code, report = evaluate_scenario_suite(suite_path)
        finally:
            Path.read_bytes = orig_read_bytes
            if suite_path.exists():
                suite_path.unlink()

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(csv_read_counter["count"], 1, f"Expected CSV to be read exactly once, got {csv_read_counter['count']}")

    def test_single_snapshot_simulated_file_change_after_read(self) -> None:
        """TC-SCEN-27: Modifying CSV file on disk immediately after snapshot read does not affect parsed pipeline data."""
        from adi_app.scenario_evaluator import evaluate_scenario_suite

        # Create temporary CSV file in fixtures dir
        temp_csv = self.fixtures_dir / "temp_snapshot_test.csv"
        initial_csv_bytes = b"x\n1.0\n1.0\n1.0\n1.0\n1.0\n"
        temp_csv.write_bytes(initial_csv_bytes)
        expected_sha = hashlib.sha256(initial_csv_bytes).hexdigest()

        # Temporary suite referencing temp_snapshot_test.csv
        suite_data = json.loads(self.checked_in_suite_path.read_text(encoding="utf-8"))
        case_data = copy.deepcopy(suite_data["cases"][0])
        case_data["csv_path"] = "temp_snapshot_test.csv"
        case_data["expected_csv_sha256"] = expected_sha

        snapshot_suite = {
            "schema_version": "1.0.0",
            "suite_id": "suite_snapshot_mutation_test",
            "evaluation_scope": "synthetic_behavioral_regression",
            "description": "Suite testing snapshot immutability during disk mutation",
            "cases": [case_data],
        }
        suite_path = self.fixtures_dir / "temp_snapshot_suite.json"
        suite_path.write_text(json.dumps(snapshot_suite), encoding="utf-8")

        orig_read_bytes = Path.read_bytes

        def mutating_read_bytes(path_obj):
            bytes_read = orig_read_bytes(path_obj)
            # When the CSV is read into memory, immediately overwrite disk with completely different data
            if str(path_obj).endswith("temp_snapshot_test.csv"):
                temp_csv.write_bytes(b"x\n999.0\n999.0\n999.0\n999.0\n999.0\n")
            return bytes_read

        try:
            Path.read_bytes = mutating_read_bytes
            exit_code, report = evaluate_scenario_suite(suite_path)
        finally:
            Path.read_bytes = orig_read_bytes
            if temp_csv.exists():
                temp_csv.unlink()
            if suite_path.exists():
                suite_path.unlink()

        # Evaluation should have completed based on initial snapshot (RMS=1.0 -> NORMAL -> MATCH)
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["cases"][0]["status"], "MATCH")
        self.assertEqual(report["cases"][0]["input_hashes"]["csv_sha256"], expected_sha)
        self.assertEqual(report["cases"][0]["actual_decisions"][0]["outcome"], "NORMAL")
        self.assertEqual(report["cases"][0]["actual_decisions"][0]["score"], 1.0)

    # =========================================================================
    # 4. Portability, Repeatability, and Immutability
    # =========================================================================

    def test_unrelated_working_directory_relative_path_resolution(self) -> None:
        """TC-SCEN-14: Suite paths resolve relative to suite JSON location even from an unrelated cwd."""
        with tempfile.TemporaryDirectory() as unrelated_cwd:
            code, stdout, stderr = _run_cli_subprocess(
                ["evaluate", "--suite", str(self.checked_in_suite_path)],
                cwd=Path(unrelated_cwd),
            )
            self.assertEqual(code, 0, f"Expected 0 from unrelated cwd. Stderr: {stderr}")
            self.assertEqual(stderr, "")
            doc = json.loads(stdout)
            self.assertEqual(doc["status"], "PASS")
            self.assertEqual(doc["summary"]["matched_cases"], 7)

    def test_repeatability_and_identical_output_bytes(self) -> None:
        """TC-SCEN-15: Repeated evaluation produces byte-for-byte identical output."""
        args = ["evaluate", "--suite", str(self.checked_in_suite_path)]
        code1, stdout1, stderr1 = _run_cli_subprocess(args)
        code2, stdout2, stderr2 = _run_cli_subprocess(args)

        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        self.assertEqual(stdout1, stdout2, "Output must be byte-for-byte deterministic across runs")

    def test_input_files_remain_unchanged_on_disk(self) -> None:
        """TC-SCEN-16: Suite manifest and all referenced files remain untouched on disk."""
        suite_before = self.checked_in_suite_path.read_bytes()
        file_snapshots = {}
        for item in self.fixtures_dir.iterdir():
            if item.is_file():
                file_snapshots[item] = item.read_bytes()

        code, stdout, stderr = _run_cli_subprocess(["evaluate", "--suite", str(self.checked_in_suite_path)])
        self.assertEqual(code, 0)

        self.assertEqual(self.checked_in_suite_path.read_bytes(), suite_before)
        for item, before_bytes in file_snapshots.items():
            self.assertEqual(item.read_bytes(), before_bytes, f"File {item} was modified on disk")

    def test_legacy_evaluate_without_suite_preserved(self) -> None:
        """TC-SCEN-17: Legacy evaluate command without --suite produces exit 1 and NOT_IMPLEMENTED."""
        code, stdout, stderr = _run_cli_subprocess(["evaluate"])
        self.assertEqual(code, 1)
        data = json.loads(stdout)
        self.assertEqual(data.get("status"), "NOT_IMPLEMENTED")
        self.assertEqual(data.get("hardware_status"), "UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
