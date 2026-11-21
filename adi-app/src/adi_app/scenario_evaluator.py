"""Deterministic behavioral regression evaluator for synthetic vibration scenarios.

Executes explicit scenario suites referencing synthetic CSV recordings, metadata,
and detector configurations against independently authored expected results.
Provides honest accounting: records MATCH, MISMATCH, and ERROR cases without
implying diagnostic accuracy, precision, recall, or ground-truth machine health.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from adi_common import ContractValidationError
from adi_runtime import ConfigurationError, ThresholdReferenceError, evaluate_rms_threshold
from adi_app.ingestion import (
    IngestionDigestMismatchError,
    IngestionFileNotFoundError,
    IngestionMetadataError,
    IngestionParseError,
    RecordingIngestionError,
    load_vibration_csv,
    parse_vibration_csv_bytes,
)
from adi_app.portfolio import (
    CLIError,
    _reject_duplicate_keys_hook,
    _reject_nonstandard_constants,
    load_strict_json_object,
)


class ScenarioSuiteError(CLIError):
    """Raised when a scenario suite manifest is malformed, unreadable, or invalid."""
    pass


_ALLOWED_SUITE_KEYS = {
    "schema_version",
    "suite_id",
    "evaluation_scope",
    "description",
    "cases",
}

_ALLOWED_CASE_KEYS = {
    "case_id",
    "description",
    "csv_path",
    "metadata_path",
    "config_path",
    "expected_csv_sha256",
    "expected_result",
}

_ALLOWED_ENVELOPE_KEYS = {
    "scope",
    "outcome",
    "quality_status",
    "reason_codes",
}

_ALLOWED_WINDOW_SCOPE_KEYS = {
    "scope",
    "windows",
}

_ALLOWED_EXPECTED_WINDOW_KEYS = {
    "window_index",
    "start_sample",
    "end_sample",
    "outcome",
    "quality_status",
    "reason_codes",
}


def _is_strict_int(val: Any) -> bool:
    """Return True if val is an int and strictly not a boolean."""
    return isinstance(val, int) and not isinstance(val, bool)


def _is_nonempty_str(val: Any) -> bool:
    """Return True if val is a string and strictly not empty or whitespace-only."""
    return isinstance(val, str) and not isinstance(val, bool) and len(val.strip()) > 0


def _validate_expected_result_schema(expected: Any, case_id: str) -> None:
    """Validate structure of expected_result in a scenario case."""
    if not isinstance(expected, dict):
        raise ScenarioSuiteError(
            f"Case '{case_id}': 'expected_result' must be a JSON object, got {type(expected).__name__}"
        )

    scope = expected.get("scope")
    if scope not in ("recording_envelope", "window"):
        raise ScenarioSuiteError(
            f"Case '{case_id}': 'expected_result.scope' must be 'recording_envelope' or 'window', got {scope!r}"
        )

    if scope == "recording_envelope":
        unknown_keys = set(expected.keys()) - _ALLOWED_ENVELOPE_KEYS
        if unknown_keys:
            raise ScenarioSuiteError(
                f"Case '{case_id}': scope 'recording_envelope' forbids keys {sorted(unknown_keys)}"
            )
        missing_keys = _ALLOWED_ENVELOPE_KEYS - set(expected.keys())
        if missing_keys:
            raise ScenarioSuiteError(
                f"Case '{case_id}': scope 'recording_envelope' missing required keys {sorted(missing_keys)}"
            )

        outcome = expected["outcome"]
        if outcome != "INSUFFICIENT_EVIDENCE":
            raise ScenarioSuiteError(
                f"Case '{case_id}': recording_envelope scope requires abstention outcome 'INSUFFICIENT_EVIDENCE', got {outcome!r}"
            )

        quality = expected["quality_status"]
        if quality not in ("FAIL", "INDETERMINATE"):
            raise ScenarioSuiteError(
                f"Case '{case_id}': recording_envelope scope abstention quality_status must be 'FAIL' or 'INDETERMINATE', got {quality!r}"
            )

        reasons = expected["reason_codes"]
        if not isinstance(reasons, list) or len(reasons) == 0:
            raise ScenarioSuiteError(
                f"Case '{case_id}': recording_envelope scope requires a nonempty list of reason_codes"
            )
        for r_idx, r in enumerate(reasons):
            if not _is_nonempty_str(r):
                raise ScenarioSuiteError(
                    f"Case '{case_id}': reason_code at index {r_idx} must be a nonempty string, got {r!r}"
                )

    elif scope == "window":
        unknown_keys = set(expected.keys()) - _ALLOWED_WINDOW_SCOPE_KEYS
        if unknown_keys:
            raise ScenarioSuiteError(
                f"Case '{case_id}': scope 'window' forbids envelope keys/unknown keys {sorted(unknown_keys)}"
            )
        if "windows" not in expected:
            raise ScenarioSuiteError(f"Case '{case_id}': scope 'window' missing required key 'windows'")

        windows = expected["windows"]
        if not isinstance(windows, list) or len(windows) == 0:
            raise ScenarioSuiteError(
                f"Case '{case_id}': 'windows' must be a nonempty list"
            )

        prev_start = -1
        prev_end = -1
        for w_idx, win in enumerate(windows):
            if not isinstance(win, dict):
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: must be an object, got {type(win).__name__}"
                )
            win_keys = set(win.keys())
            unknown_win_keys = win_keys - _ALLOWED_EXPECTED_WINDOW_KEYS
            if unknown_win_keys:
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: unknown keys: {sorted(unknown_win_keys)}"
                )
            missing_win_keys = _ALLOWED_EXPECTED_WINDOW_KEYS - win_keys
            if missing_win_keys:
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: missing required keys: {sorted(missing_win_keys)}"
                )

            # window_index: non-boolean int, ordered starting at 0
            w_num = win["window_index"]
            if not _is_strict_int(w_num) or w_num < 0:
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: 'window_index' must be a non-boolean non-negative integer, got {w_num!r}"
                )
            if w_num != w_idx:
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: 'window_index' must be ordered starting at 0, expected {w_idx}, got {w_num}"
                )

            # start_sample: non-boolean int >= 0
            start_s = win["start_sample"]
            if not _is_strict_int(start_s) or start_s < 0:
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: 'start_sample' must be a non-boolean non-negative integer, got {start_s!r}"
                )

            # end_sample: non-boolean int > start_sample
            end_s = win["end_sample"]
            if not _is_strict_int(end_s) or end_s <= start_s:
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: 'end_sample' must be a non-boolean integer strictly greater than start_sample ({start_s}), got {end_s!r}"
                )

            # Ordered progression check
            if w_idx > 0:
                if start_s < prev_start:
                    raise ScenarioSuiteError(
                        f"Case '{case_id}', window {w_idx}: start_sample ({start_s}) cannot decrease from previous window start ({prev_start})"
                    )
                if end_s < prev_end:
                    raise ScenarioSuiteError(
                        f"Case '{case_id}', window {w_idx}: end_sample ({end_s}) cannot decrease from previous window end ({prev_end})"
                    )
            prev_start = start_s
            prev_end = end_s

            outcome = win["outcome"]
            if outcome not in ("NORMAL", "ANOMALY", "INSUFFICIENT_EVIDENCE"):
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: invalid outcome '{outcome}'"
                )

            quality = win["quality_status"]
            if quality not in ("PASS", "FAIL", "INDETERMINATE"):
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: invalid quality_status '{quality}'"
                )

            reasons = win["reason_codes"]
            if not isinstance(reasons, list):
                raise ScenarioSuiteError(
                    f"Case '{case_id}', window {w_idx}: 'reason_codes' must be an explicit list, got {type(reasons).__name__}"
                )
            for r_i, r in enumerate(reasons):
                if not _is_nonempty_str(r):
                    raise ScenarioSuiteError(
                        f"Case '{case_id}', window {w_idx}: reason_code at index {r_i} must be a nonempty string, got {r!r}"
                    )

            # Contradictory outcome/quality combinations
            if outcome in ("NORMAL", "ANOMALY"):
                if quality != "PASS":
                    raise ScenarioSuiteError(
                        f"Case '{case_id}', window {w_idx}: outcome '{outcome}' requires quality_status 'PASS', got '{quality}'"
                    )
                if len(reasons) != 0:
                    raise ScenarioSuiteError(
                        f"Case '{case_id}', window {w_idx}: outcome '{outcome}' requires empty reason_codes, got {reasons!r}"
                    )
            elif outcome == "INSUFFICIENT_EVIDENCE":
                if quality == "PASS":
                    raise ScenarioSuiteError(
                        f"Case '{case_id}', window {w_idx}: abstention outcome 'INSUFFICIENT_EVIDENCE' cannot have quality_status 'PASS'"
                    )
                if len(reasons) == 0:
                    raise ScenarioSuiteError(
                        f"Case '{case_id}', window {w_idx}: abstention outcome 'INSUFFICIENT_EVIDENCE' requires nonempty reason_codes"
                    )


def _validate_suite_manifest(manifest: Dict[str, Any], path: Path) -> None:
    """Validate strict schema and contents of a loaded suite manifest."""
    unknown_keys = set(manifest.keys()) - _ALLOWED_SUITE_KEYS
    if unknown_keys:
        raise ScenarioSuiteError(
            f"Suite manifest '{path}' contains disallowed unknown keys: {sorted(unknown_keys)}"
        )

    for req in ("schema_version", "suite_id", "evaluation_scope", "cases"):
        if req not in manifest:
            raise ScenarioSuiteError(f"Suite manifest '{path}' missing required key '{req}'")

    if manifest["schema_version"] != "1.0.0":
        raise ScenarioSuiteError(
            f"Suite manifest '{path}': unsupported schema_version '{manifest['schema_version']}', expected '1.0.0'"
        )

    suite_id = manifest["suite_id"]
    if not _is_nonempty_str(suite_id):
        raise ScenarioSuiteError(f"Suite manifest '{path}': 'suite_id' must be a nonempty string")

    if manifest["evaluation_scope"] != "synthetic_behavioral_regression":
        raise ScenarioSuiteError(
            f"Suite manifest '{path}': 'evaluation_scope' must be 'synthetic_behavioral_regression', "
            f"got {manifest['evaluation_scope']!r}"
        )

    cases = manifest["cases"]
    if not isinstance(cases, list) or len(cases) == 0:
        raise ScenarioSuiteError(f"Suite manifest '{path}': 'cases' must be a nonempty list")

    seen_case_ids: set[str] = set()
    for idx, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ScenarioSuiteError(
                f"Suite manifest '{path}', case {idx}: must be an object, got {type(case).__name__}"
            )
        unknown_case_keys = set(case.keys()) - _ALLOWED_CASE_KEYS
        if unknown_case_keys:
            raise ScenarioSuiteError(
                f"Suite manifest '{path}', case {idx}: unknown keys: {sorted(unknown_case_keys)}"
            )
        for req_c in ("case_id", "csv_path", "metadata_path", "config_path", "expected_csv_sha256", "expected_result"):
            if req_c not in case:
                raise ScenarioSuiteError(
                    f"Suite manifest '{path}', case {idx}: missing required key '{req_c}'"
                )

        cid = case["case_id"]
        if not _is_nonempty_str(cid):
            raise ScenarioSuiteError(f"Suite manifest '{path}', case {idx}: 'case_id' must be a nonempty string")
        if cid in seen_case_ids:
            raise ScenarioSuiteError(f"Suite manifest '{path}': duplicate case_id '{cid}' detected")
        seen_case_ids.add(cid)

        # Require nonempty string paths
        for path_field in ("csv_path", "metadata_path", "config_path"):
            val = case[path_field]
            if not _is_nonempty_str(val):
                raise ScenarioSuiteError(
                    f"Case '{cid}': '{path_field}' must be a nonempty string, got {val!r}"
                )

        # Validate expected_csv_sha256 syntax
        exp_sha = case["expected_csv_sha256"]
        if not _is_nonempty_str(exp_sha) or len(exp_sha) != 64 or not all(c in "0123456789abcdefABCDEF" for c in exp_sha):
            raise ScenarioSuiteError(
                f"Case '{cid}': 'expected_csv_sha256' must be a 64-hex SHA-256 string, got {exp_sha!r}"
            )

        # Validate expected_result schema
        _validate_expected_result_schema(case["expected_result"], cid)


def _evaluate_single_case(case: Dict[str, Any], suite_dir: Path) -> Dict[str, Any]:
    """Evaluate a single test case with honest accounting and single-read snapshotting."""
    cid = case["case_id"]
    description = case.get("description", "")
    expected = case["expected_result"]

    input_hashes: Dict[str, Optional[str]] = {
        "config_sha256": None,
        "csv_sha256": None,
        "metadata_sha256": None,
    }

    # Resolve paths relative to suite_dir
    csv_path = suite_dir / case["csv_path"]
    meta_path = suite_dir / case["metadata_path"]
    cfg_path = suite_dir / case["config_path"]

    # 1. Single read & hash of CSV
    if not csv_path.exists() or not csv_path.is_file():
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [f"CSV file not found or not a regular file: '{csv_path}'"],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    try:
        csv_bytes = csv_path.read_bytes()
        csv_sha = hashlib.sha256(csv_bytes).hexdigest()
        input_hashes["csv_sha256"] = csv_sha
    except OSError as exc:
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [f"Failed to read CSV '{csv_path}': {exc}"],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    # Verify expected CSV sha256
    if csv_sha.lower() != case["expected_csv_sha256"].lower():
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [
                f"CSV byte digest '{csv_sha}' does not match expected '{case['expected_csv_sha256']}'"
            ],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    # 2. Single read & hash of metadata
    if not meta_path.exists() or not meta_path.is_file():
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [f"Metadata file not found or not a regular file: '{meta_path}'"],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    try:
        meta_bytes = meta_path.read_bytes()
        input_hashes["metadata_sha256"] = hashlib.sha256(meta_bytes).hexdigest()
        metadata = json.loads(
            meta_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys_hook,
            parse_constant=_reject_nonstandard_constants,
        )
        if not isinstance(metadata, dict):
            raise ValueError(f"Top-level metadata must be an object, got {type(metadata).__name__}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [f"Metadata file error in '{meta_path}': {exc}"],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    # Require synthetic provenance
    provenance = metadata.get("provenance")
    if provenance != "synthetic":
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [f"Recording provenance must be 'synthetic', got {provenance!r}"],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    # 3. Single read & hash of configuration
    if not cfg_path.exists() or not cfg_path.is_file():
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [f"Config file not found or not a regular file: '{cfg_path}'"],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    try:
        cfg_bytes = cfg_path.read_bytes()
        input_hashes["config_sha256"] = hashlib.sha256(cfg_bytes).hexdigest()
        config = json.loads(
            cfg_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys_hook,
            parse_constant=_reject_nonstandard_constants,
        )
        if not isinstance(config, dict):
            raise ValueError(f"Top-level config must be an object, got {type(config).__name__}")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [f"Config file error in '{cfg_path}': {exc}"],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    # 4. Ingest and evaluate using the EXACT original CSV snapshot bytes!
    try:
        recording = parse_vibration_csv_bytes(
            raw_bytes=csv_bytes,
            metadata=metadata,
            source_path_context=csv_path,
            expected_sha256=csv_sha,
        )
        detector_result = evaluate_rms_threshold(recording, config)
    except (RecordingIngestionError, ThresholdReferenceError, ContractValidationError) as exc:
        return {
            "case_id": cid,
            "description": description,
            "status": "ERROR",
            "details": [f"Pipeline execution error: {exc}"],
            "input_hashes": input_hashes,
            "expected_result": expected,
            "actual_scope": None,
            "actual_window_boundaries": [],
            "actual_decisions": [],
        }

    # 5. Compare against expected results
    actual_status = detector_result.get("status")
    actual_boundaries = detector_result.get("window_boundaries", [])
    actual_decisions = detector_result.get("decisions", [])

    is_rec_level = actual_status == "INSUFFICIENT_EVIDENCE" and len(actual_boundaries) == 0
    actual_scope = "recording_envelope" if is_rec_level else "window"
    expected_scope = expected.get("scope")

    discrepancies: List[str] = []

    if actual_scope != expected_scope:
        discrepancies.append(
            f"Scope mismatch: expected '{expected_scope}', got actual '{actual_scope}'"
        )
    else:
        if expected_scope == "recording_envelope":
            if len(actual_decisions) != 1:
                discrepancies.append(
                    f"Expected exactly 1 recording-level decision, got {len(actual_decisions)}"
                )
            else:
                act_dec = actual_decisions[0]
                if act_dec.get("outcome") != expected.get("outcome"):
                    discrepancies.append(
                        f"Outcome mismatch: expected '{expected.get('outcome')}', got '{act_dec.get('outcome')}'"
                    )
                if act_dec.get("quality_status") != expected.get("quality_status"):
                    discrepancies.append(
                        f"Quality status mismatch: expected '{expected.get('quality_status')}', got '{act_dec.get('quality_status')}'"
                    )
                act_reasons = sorted(act_dec.get("reason_codes", []))
                exp_reasons = sorted(expected.get("reason_codes", []))
                if act_reasons != exp_reasons:
                    discrepancies.append(
                        f"Reason codes mismatch: expected {exp_reasons}, got {act_reasons}"
                    )

        elif expected_scope == "window":
            exp_windows = expected.get("windows", [])
            if len(actual_decisions) != len(exp_windows):
                discrepancies.append(
                    f"Window decision count mismatch: expected {len(exp_windows)}, got {len(actual_decisions)}"
                )
            else:
                for w_i, exp_win in enumerate(exp_windows):
                    act_dec = actual_decisions[w_i]
                    act_bound = actual_boundaries[w_i] if w_i < len(actual_boundaries) else {}

                    if act_bound.get("window_index") != exp_win.get("window_index"):
                        discrepancies.append(
                            f"Window {w_i}: window_index expected {exp_win.get('window_index')}, got {act_bound.get('window_index')}"
                        )
                    if act_bound.get("start_sample") != exp_win.get("start_sample"):
                        discrepancies.append(
                            f"Window {w_i}: start_sample expected {exp_win.get('start_sample')}, got {act_bound.get('start_sample')}"
                        )
                    if act_bound.get("end_sample") != exp_win.get("end_sample"):
                        discrepancies.append(
                            f"Window {w_i}: end_sample expected {exp_win.get('end_sample')}, got {act_bound.get('end_sample')}"
                        )
                    if act_dec.get("outcome") != exp_win.get("outcome"):
                        discrepancies.append(
                            f"Window {w_i}: outcome expected '{exp_win.get('outcome')}', got '{act_dec.get('outcome')}'"
                        )
                    if act_dec.get("quality_status") != exp_win.get("quality_status"):
                        discrepancies.append(
                            f"Window {w_i}: quality_status expected '{exp_win.get('quality_status')}', got '{act_dec.get('quality_status')}'"
                        )
                    act_reasons = sorted(act_dec.get("reason_codes", []))
                    exp_reasons = sorted(exp_win.get("reason_codes", []))
                    if act_reasons != exp_reasons:
                        discrepancies.append(
                            f"Window {w_i}: reason_codes expected {exp_reasons}, got {act_reasons}"
                        )

    case_status = "MISMATCH" if discrepancies else "MATCH"

    return {
        "case_id": cid,
        "description": description,
        "status": case_status,
        "details": discrepancies,
        "input_hashes": input_hashes,
        "expected_result": expected,
        "actual_scope": actual_scope,
        "actual_window_boundaries": actual_boundaries,
        "actual_decisions": actual_decisions,
    }


def evaluate_scenario_suite(suite_path: str | Path) -> Tuple[int, Dict[str, Any]]:
    """Evaluate a scenario suite manifest and return exit code and complete evaluation report.

    Args:
        suite_path: Local filesystem path to the scenario suite JSON manifest.

    Returns:
        Tuple[int, dict]: (exit_code, report_dict)
            exit_code: 0 if all cases MATCH, 1 if any MISMATCH or ERROR.

    Raises:
        ScenarioSuiteError: If suite manifest file is missing, unreadable, or violates schema.
    """
    path = Path(suite_path)
    if not path.exists():
        raise ScenarioSuiteError(f"Suite manifest file not found: '{path}'")
    if not path.is_file():
        raise ScenarioSuiteError(f"Suite manifest path is not a regular file: '{path}'")

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise ScenarioSuiteError(f"Failed to read suite manifest '{path}': {exc}")

    if len(raw_bytes) == 0:
        raise ScenarioSuiteError(f"Suite manifest '{path}' is empty (0 bytes)")

    suite_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    try:
        manifest = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys_hook,
            parse_constant=_reject_nonstandard_constants,
        )
    except UnicodeDecodeError as exc:
        raise ScenarioSuiteError(f"Suite manifest '{path}' is not valid UTF-8: {exc}")
    except json.JSONDecodeError as exc:
        raise ScenarioSuiteError(
            f"Malformed JSON in suite manifest '{path}' at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )
    except ValueError as exc:
        raise ScenarioSuiteError(f"Invalid JSON in suite manifest '{path}': {exc}")

    if not isinstance(manifest, dict):
        raise ScenarioSuiteError(
            f"Top-level suite manifest '{path}' must be an object, got {type(manifest).__name__}"
        )

    _validate_suite_manifest(manifest, path)

    suite_dir = path.resolve().parent
    cases_specs = manifest["cases"]

    # Compute expected decisions summary over ALL declared cases (including those that might ERROR)
    exp_total = 0
    exp_rec_scope = 0
    exp_win_scope = 0
    exp_normal = 0
    exp_anomaly = 0
    exp_abstention = 0
    exp_rec_abstention = 0
    exp_win_normal = 0
    exp_win_anomaly = 0
    exp_win_abstention = 0

    for case_spec in cases_specs:
        exp_res = case_spec["expected_result"]
        exp_sc = exp_res["scope"]
        if exp_sc == "recording_envelope":
            exp_total += 1
            exp_rec_scope += 1
            exp_abstention += 1
            exp_rec_abstention += 1
        elif exp_sc == "window":
            for w in exp_res["windows"]:
                exp_total += 1
                exp_win_scope += 1
                w_out = w["outcome"]
                if w_out == "NORMAL":
                    exp_normal += 1
                    exp_win_normal += 1
                elif w_out == "ANOMALY":
                    exp_anomaly += 1
                    exp_win_anomaly += 1
                elif w_out == "INSUFFICIENT_EVIDENCE":
                    exp_abstention += 1
                    exp_win_abstention += 1

    case_results: List[Dict[str, Any]] = []

    matched_count = 0
    mismatched_count = 0
    error_count = 0

    all_actual_decisions: List[Dict[str, Any]] = []
    actual_rec_decisions: List[Dict[str, Any]] = []
    actual_win_decisions: List[Dict[str, Any]] = []

    for case_spec in cases_specs:
        case_res = _evaluate_single_case(case_spec, suite_dir)
        case_results.append(case_res)

        status = case_res["status"]
        if status == "MATCH":
            matched_count += 1
        elif status == "MISMATCH":
            mismatched_count += 1
        elif status == "ERROR":
            error_count += 1

        # Aggregate actual decisions (ERROR cases contribute no invented actual decisions)
        acts = case_res.get("actual_decisions", [])
        all_actual_decisions.extend(acts)
        if case_res.get("actual_scope") == "recording_envelope":
            actual_rec_decisions.extend(acts)
        elif case_res.get("actual_scope") == "window":
            actual_win_decisions.extend(acts)

    total_cases = len(cases_specs)
    assert total_cases == (matched_count + mismatched_count + error_count)

    # Actual decisions breakdowns
    act_total = len(all_actual_decisions)
    act_rec_scope = len(actual_rec_decisions)
    act_win_scope = len(actual_win_decisions)

    act_rec_normal = sum(1 for d in actual_rec_decisions if d.get("outcome") == "NORMAL")
    act_rec_anomaly = sum(1 for d in actual_rec_decisions if d.get("outcome") == "ANOMALY")
    act_rec_abstention = sum(1 for d in actual_rec_decisions if d.get("outcome") == "INSUFFICIENT_EVIDENCE")

    act_win_normal = sum(1 for d in actual_win_decisions if d.get("outcome") == "NORMAL")
    act_win_anomaly = sum(1 for d in actual_win_decisions if d.get("outcome") == "ANOMALY")
    act_win_abstention = sum(1 for d in actual_win_decisions if d.get("outcome") == "INSUFFICIENT_EVIDENCE")

    act_normal = act_rec_normal + act_win_normal
    act_anomaly = act_rec_anomaly + act_win_anomaly
    act_abstention = act_rec_abstention + act_win_abstention

    overall_status = "PASS" if (mismatched_count == 0 and error_count == 0) else "FAIL"
    exit_code = 0 if overall_status == "PASS" else 1

    report: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "command": "evaluate",
        "suite_id": manifest["suite_id"],
        "evaluation_scope": "synthetic_behavioral_regression",
        "execution_environment": "HOST_CPU",
        "suite_sha256": suite_sha256,
        "status": overall_status,
        "summary": {
            "total_cases": total_cases,
            "matched_cases": matched_count,
            "mismatched_cases": mismatched_count,
            "error_cases": error_count,
            "expected_decisions_summary": {
                "total_decisions": exp_total,
                "normal_count": exp_normal,
                "anomaly_count": exp_anomaly,
                "insufficient_evidence_count": exp_abstention,
                "recording_level_decisions": exp_rec_scope,
                "window_level_decisions": exp_win_scope,
                "recording_level_abstention_count": exp_rec_abstention,
                "window_abstention_count": exp_win_abstention,
                "by_scope": {
                    "recording_envelope": exp_rec_scope,
                    "window": exp_win_scope,
                },
                "by_outcome": {
                    "NORMAL": exp_normal,
                    "ANOMALY": exp_anomaly,
                    "INSUFFICIENT_EVIDENCE": exp_abstention,
                },
                "by_scope_and_outcome": {
                    "recording_envelope": {
                        "NORMAL": 0,
                        "ANOMALY": 0,
                        "INSUFFICIENT_EVIDENCE": exp_rec_abstention,
                    },
                    "window": {
                        "NORMAL": exp_win_normal,
                        "ANOMALY": exp_win_anomaly,
                        "INSUFFICIENT_EVIDENCE": exp_win_abstention,
                    },
                },
            },
            "actual_decisions_summary": {
                "total_decisions": act_total,
                "normal_count": act_normal,
                "anomaly_count": act_anomaly,
                "insufficient_evidence_count": act_abstention,
                "recording_level_decisions": act_rec_scope,
                "window_level_decisions": act_win_scope,
                "recording_level_abstention_count": act_rec_abstention,
                "window_abstention_count": act_win_abstention,
                "by_scope": {
                    "recording_envelope": act_rec_scope,
                    "window": act_win_scope,
                },
                "by_outcome": {
                    "NORMAL": act_normal,
                    "ANOMALY": act_anomaly,
                    "INSUFFICIENT_EVIDENCE": act_abstention,
                },
                "by_scope_and_outcome": {
                    "recording_envelope": {
                        "NORMAL": act_rec_normal,
                        "ANOMALY": act_rec_anomaly,
                        "INSUFFICIENT_EVIDENCE": act_rec_abstention,
                    },
                    "window": {
                        "NORMAL": act_win_normal,
                        "ANOMALY": act_win_anomaly,
                        "INSUFFICIENT_EVIDENCE": act_win_abstention,
                    },
                },
            },
        },
        "cases": case_results,
    }

    return exit_code, report
