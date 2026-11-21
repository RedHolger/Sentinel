"""Installed portfolio CLI entry point for ADI Edge Portfolio."""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence
import uuid

from adi_common import (
    ContractValidationError,
    EvidenceRegistry,
    OperationalAdmissionDisabledError,
    evaluate_measurement_admission,
    validate_calibration_measurement_evidence,
    validate_measurement_admission_decision,
)
from adi_runtime import (
    ConfigurationError,
    ThresholdReferenceError,
    evaluate_rms_threshold,
)
from adi_app.ingestion import (
    IngestionDigestMismatchError,
    IngestionFileNotFoundError,
    IngestionMetadataError,
    IngestionParseError,
    RecordingIngestionError,
    load_vibration_csv,
)
from adi_app.source_inspection import (
    SourceInspectionError,
    _validate_path_argument,
    inspect_source,
)


class CLIError(Exception):
    """Raised for expected CLI user, argument, file, or parse failures."""
    pass


def _reject_duplicate_keys_hook(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
    """Strict JSON object pairs hook rejecting duplicate keys."""
    keys: set[str] = set()
    result: Dict[str, Any] = {}
    for k, v in pairs:
        if k in keys:
            raise ValueError(f"Duplicate key '{k}' found in JSON object")
        keys.add(k)
        result[k] = v
    return result


def _reject_nonstandard_constants(constant: str) -> None:
    """Strict JSON constant callback rejecting nonstandard constants like NaN and Infinity."""
    raise ValueError(f"Nonstandard JSON constant '{constant}' is forbidden")


def load_strict_json_object(file_path: str | Path, context_name: str) -> Dict[str, Any]:
    """Load a UTF-8 JSON object from file_path with strict syntax checks.

    Enforces:
    - Path existence and regular file check
    - UTF-8 decoding
    - No duplicate object keys
    - No NaN / Infinity constants
    - Top-level value must be a JSON object (dict)
    - Actionable path context on any failure
    """
    path = Path(file_path)
    if not path.exists():
        raise CLIError(f"File not found for {context_name}: '{path}'")
    if not path.is_file():
        raise CLIError(f"Path is not a regular file for {context_name}: '{path}'")

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise CLIError(f"Failed to read file for {context_name} '{path}': {exc}")

    if len(raw_bytes) == 0:
        raise CLIError(f"File for {context_name} '{path}' is empty (0 bytes)")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CLIError(f"File for {context_name} '{path}' is not valid UTF-8: {exc}")

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys_hook,
            parse_constant=_reject_nonstandard_constants,
        )
    except json.JSONDecodeError as exc:
        raise CLIError(
            f"Malformed JSON in {context_name} '{path}' at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        )
    except ValueError as exc:
        raise CLIError(f"Invalid JSON in {context_name} '{path}': {exc}")

    if not isinstance(parsed, dict):
        raise CLIError(
            f"Top-level JSON value in {context_name} '{path}' must be an object, got {type(parsed).__name__}"
        )

    return parsed


def execute_analyze(
    csv_path: str | Path,
    metadata_path: str | Path,
    config_path: str | Path,
    expected_sha256: Optional[str] = None,
) -> int:
    """Execute the analyze workflow connecting CSV ingestion to threshold detector.

    Returns exit code 0 on successful analysis (NORMAL, ANOMALY, INSUFFICIENT_EVIDENCE).
    Raises CLIError, RecordingIngestionError, ThresholdReferenceError, or ContractValidationError
    on expected user, input, or validation failures.
    """
    # 1. Load metadata JSON
    metadata = load_strict_json_object(metadata_path, "metadata")

    # 2. Load detector configuration JSON
    config = load_strict_json_object(config_path, "config")

    # 3. Ingest CSV file into validated VibrationRecordingV1
    csv_p = Path(csv_path)
    if not csv_p.exists():
        raise CLIError(f"CSV file not found: '{csv_p}'")
    if not csv_p.is_file():
        raise CLIError(f"CSV path is not a regular file: '{csv_p}'")

    recording = load_vibration_csv(
        csv_p,
        metadata=metadata,
        expected_sha256=expected_sha256,
    )

    # 4. Evaluate against RMS threshold detector reference
    result = evaluate_rms_threshold(recording, config)

    # 5. Build reconciled decision summary
    decisions = result.get("decisions", [])
    window_boundaries = result.get("window_boundaries", [])
    status = result.get("status", "COMPLETED")

    normal_count = sum(1 for d in decisions if d.get("outcome") == "NORMAL")
    anomaly_count = sum(1 for d in decisions if d.get("outcome") == "ANOMALY")
    insufficient_evidence_count = sum(
        1 for d in decisions if d.get("outcome") == "INSUFFICIENT_EVIDENCE"
    )
    total_decisions = len(decisions)

    # Invariant: total must match sum of individual outcomes
    assert total_decisions == (normal_count + anomaly_count + insufficient_evidence_count), (
        f"Decision count mismatch: {total_decisions} != "
        f"{normal_count} + {anomaly_count} + {insufficient_evidence_count}"
    )

    # Distinguish recording-level abstention from window-level counts
    is_recording_level = status == "INSUFFICIENT_EVIDENCE" and len(window_boundaries) == 0

    if is_recording_level:
        window_counts = {
            "anomaly": 0,
            "insufficient_evidence": 0,
            "normal": 0,
        }
    else:
        window_counts = {
            "anomaly": anomaly_count,
            "insufficient_evidence": insufficient_evidence_count,
            "normal": normal_count,
        }

    summary = {
        "anomaly_count": anomaly_count,
        "insufficient_evidence_count": insufficient_evidence_count,
        "normal_count": normal_count,
        "recording_level_abstention": is_recording_level,
        "total_decisions": total_decisions,
        "window_level_counts": window_counts,
    }

    # 6. Formulate deterministic single JSON document output
    output_doc = {
        "command": "analyze",
        "configuration": result["configuration"],
        "configuration_id": result["configuration_id"],
        "decisions": result["decisions"],
        "detector_id": result["detector_id"],
        "detector_version": result["detector_version"],
        "evaluation_scope": result["evaluation_scope"],
        "execution_environment": result["execution_environment"],
        "provenance": result["provenance"],
        "recording_id": result["recording_id"],
        "schema_version": "1.0.0",
        "source_sha256": result["source_sha256"],
        "status": status,
        "summary": summary,
        "trailing_samples": result["trailing_samples"],
        "window_boundaries": result["window_boundaries"],
    }

    output_json = json.dumps(
        output_doc,
        indent=2,
        sort_keys=True,
        allow_nan=False,
        ensure_ascii=True,
    )
    sys.stdout.write(output_json + "\n")
    return 0


def execute_inspect_source(
    mat_path: str | Path,
    variable: str,
    out_path: Optional[str | Path] = None,
) -> int:
    """Execute bounded source inspection on a local MATLAB container.

    Returns:
        0 if structural_status == "STRUCTURAL_SUCCESS"
        1 if completed report with non-success structural_status
    Raises:
        CLIError, SourceInspectionError on user, input, or output write failures.
    """
    mat_p = _validate_path_argument(mat_path, context="Input path")
    out_p = _validate_path_argument(out_path, context="Output path") if out_path is not None else None

    if out_p is not None:
        try:
            if mat_p.exists() and out_p.exists():
                mat_st = mat_p.stat()
                out_st = out_p.stat()
                if mat_st.st_ino == out_st.st_ino and mat_st.st_dev == out_st.st_dev:
                    raise CLIError(f"Output path cannot be identical to input MAT file: '{out_p}'")
        except (OSError, ValueError, UnicodeError):
            pass

        try:
            if mat_p.resolve() == out_p.resolve():
                raise CLIError(f"Output path cannot be identical to input MAT file: '{out_p}'")
        except (OSError, ValueError, UnicodeError):
            pass

        if out_p.exists():
            raise CLIError(f"Output file already exists and will not be overwritten: '{out_p}'")
        if not out_p.parent.exists():
            raise CLIError(f"Output directory does not exist: '{out_p.parent}'")
        if not out_p.parent.is_dir():
            raise CLIError(f"Output parent path is not a directory: '{out_p.parent}'")

    report = inspect_source(path=mat_p, variable=variable)

    output_json = json.dumps(
        report,
        indent=2,
        sort_keys=True,
        allow_nan=False,
        ensure_ascii=True,
    )

    if out_p is not None:
        if out_p.exists():
            raise CLIError(f"Output file already exists and will not be overwritten: '{out_p}'")

        temp_file = out_p.parent / f".{out_p.name}.tmp_{os.getpid()}_{uuid.uuid4().hex}"
        try:
            fd = os.open(temp_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write((output_json + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())

            if out_p.exists():
                raise CLIError(f"Output file already exists and will not be overwritten: '{out_p}'")

            try:
                os.link(temp_file, out_p)
            except FileExistsError:
                raise CLIError(f"Output file already exists and will not be overwritten: '{out_p}'")
            except OSError as exc:
                raise CLIError(f"Failed to publish inspection report to '{out_p}': {exc}") from exc
            finally:
                try:
                    temp_file.unlink(missing_ok=True)
                except OSError:
                    pass
        except Exception as exc:
            if isinstance(exc, CLIError):
                raise
            raise CLIError(f"Failed to write inspection report to '{out_p}': {exc}") from exc
        finally:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)

    sys.stdout.write(output_json + "\n")
    findings = report.get("inspection_findings", {})
    return 0 if findings.get("structural_status") == "STRUCTURAL_SUCCESS" else 1


ALLOWED_OPERATIONAL_CONTEXTS = {
    "ACADEMIC_RESEARCH_NONCOMMERCIAL",
    "COMMERCIAL_R_AND_D",
    "INTERNAL_TESTING",
    "DATASET_REDISTRIBUTION",
    "ARTIFACT_PUBLICATION",
}


def execute_validate_measurement_evidence(evidence_path: str | Path) -> int:
    """Validate calibration and measurement evidence JSON against Draft 2020-12 schema.

    Returns:
        0 on successful validation
        1 on schema / contract validation error
    Raises:
        CLIError, FileNotFoundError, OSError on file or parse failures
    """
    evidence = load_strict_json_object(evidence_path, "measurement evidence")
    try:
        validate_calibration_measurement_evidence(evidence)
    except ContractValidationError as exc:
        sys.stderr.write(f"Validation failed for measurement evidence '{evidence_path}': {exc}\n")
        return 1

    target_var = evidence.get("target_variable_name")
    if target_var is None and isinstance(evidence.get("acquisition_signal_interpretation"), dict):
        target_var = evidence["acquisition_signal_interpretation"].get("target_channel_variable")

    result = {
        "evidence_record_id": evidence.get("evidence_record_id"),
        "schema_version": "1.0.0",
        "status": "VALID",
        "target_channel": target_var,
    }
    output_json = json.dumps(
        result,
        indent=2,
        sort_keys=True,
        allow_nan=False,
        ensure_ascii=True,
    )
    sys.stdout.write(output_json + "\n")
    return 0


def execute_simulate_measurement_admission(
    evidence_path: str | Path,
    inspection_path: str | Path,
    context: str = "ACADEMIC_RESEARCH_NONCOMMERCIAL",
    signal_integrity_path: Optional[str | Path] = None,
    partitions_path: Optional[str | Path] = None,
    out_path: Optional[str | Path] = None,
    source_artifact: Optional[str | Path] = None,
) -> int:
    """Simulate deterministic measurement admission decision on fictional fixtures.

    Returns:
        0 if ADMITTED_FOR_MEASUREMENT
        1 if BLOCKED_* (refused admission)
    Raises:
        CLIError, ContractValidationError, OperationalAdmissionDisabledError on invalid inputs/contexts
    """
    if context not in ALLOWED_OPERATIONAL_CONTEXTS:
        raise CLIError(
            f"Unsupported operational context: '{context}'. Must be one of: {sorted(ALLOWED_OPERATIONAL_CONTEXTS)}"
        )

    evidence = load_strict_json_object(evidence_path, "measurement evidence")
    inspection = load_strict_json_object(inspection_path, "inspection report")
    sig_integrity = None
    if signal_integrity_path is not None:
        sig_integrity = load_strict_json_object(signal_integrity_path, "signal integrity evidence")
    partitions = None
    if partitions_path is not None:
        partitions = load_strict_json_object(partitions_path, "partition assignments")

    registry = EvidenceRegistry.create_fictional_test_registry()
    if source_artifact is None:
        raise CLIError("A required --source-artifact is missing; external signal claims are not source-bound.")

    decision = evaluate_measurement_admission(
        evidence=evidence,
        stage10_report=inspection,
        intended_use=context,
        evidence_registry=registry,
        signal_integrity_evidence=sig_integrity,
        partition_assignments=partitions,
        operational_mode=False,
        source_artifact=source_artifact,
    )

    validate_measurement_admission_decision(decision)

    output_json = json.dumps(
        decision,
        indent=2,
        sort_keys=True,
        allow_nan=False,
        ensure_ascii=True,
    )

    if out_path is not None:
        out_p = _validate_path_argument(out_path, context="Output path")
        if out_p.exists():
            raise CLIError(f"Output file already exists and will not be overwritten: '{out_p}'")
        if not out_p.parent.exists():
            raise CLIError(f"Output directory does not exist: '{out_p.parent}'")

        temp_file = out_p.with_name(f".{out_p.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_file, "xb") as f:
                f.write((output_json + "\n").encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())

            if out_p.exists():
                raise CLIError(f"Output file already exists and will not be overwritten: '{out_p}'")

            try:
                os.link(temp_file, out_p)
            except FileExistsError:
                raise CLIError(f"Output file already exists and will not be overwritten: '{out_p}'")
            except OSError as exc:
                raise CLIError(f"Failed to publish admission decision to '{out_p}': {exc}") from exc
            finally:
                try:
                    temp_file.unlink(missing_ok=True)
                except OSError:
                    pass
        except Exception as exc:
            if isinstance(exc, CLIError):
                raise
            raise CLIError(f"Failed to write admission decision to '{out_p}': {exc}") from exc
        finally:
            if temp_file.exists():
                temp_file.unlink(missing_ok=True)

    sys.stdout.write(output_json + "\n")
    return 0 if decision.get("measurement_admission_status") == "ADMITTED_FOR_MEASUREMENT" else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI main entry point for adi-portfolio."""
    parser = argparse.ArgumentParser(
        prog="adi-portfolio",
        description="ADI Portfolio CLI: Edge Sentinel offline analysis and scaffolding",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=10,
        help="Trial count for scaffold benchmarks (default: 10)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # 1. demo (scaffold)
    subparsers.add_parser("demo", help="Show demo scaffold information")

    # 2. benchmark (scaffold)
    bench_parser = subparsers.add_parser("benchmark", help="Run benchmark scaffold")
    bench_parser.add_argument("--trials", type=int, default=10, help="Trial count (default: 10)")

    # 3. evaluate (scaffold or scenario suite)
    eval_parser = subparsers.add_parser("evaluate", help="Run evaluate scaffold or scenario evaluation suite")
    eval_parser.add_argument("--trials", type=int, default=10, help="Trial count (default: 10)")
    eval_parser.add_argument(
        "--suite",
        type=str,
        default=None,
        help="Path to scenario evaluation suite JSON manifest",
    )

    # 4. analyze (Stage 05 implementation)
    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze vibration CSV recording against RMS threshold detector reference"
    )
    analyze_parser.add_argument(
        "--csv",
        type=str,
        required=True,
        help="Path to local vibration recording CSV file",
    )
    analyze_parser.add_argument(
        "--metadata",
        type=str,
        required=True,
        help="Path to local recording metadata JSON file",
    )
    analyze_parser.add_argument(
        "--config",
        type=str,
        required=True,
        help="Path to local detector configuration JSON file",
    )
    analyze_parser.add_argument(
        "--expected-sha256",
        type=str,
        default=None,
        help="Optional expected 64-hex SHA-256 hash of raw CSV bytes",
    )

    # 5. inspect-source (Stage 10 implementation)
    inspect_parser = subparsers.add_parser(
        "inspect-source",
        help="Inspect local MATLAB file variable structure and metadata gaps",
    )
    inspect_parser.add_argument(
        "--mat",
        type=str,
        required=True,
        help="Path to local MATLAB .mat file",
    )
    inspect_parser.add_argument(
        "--variable",
        type=str,
        required=True,
        help="Target MATLAB variable name to inspect",
    )
    inspect_parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Optional path to write inspection report JSON atomically",
    )

    # 6. validate-measurement-evidence (Stage 12 implementation)
    val_parser = subparsers.add_parser(
        "validate-measurement-evidence",
        aliases=["validate-evidence"],
        help="Validate calibration and measurement evidence JSON against Draft 2020-12 contract",
    )
    val_parser.add_argument(
        "--evidence",
        type=str,
        required=True,
        help="Path to local calibration measurement evidence JSON file",
    )

    # 7. simulate-measurement-admission (Stage 12 implementation)
    sim_parser = subparsers.add_parser(
        "simulate-measurement-admission",
        aliases=["simulate-admission"],
        help="Simulate deterministic measurement admission decision on fictional fixtures",
    )
    sim_parser.add_argument(
        "--evidence",
        type=str,
        required=True,
        help="Path to local calibration measurement evidence JSON file",
    )
    sim_parser.add_argument(
        "--inspection",
        type=str,
        required=True,
        help="Path to local Stage 10 source inspection report JSON file",
    )
    sim_parser.add_argument(
        "--context",
        type=str,
        default="ACADEMIC_RESEARCH_NONCOMMERCIAL",
        help="Operational context for evaluation (default: ACADEMIC_RESEARCH_NONCOMMERCIAL)",
    )
    sim_parser.add_argument(
        "--signal-integrity",
        type=str,
        default=None,
        help="Optional path to signal integrity verification evidence JSON file",
    )
    sim_parser.add_argument(
        "--source-artifact",
        type=str,
        required=True,
        help="Required bounded fictional source artifact JSON with an ADI_FICTIONAL_SOURCE_V1 payload",
    )
    sim_parser.add_argument(
        "--partitions",
        type=str,
        default=None,
        help="Optional path to partition assignments JSON file",
    )
    sim_parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="Optional path to write decision JSON atomically",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    if not args.command:
        parser.print_help(sys.stderr)
        return 2

    if args.command == "demo":
        sys.stdout.write(
            "ADI Portfolio Demo: Baseline CLI scaffold only. Edge inference and deterministic verification are not implemented.\n"
        )
        return 0
    elif args.command == "benchmark":
        sys.stdout.write(
            json.dumps(
                {
                    "status": "NOT_IMPLEMENTED",
                    "reason": "Pipeline latency benchmarking harness is not implemented in baseline scaffold.",
                    "hardware_status": "UNVERIFIED",
                },
                indent=2,
            )
            + "\n"
        )
        return 1
    elif args.command == "evaluate":
        if args.suite is not None:
            from adi_app.scenario_evaluator import ScenarioSuiteError, evaluate_scenario_suite
            try:
                code, report = evaluate_scenario_suite(args.suite)
            except (ScenarioSuiteError, CLIError, FileNotFoundError, OSError) as exc:
                sys.stderr.write(f"adi-portfolio evaluate error: {exc}\n")
                return 2
            sys.stdout.write(
                json.dumps(
                    report,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                    ensure_ascii=True,
                )
                + "\n"
            )
            return code
        else:
            sys.stdout.write(
                json.dumps(
                    {
                        "status": "NOT_IMPLEMENTED",
                        "reason": "Evaluation suite and test fixtures are not implemented in baseline scaffold.",
                        "hardware_status": "UNVERIFIED",
                    },
                    indent=2,
                )
                + "\n"
            )
            return 1
    elif args.command == "analyze":
        try:
            return execute_analyze(
                csv_path=args.csv,
                metadata_path=args.metadata,
                config_path=args.config,
                expected_sha256=args.expected_sha256,
            )
        except (
            CLIError,
            RecordingIngestionError,
            ThresholdReferenceError,
            ContractValidationError,
            FileNotFoundError,
            OSError,
        ) as exc:
            sys.stderr.write(f"adi-portfolio analyze error: {exc}\n")
            return 2
    elif args.command == "inspect-source":
        try:
            return execute_inspect_source(
                mat_path=args.mat,
                variable=args.variable,
                out_path=args.out,
            )
        except (
            CLIError,
            SourceInspectionError,
            ContractValidationError,
            ValueError,
            UnicodeError,
            FileNotFoundError,
            OSError,
        ) as exc:
            sys.stderr.write(f"adi-portfolio inspect-source error: {exc}\n")
            return 2
    elif args.command in ("validate-measurement-evidence", "validate-evidence"):
        try:
            return execute_validate_measurement_evidence(args.evidence)
        except (CLIError, FileNotFoundError, OSError, ValueError, UnicodeError) as exc:
            sys.stderr.write(f"adi-portfolio validate-measurement-evidence error: {exc}\n")
            return 2
    elif args.command in ("simulate-measurement-admission", "simulate-admission"):
        try:
            return execute_simulate_measurement_admission(
                evidence_path=args.evidence,
                inspection_path=args.inspection,
                context=args.context,
                signal_integrity_path=args.signal_integrity,
                partitions_path=args.partitions,
                out_path=args.out,
                source_artifact=args.source_artifact,
            )
        except (
            CLIError,
            ContractValidationError,
            OperationalAdmissionDisabledError,
            FileNotFoundError,
            OSError,
            ValueError,
            UnicodeError,
        ) as exc:
            sys.stderr.write(f"adi-portfolio simulate-measurement-admission error: {exc}\n")
            return 2

    return 2


if __name__ == "__main__":
    sys.exit(main())
