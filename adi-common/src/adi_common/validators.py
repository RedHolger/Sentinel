"""Canonical validators and packaged schema loaders for ADI Common contracts.

Enforces versioned contracts, structural invariants, and semantic constraints
for multi-axis vibration recordings and detector anomaly decisions.
Validation is deterministic and side-effect free.
"""

from __future__ import annotations

import copy
from importlib import resources
import json
import math
import re
import sys
from typing import Any, Dict, List, Optional

import jsonschema


class ContractValidationError(ValueError):
    """Raised when a data payload fails contract schema or semantic invariant validation."""

    def __init__(self, field: Optional[str], message: str, value: Any = None):
        self.field = field
        self.message = message
        self.value = value
        prefix = f"Field '{field}': " if field else ""
        super().__init__(f"{prefix}{message}")


def load_schema(schema_name: str) -> Dict[str, Any]:
    """Load a packaged JSON schema from adi_common.schemas using importlib.resources.

    Args:
        schema_name: Name of the schema file (e.g. 'vibration_recording.schema.v1.json').

    Returns:
        Loaded JSON schema as a dictionary.

    Raises:
        FileNotFoundError: If the schema file cannot be located or loaded.
    """
    try:
        content = (
            resources.files("adi_common.schemas")
            .joinpath(schema_name)
            .read_text(encoding="utf-8")
        )
        return json.loads(content)
    except Exception as exc:
        raise FileNotFoundError(
            f"Packaged schema '{schema_name}' could not be loaded from adi_common.schemas: {exc}"
        ) from exc


# Pre-loaded schemas (cached in memory for zero IO per validation call)
_VIBRATION_RECORDING_SCHEMA = load_schema("vibration_recording.schema.v1.json")
_ANOMALY_DECISION_SCHEMA = load_schema("anomaly_decision.schema.v1.json")
_CALIBRATION_EVIDENCE_SCHEMA = load_schema("calibration_measurement_evidence.schema.v1.json")
_CALIBRATION_EVIDENCE_VALIDATOR = jsonschema.Draft202012Validator(_CALIBRATION_EVIDENCE_SCHEMA)
_MEASUREMENT_DECISION_SCHEMA = load_schema("measurement_admission_decision.schema.v1.json")
_MEASUREMENT_DECISION_VALIDATOR = jsonschema.Draft202012Validator(_MEASUREMENT_DECISION_SCHEMA)

_SHA256_REGEX = re.compile(r"^[a-fA-F0-9]{64}\Z")
_ALLOWED_CHANNELS = ("x", "y", "z")
_ALLOWED_UNITS = ("g", "m/s^2")
_ALLOWED_PROVENANCE = ("synthetic", "recorded")
_ALLOWED_OUTCOMES = ("NORMAL", "ANOMALY", "INSUFFICIENT_EVIDENCE")
_ALLOWED_ENVIRONMENTS = ("HOST_CPU",)
_ALLOWED_QUALITY_STATUSES = ("PASS", "FAIL", "INDETERMINATE")
_ALLOWED_SIGNAL_UNITS = ("g", "m/s^2")
_TRUSTED_SIGNAL_ANALYZER = "adi_common.admission.analyze_signal_integrity"
_SIGNAL_ANALYSIS_POLICY_VERSION = "stage12.signal-integrity.v2"

# IEEE 754 binary64 (double-precision float) domain limits
# Max finite magnitude: (2 - 2^-52) * 2^1023 ≈ 1.7976931348623157e+308
_FLOAT_INFO_MAX = sys.float_info.max
_BINARY64_MAX_INT = int(_FLOAT_INFO_MAX)


def _validate_numeric_binary64(val: Any, field_name: str) -> None:
    """Validate that a value is numeric and within the finite IEEE 754 binary64 domain.

    Permitted domain: [-sys.float_info.max, sys.float_info.max].
    Rejects booleans, non-numeric types, NaN, +Infinity, -Infinity, and Python integers
    exceeding the binary64 representable range (preventing OverflowError).
    """
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ContractValidationError(
            field_name,
            f"Field '{field_name}' must be numeric, got {type(val).__name__}",
            val,
        )
    if isinstance(val, int):
        if val > _BINARY64_MAX_INT or val < -_BINARY64_MAX_INT:
            raise ContractValidationError(
                field_name,
                f"Field '{field_name}' value {val!r} exceeds finite binary64 numeric domain "
                f"[-{_FLOAT_INFO_MAX}, {_FLOAT_INFO_MAX}]",
                val,
            )
    else:  # float
        if not math.isfinite(val):
            raise ContractValidationError(
                field_name,
                f"Field '{field_name}' must be a finite number (no NaN or Infinity), got {val}",
                val,
            )


_VIBRATION_RECORDING_KEYS = {
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
}

_ANOMALY_DECISION_KEYS = {
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
}


def _run_jsonschema_validation(payload: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """Run JSON Schema validation using the required jsonschema library."""
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as err:
        field = ".".join(str(p) for p in err.path) if err.path else None
        raise ContractValidationError(field, err.message, err.instance) from err


def validate_vibration_recording(payload: Any) -> Dict[str, Any]:
    """Validate a multi-axis vibration recording payload against V1 contract invariants.

    Enforces:
    - schema_version == "1.0.0"
    - Nonempty identifiers: recording_id, machine_id, session_id, source_id
    - provenance in ('synthetic', 'recorded')
    - sample_rate_hz > 0, finite, non-boolean
    - acceleration_unit in ('g', 'm/s^2')
    - channels unique subset of ('x', 'y', 'z'), 1-3 items
    - samples 2D matrix [sample, channel], nonempty, rectangular, matching channel count
    - No boolean or non-finite values in samples
    - source_sha256 matches 64 hex characters (establishes byte integrity, not metadata authenticity)
    - additionalProperties: false (no unknown fields)
    - Input immutability (input payload is not modified)
    - Zero side effects (no file IO, no network calls)

    Args:
        payload: Dict representation of vibration recording.

    Returns:
        The validated payload dictionary.

    Raises:
        ContractValidationError: On any schema or semantic invariant violation.
    """
    if not isinstance(payload, dict):
        raise ContractValidationError(
            None, f"Payload must be a dictionary, got {type(payload).__name__}", payload
        )

    # Check for unknown / extra fields
    unknown = set(payload.keys()) - _VIBRATION_RECORDING_KEYS
    if unknown:
        raise ContractValidationError(
            "unknown_fields",
            f"Disallowed unknown field(s): {sorted(unknown)}. additionalProperties is false.",
            unknown,
        )

    # Check for missing required fields
    missing = _VIBRATION_RECORDING_KEYS - set(payload.keys())
    if missing:
        raise ContractValidationError(
            "missing_fields",
            f"Missing required contract field(s): {sorted(missing)}",
            missing,
        )

    # Schema version
    version = payload.get("schema_version")
    if version != "1.0.0":
        raise ContractValidationError(
            "schema_version",
            f"Unsupported schema version: {version!r}. Expected '1.0.0'.",
            version,
        )

    # Nonempty string identifiers
    for field_name in ("recording_id", "machine_id", "session_id", "source_id"):
        val = payload.get(field_name)
        if not isinstance(val, str) or len(val.strip()) == 0:
            raise ContractValidationError(
                field_name,
                f"Field '{field_name}' must be a nonempty string, got {val!r}",
                val,
            )

    # Provenance
    prov = payload.get("provenance")
    if prov not in _ALLOWED_PROVENANCE:
        raise ContractValidationError(
            "provenance",
            f"Field 'provenance' must be one of {_ALLOWED_PROVENANCE}, got {prov!r}",
            prov,
        )

    # Sample rate (positive, finite binary64, non-bool)
    sr = payload.get("sample_rate_hz")
    _validate_numeric_binary64(sr, "sample_rate_hz")
    if sr <= 0:
        raise ContractValidationError(
            "sample_rate_hz",
            f"Field 'sample_rate_hz' must be strictly positive (> 0), got {sr}",
            sr,
        )

    # Acceleration unit
    unit = payload.get("acceleration_unit")
    if unit not in _ALLOWED_UNITS:
        raise ContractValidationError(
            "acceleration_unit",
            f"Field 'acceleration_unit' must be one of {_ALLOWED_UNITS}, got {unit!r}",
            unit,
        )

    # Channels
    channels = payload.get("channels")
    if not isinstance(channels, list) or len(channels) == 0:
        raise ContractValidationError(
            "channels",
            f"Field 'channels' must be a nonempty list, got {channels!r}",
            channels,
        )
    if len(channels) > 3:
        raise ContractValidationError(
            "channels",
            f"Field 'channels' must contain at most 3 channels, got {len(channels)}",
            channels,
        )
    for ch in channels:
        if ch not in _ALLOWED_CHANNELS:
            raise ContractValidationError(
                "channels",
                f"Invalid channel {ch!r}. Allowed channels are {_ALLOWED_CHANNELS}",
                ch,
            )
    if len(channels) != len(set(channels)):
        raise ContractValidationError(
            "channels",
            f"Duplicate channel names are not permitted: {channels}",
            channels,
        )

    num_channels = len(channels)

    # Samples matrix
    samples = payload.get("samples")
    if not isinstance(samples, list) or len(samples) == 0:
        raise ContractValidationError(
            "samples",
            f"Field 'samples' must be a nonempty list of rows, got {samples!r}",
            samples,
        )

    for i, row in enumerate(samples):
        if not isinstance(row, list):
            raise ContractValidationError(
                f"samples[{i}]",
                f"Sample row {i} must be a list, got {type(row).__name__}",
                row,
            )
        if len(row) != num_channels:
            raise ContractValidationError(
                f"samples[{i}]",
                f"Sample row {i} length {len(row)} does not match channel count {num_channels}",
                row,
            )
        for j, val in enumerate(row):
            if isinstance(val, bool):
                raise ContractValidationError(
                    f"samples[{i}][{j}]",
                    f"Boolean value not allowed in samples at [{i}][{j}], got {val!r}",
                    val,
                )
            _validate_numeric_binary64(val, f"samples[{i}][{j}]")

    # Source SHA-256 (must be exact 64-hex string, no whitespace, no newlines)
    sha = payload.get("source_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or not _SHA256_REGEX.fullmatch(sha):
        raise ContractValidationError(
            "source_sha256",
            f"Field 'source_sha256' must be an exact 64-character hexadecimal digest without whitespace or newlines, got {sha!r}",
            sha,
        )

    # JSONSchema engine validation
    _run_jsonschema_validation(payload, _VIBRATION_RECORDING_SCHEMA)

    return payload


def validate_anomaly_decision(payload: Any) -> Dict[str, Any]:
    """Validate a detector anomaly decision payload against V1 contract invariants.

    Enforces:
    - schema_version == "1.0.0"
    - Nonempty identifiers: decision_id, recording_ref, detector_id, detector_version
    - outcome in ('NORMAL', 'ANOMALY', 'INSUFFICIENT_EVIDENCE')
    - execution_environment == 'HOST_CPU' (rejects unmeasured embedded silicon targets)
    - quality_status in ('PASS', 'FAIL', 'INDETERMINATE')
    - reason_codes is a list of nonempty strings
    - For NORMAL and ANOMALY:
      - quality_status == 'PASS'
      - score and threshold are finite numbers, non-boolean
      - score >= threshold => ANOMALY (rejects score < threshold)
      - score < threshold => NORMAL (rejects score >= threshold)
      - score == threshold => ANOMALY
    - For INSUFFICIENT_EVIDENCE:
      - score is None (null)
      - threshold is None (null)
      - len(reason_codes) >= 1
      - Does not imply a healthy machine or completed diagnosis
    - additionalProperties: false (no unknown fields)
    - Input immutability (input payload is not modified)
    - Zero side effects (no file IO, no network calls)

    Args:
        payload: Dict representation of anomaly decision.

    Returns:
        The validated payload dictionary.

    Raises:
        ContractValidationError: On any schema or semantic invariant violation.
    """
    if not isinstance(payload, dict):
        raise ContractValidationError(
            None, f"Payload must be a dictionary, got {type(payload).__name__}", payload
        )

    # Check for unknown / extra fields
    unknown = set(payload.keys()) - _ANOMALY_DECISION_KEYS
    if unknown:
        raise ContractValidationError(
            "unknown_fields",
            f"Disallowed unknown field(s): {sorted(unknown)}. additionalProperties is false.",
            unknown,
        )

    # Check for missing required fields
    missing = _ANOMALY_DECISION_KEYS - set(payload.keys())
    if missing:
        raise ContractValidationError(
            "missing_fields",
            f"Missing required contract field(s): {sorted(missing)}",
            missing,
        )

    # Schema version
    version = payload.get("schema_version")
    if version != "1.0.0":
        raise ContractValidationError(
            "schema_version",
            f"Unsupported schema version: {version!r}. Expected '1.0.0'.",
            version,
        )

    # Nonempty string identifiers
    for field_name in ("decision_id", "recording_ref", "detector_id", "detector_version"):
        val = payload.get(field_name)
        if not isinstance(val, str) or len(val.strip()) == 0:
            raise ContractValidationError(
                field_name,
                f"Field '{field_name}' must be a nonempty string, got {val!r}",
                val,
            )

    # Execution environment
    env = payload.get("execution_environment")
    if env != "HOST_CPU":
        raise ContractValidationError(
            "execution_environment",
            f"Unsupported or unmeasured execution environment: {env!r}. "
            "Only 'HOST_CPU' is verified; embedded silicon acceleration has not been measured.",
            env,
        )

    # Outcome
    outcome = payload.get("outcome")
    if outcome not in _ALLOWED_OUTCOMES:
        raise ContractValidationError(
            "outcome",
            f"Field 'outcome' must be one of {_ALLOWED_OUTCOMES}, got {outcome!r}",
            outcome,
        )

    # Quality status
    qs = payload.get("quality_status")
    if qs not in _ALLOWED_QUALITY_STATUSES:
        raise ContractValidationError(
            "quality_status",
            f"Field 'quality_status' must be one of {_ALLOWED_QUALITY_STATUSES}, got {qs!r}",
            qs,
        )

    # Reason codes
    reasons = payload.get("reason_codes")
    if not isinstance(reasons, list):
        raise ContractValidationError(
            "reason_codes",
            f"Field 'reason_codes' must be a list of strings, got {type(reasons).__name__}",
            reasons,
        )
    for idx, r in enumerate(reasons):
        if not isinstance(r, str) or len(r.strip()) == 0:
            raise ContractValidationError(
                f"reason_codes[{idx}]",
                f"Reason code at index {idx} must be a nonempty string, got {r!r}",
                r,
            )

    score = payload.get("score")
    threshold = payload.get("threshold")

    # Invariants specific to outcome
    if outcome in ("NORMAL", "ANOMALY"):
        if qs != "PASS":
            raise ContractValidationError(
                "quality_status",
                f"Outcome '{outcome}' requires quality_status 'PASS', got {qs!r}",
                qs,
            )

        # Score checks (finite binary64, non-bool)
        if score is None:
            raise ContractValidationError(
                "score",
                f"Outcome '{outcome}' requires finite numeric score, got None",
                score,
            )
        _validate_numeric_binary64(score, "score")

        # Threshold checks (finite binary64, non-bool)
        if threshold is None:
            raise ContractValidationError(
                "threshold",
                f"Outcome '{outcome}' requires finite numeric threshold, got None",
                threshold,
            )
        _validate_numeric_binary64(threshold, "threshold")

        # Score vs Threshold convention
        # score >= threshold => ANOMALY
        # score < threshold => NORMAL
        if outcome == "ANOMALY" and score < threshold:
            raise ContractValidationError(
                "outcome",
                f"Contradictory decision: outcome is ANOMALY but score {score} < threshold {threshold}",
                outcome,
            )
        if outcome == "NORMAL" and score >= threshold:
            raise ContractValidationError(
                "outcome",
                f"Contradictory decision: outcome is NORMAL but score {score} >= threshold {threshold}",
                outcome,
            )

    elif outcome == "INSUFFICIENT_EVIDENCE":
        if score is not None:
            raise ContractValidationError(
                "score",
                f"Outcome 'INSUFFICIENT_EVIDENCE' requires score to be null/None, got {score!r}",
                score,
            )
        if threshold is not None:
            raise ContractValidationError(
                "threshold",
                f"Outcome 'INSUFFICIENT_EVIDENCE' requires threshold to be null/None, got {threshold!r}",
                threshold,
            )
        if len(reasons) == 0:
            raise ContractValidationError(
                "reason_codes",
                "Outcome 'INSUFFICIENT_EVIDENCE' requires at least one explanatory reason code",
                reasons,
            )

    # JSONSchema engine validation
    _run_jsonschema_validation(payload, _ANOMALY_DECISION_SCHEMA)

    return payload


def _check_no_non_json_values(val: Any, path: str = "") -> None:
    """Recursively ensure there are no NaN, Infinity, non-JSON types, or oversized ints."""
    if isinstance(val, bool):
        return
    elif isinstance(val, (int, float)):
        _validate_numeric_binary64(val, path or "value")
    elif isinstance(val, str):
        return
    elif val is None:
        return
    elif isinstance(val, dict):
        for k, v in val.items():
            if not isinstance(k, str):
                raise ContractValidationError(
                    path,
                    f"JSON object keys must be strings, got {type(k).__name__}",
                    k,
                )
            subpath = f"{path}.{k}" if path else k
            _check_no_non_json_values(v, subpath)
    elif isinstance(val, (list, tuple)):
        for idx, item in enumerate(val):
            subpath = f"{path}[{idx}]"
            _check_no_non_json_values(item, subpath)
    else:
        raise ContractValidationError(
            path,
            f"Value of non-JSON serializable type {type(val).__name__} is forbidden",
            val,
        )


def validate_calibration_measurement_evidence(evidence: Any) -> Dict[str, Any]:
    """Validate a CalibrationMeasurementEvidenceV1 contract payload.

    Enforces:
    - Top-level object structure and immutability
    - No non-JSON types, NaN, Infinity, or out-of-domain binary64 integers
    - JSON Schema Draft 2020-12 compliance against packaged canonical schema
    - Evidence status mutual exclusion and Tier 1/2/3 constraints
    - Non-negative rotational speeds / loads vs strictly positive physical quantities

    Args:
        evidence: The evidence payload dictionary to validate.

    Returns:
        The validated evidence payload dictionary (unmutated).

    Raises:
        ContractValidationError: If the payload fails contract schema or invariant checks.
    """
    if not isinstance(evidence, dict) or isinstance(evidence, bool):
        raise ContractValidationError(
            None,
            f"Evidence payload must be a JSON object (dict), got {type(evidence).__name__}",
            evidence,
        )

    # 1. Non-JSON / non-finite checks
    _check_no_non_json_values(evidence)

    # 2. Draft 2020-12 Schema validation
    errors = list(_CALIBRATION_EVIDENCE_VALIDATOR.iter_errors(evidence))
    if errors:
        first_err = errors[0]
        field = ".".join(str(p) for p in first_err.path) if first_err.path else None
        raise ContractValidationError(field, first_err.message, first_err.instance)

    return evidence


def validate_measurement_admission_decision(decision: Any) -> Dict[str, Any]:
    """Validate a MeasurementAdmissionDecisionV1 contract payload.

    Enforces:
    - Top-level object structure and immutability
    - No non-JSON types, NaN, Infinity, or out-of-domain binary64 integers
    - JSON Schema Draft 2020-12 compliance against packaged canonical schema
    - Strict 8-status admission enumeration and precedence gates
    - Decoupled diagnostic evaluation eligibility sub-record structure

    Args:
        decision: The admission decision payload dictionary to validate.

    Returns:
        The validated decision payload dictionary (unmutated).

    Raises:
        ContractValidationError: If the payload fails contract schema or invariant checks.
    """
    if not isinstance(decision, dict) or isinstance(decision, bool):
        raise ContractValidationError(
            None,
            f"Decision payload must be a JSON object (dict), got {type(decision).__name__}",
            decision,
        )

    # 1. Non-JSON / non-finite checks
    _check_no_non_json_values(decision)

    # Invariant enforcement: strictly fail closed on operational execution in Stage 12
    mode = decision.get("evaluation_mode")
    if mode != "FICTIONAL_TEST_ONLY":
        raise ContractValidationError(
            "evaluation_mode",
            f"Evaluation mode '{mode}' is forbidden; Stage 12 strictly enforces 'FICTIONAL_TEST_ONLY'",
            mode,
        )
    auth = decision.get("operational_execution_authorized")
    if auth is not False:
        raise ContractValidationError(
            "operational_execution_authorized",
            f"Operational execution authorization must be strictly False in Stage 12, got {auth!r}",
            auth,
        )

    # 2. Draft 2020-12 Schema validation
    errors = list(_MEASUREMENT_DECISION_VALIDATOR.iter_errors(decision))
    if errors:
        first_err = errors[0]
        field = ".".join(str(p) for p in first_err.path) if first_err.path else None
        raise ContractValidationError(field, first_err.message, first_err.instance)

    return decision


_VALID_SIGNAL_INTEGRITY_STATUSES = {
    "PASS",
    "SUSTAINED_RAIL_SATURATION",
    "EXCESSIVE_CLIPPING",
    "NONFINITE_DETECTED",
    "UNKNOWN_FULL_SCALE",
}


def validate_signal_integrity_evidence(
    sig_evidence: Any,
    expected_source_sha256: Optional[str] = None,
    expected_target_channel: Optional[str] = None,
    expected_sample_count: Optional[int] = None,
    expected_nonfinite_count: Optional[int] = None,
    stage10_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate a SignalIntegrityEvidenceV1 payload against formal contract and source facts.

    Enforces:
    1. Top-level object structure and non-mutation (dict, non-boolean).
    2. No non-JSON types, NaN, Infinity, or out-of-domain binary64 integers.
    3. Required top-level fields: schema_version, source_sha256, target_channel,
       sample_count, nonfinite_sample_count, verification_status, data_quality_provenance.
    4. Exact source_sha256 format (64-hex SHA-256).
    5. Nonnegative sample_count and nonfinite_sample_count integers.
    6. Nonnegative integer rail_sample_count and longest_contiguous_rail_run (or None if unknown).
    7. Invariant: longest_contiguous_rail_run <= rail_sample_count <= sample_count (when known).
    8. Invariant: clipping_ratio is consistent with rail_sample_count / sample_count.
    9. Recognized verification_status enumeration:
       PASS, SUSTAINED_RAIL_SATURATION, EXCESSIVE_CLIPPING, NONFINITE_DETECTED, UNKNOWN_FULL_SCALE.
    10. Trustworthy data_quality_provenance with non-empty analyzer name.
    11. If status is PASS: full_scale_limit must be a positive number, rail_sample_count >= 0,
        longest_contiguous_rail_run < 3, clipping_ratio <= 0.0001, and nonfinite_sample_count == 0.
    12. If expected source identity facts are provided:
        - Exact source_sha256 match.
        - Exact target_channel match.
        - Exact sample_count match.
        - Nonfinite sample count consistency.

    Args:
        sig_evidence: Signal integrity evidence dictionary.
        expected_source_sha256: Optional expected 64-hex SHA-256 digest.
        expected_target_channel: Optional expected target channel name.
        expected_sample_count: Optional expected sample count integer.
        expected_nonfinite_count: Optional expected nonfinite sample count integer.
        stage10_report: Optional authentic Stage 10 inspection report to extract expected facts from.

    Returns:
        The validated signal integrity evidence dictionary (unmutated).

    Raises:
        ContractValidationError: If any contract or consistency check fails.
    """
    if not isinstance(sig_evidence, dict) or isinstance(sig_evidence, bool):
        raise ContractValidationError(
            None,
            f"Signal integrity evidence payload must be a JSON object (dict), got {type(sig_evidence).__name__}",
            sig_evidence,
        )

    _check_no_non_json_values(sig_evidence)

    # 1. Required top-level fields
    required_fields = [
        "schema_version",
        "source_sha256",
        "target_channel",
        "sample_count",
        "nonfinite_sample_count",
        "verification_status",
        "data_quality_provenance",
    ]
    for rf in required_fields:
        if rf not in sig_evidence:
            raise ContractValidationError(
                rf,
                f"Missing required field '{rf}' in signal integrity evidence",
                sig_evidence,
            )

    # 2. Schema version
    if sig_evidence["schema_version"] != "1.0.0":
        raise ContractValidationError(
            "schema_version",
            f"Unsupported schema_version '{sig_evidence['schema_version']}', expected '1.0.0'",
            sig_evidence["schema_version"],
        )

    # 3. Source SHA-256 format
    src_sha = sig_evidence["source_sha256"]
    if not isinstance(src_sha, str) or not _SHA256_REGEX.match(src_sha):
        raise ContractValidationError(
            "source_sha256",
            f"Field 'source_sha256' must be a 64-hex SHA-256 digest, got {src_sha!r}",
            src_sha,
        )

    # 4. Target channel
    tgt_chan = sig_evidence["target_channel"]
    if not isinstance(tgt_chan, str) or not tgt_chan.strip():
        raise ContractValidationError(
            "target_channel",
            "Field 'target_channel' must be a non-empty string",
            tgt_chan,
        )

    # 5. Sample counts
    cnt = sig_evidence["sample_count"]
    if isinstance(cnt, bool) or not isinstance(cnt, int) or cnt < 0:
        raise ContractValidationError(
            "sample_count",
            f"Field 'sample_count' must be a non-negative integer, got {cnt!r}",
            cnt,
        )

    nonfinite = sig_evidence["nonfinite_sample_count"]
    if isinstance(nonfinite, bool) or not isinstance(nonfinite, int) or nonfinite < 0:
        raise ContractValidationError(
            "nonfinite_sample_count",
            f"Field 'nonfinite_sample_count' must be a non-negative integer, got {nonfinite!r}",
            nonfinite,
        )

    # 6. Verification status
    v_status = sig_evidence["verification_status"]
    if v_status not in _VALID_SIGNAL_INTEGRITY_STATUSES:
        raise ContractValidationError(
            "verification_status",
            f"Field 'verification_status' must be one of {sorted(_VALID_SIGNAL_INTEGRITY_STATUSES)}, got {v_status!r}",
            v_status,
        )

    # 7. Data quality provenance
    prov = sig_evidence["data_quality_provenance"]
    if not isinstance(prov, dict) or not prov.get("analyzer") or not isinstance(prov.get("analyzer"), str):
        raise ContractValidationError(
            "data_quality_provenance",
            "Field 'data_quality_provenance' must be an object with non-empty 'analyzer'",
            prov,
        )
    if prov.get("analyzer") != _TRUSTED_SIGNAL_ANALYZER:
        raise ContractValidationError(
            "data_quality_provenance.analyzer",
            "Signal integrity evidence must be produced by the trusted bounded analyzer.",
            prov.get("analyzer"),
        )
    for field_name in ("analysis_policy_version", "source_sha256", "target_channel", "sample_count"):
        if field_name not in prov:
            raise ContractValidationError(
                f"data_quality_provenance.{field_name}",
                f"Trusted signal analysis provenance must bind '{field_name}'.",
                prov,
            )
    if prov["analysis_policy_version"] != _SIGNAL_ANALYSIS_POLICY_VERSION:
        raise ContractValidationError(
            "data_quality_provenance.analysis_policy_version",
            f"Unsupported signal analysis policy {prov['analysis_policy_version']!r}.",
            prov["analysis_policy_version"],
        )

    # 8. Full scale limit and rail metrics consistency
    fs = sig_evidence.get("full_scale_limit")
    fs_units = sig_evidence.get("full_scale_units")
    rail_cnt = sig_evidence.get("rail_sample_count")
    longest_run = sig_evidence.get("longest_contiguous_rail_run")
    clip_ratio = sig_evidence.get("clipping_ratio")

    if fs is not None:
        if isinstance(fs, bool) or not isinstance(fs, (int, float)) or not math.isfinite(fs) or fs <= 0:
            raise ContractValidationError(
                "full_scale_limit",
                f"Field 'full_scale_limit' must be a positive number when provided, got {fs!r}",
                fs,
            )
    if fs_units is not None and fs_units not in _ALLOWED_SIGNAL_UNITS:
        raise ContractValidationError(
            "full_scale_units",
            f"Field 'full_scale_units' must be one of {_ALLOWED_SIGNAL_UNITS}, got {fs_units!r}",
            fs_units,
        )

    if rail_cnt is not None:
        if isinstance(rail_cnt, bool) or not isinstance(rail_cnt, int) or rail_cnt < 0:
            raise ContractValidationError(
                "rail_sample_count",
                f"Field 'rail_sample_count' must be a non-negative integer when provided, got {rail_cnt!r}",
                rail_cnt,
            )
        if rail_cnt > cnt:
            raise ContractValidationError(
                "rail_sample_count",
                f"Field 'rail_sample_count' ({rail_cnt}) cannot exceed sample_count ({cnt})",
                rail_cnt,
            )

    if longest_run is not None:
        if isinstance(longest_run, bool) or not isinstance(longest_run, int) or longest_run < 0:
            raise ContractValidationError(
                "longest_contiguous_rail_run",
                f"Field 'longest_contiguous_rail_run' must be a non-negative integer when provided, got {longest_run!r}",
                longest_run,
            )
        if rail_cnt is not None and longest_run > rail_cnt:
            raise ContractValidationError(
                "longest_contiguous_rail_run",
                f"Field 'longest_contiguous_rail_run' ({longest_run}) cannot exceed rail_sample_count ({rail_cnt})",
                longest_run,
            )

    if clip_ratio is not None:
        if isinstance(clip_ratio, bool) or not isinstance(clip_ratio, (int, float)) or not math.isfinite(clip_ratio) or clip_ratio < 0.0 or clip_ratio > 1.0:
            raise ContractValidationError(
                "clipping_ratio",
                f"Field 'clipping_ratio' must be a float between 0.0 and 1.0, got {clip_ratio!r}",
                clip_ratio,
            )
        if rail_cnt is not None and cnt > 0:
            expected_ratio = rail_cnt / cnt
            if abs(clip_ratio - expected_ratio) > 1e-12:
                raise ContractValidationError(
                    "clipping_ratio",
                    f"Reported clipping_ratio {clip_ratio} is inconsistent with rail_sample_count / sample_count ({expected_ratio})",
                    clip_ratio,
                )
            expected_status = (
                "SUSTAINED_RAIL_SATURATION" if longest_run is not None and longest_run >= 3
                else "EXCESSIVE_CLIPPING" if clip_ratio > 0.0001
                else "PASS"
            )
            if nonfinite == 0 and v_status != expected_status:
                raise ContractValidationError(
                    "verification_status",
                    f"Reported verification_status {v_status!r} contradicts validated rail metrics; expected {expected_status!r}.",
                    v_status,
                )

    # 9. PASS status requirements
    if v_status == "PASS":
        if fs is None:
            raise ContractValidationError(
                "full_scale_limit",
                "Verification status 'PASS' requires verified positive 'full_scale_limit'",
                fs,
            )
        if rail_cnt is None or longest_run is None or clip_ratio is None:
            raise ContractValidationError(
                "rail_sample_count",
                "Verification status 'PASS' requires calculated rail and clipping metrics",
                sig_evidence,
            )
        if longest_run >= 3:
            raise ContractValidationError(
                "longest_contiguous_rail_run",
                f"Verification status cannot be 'PASS' when longest_contiguous_rail_run is {longest_run} (>= 3)",
                longest_run,
            )
        if clip_ratio > 0.0001:
            raise ContractValidationError(
                "clipping_ratio",
                f"Verification status cannot be 'PASS' when clipping_ratio is {clip_ratio} (> 0.0001)",
                clip_ratio,
            )
        if nonfinite > 0:
            raise ContractValidationError(
                "nonfinite_sample_count",
                f"Verification status cannot be 'PASS' when nonfinite_sample_count is {nonfinite} (> 0)",
                nonfinite,
            )

    # 10. Consistency checks against Stage 10 inspection report if passed directly
    if stage10_report is not None:
        s10_id = stage10_report.get("source_identity", {})
        expected_source_sha256 = expected_source_sha256 or s10_id.get("raw_byte_sha256")
        s10_facts = stage10_report.get("container_facts", {})
        expected_target_channel = expected_target_channel or s10_facts.get("target_variable")
        if expected_sample_count is None:
            expected_sample_count = s10_facts.get("sample_count")
        if expected_nonfinite_count is None:
            expected_nonfinite_count = s10_facts.get("nonfinite_sample_count")

    # 11. Enforce expected source facts
    if expected_source_sha256 and src_sha.lower() != expected_source_sha256.lower():
        raise ContractValidationError(
            "source_sha256",
            f"Signal integrity source_sha256 '{src_sha}' does not match expected source digest '{expected_source_sha256}'",
            src_sha,
        )

    if expected_target_channel and tgt_chan != expected_target_channel:
        raise ContractValidationError(
            "target_channel",
            f"Signal integrity target_channel '{tgt_chan}' does not match expected target channel '{expected_target_channel}'",
            tgt_chan,
        )

    if expected_sample_count is not None and cnt != expected_sample_count:
        raise ContractValidationError(
            "sample_count",
            f"Signal integrity sample_count ({cnt}) does not match expected sample count ({expected_sample_count})",
            cnt,
        )

    if expected_nonfinite_count is not None and nonfinite != expected_nonfinite_count:
        raise ContractValidationError(
            "nonfinite_sample_count",
            f"Signal integrity nonfinite_sample_count ({nonfinite}) does not match expected nonfinite count ({expected_nonfinite_count})",
            nonfinite,
        )

    if prov["source_sha256"] != src_sha or prov["target_channel"] != tgt_chan or prov["sample_count"] != cnt:
        raise ContractValidationError(
            "data_quality_provenance",
            "Trusted signal analysis provenance does not match validated signal identity.",
            prov,
        )

    return sig_evidence
