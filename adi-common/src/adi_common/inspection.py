"""Canonical validator and packaged schema loader for ExploratorySourceInspectionV1 contracts.

Enforces versioned contracts, structural invariants, and semantic constraints
for exploratory source inspection reports.
Validation is deterministic, side-effect free, and non-mutating.
"""

from __future__ import annotations

import datetime
import math
import re
import sys
from typing import Any, Dict, List, Optional, Set

import jsonschema

from adi_common.validators import ContractValidationError, load_schema

# Pre-loaded packaged schema
_EXPLORATORY_SCHEMA = load_schema("exploratory_source_inspection.schema.v1.json")

_SHA256_HEX_REGEX = re.compile(r"^[a-fA-F0-9]{64}\Z")
_INSPECTION_ID_REGEX = re.compile(r"^[a-z0-9_-]+\Z")

_FLOAT_INFO_MAX = sys.float_info.max
_BINARY64_MAX_INT = int(_FLOAT_INFO_MAX)

_TOP_LEVEL_KEYS: Set[str] = {
    "schema_version",
    "inspection_id",
    "inspected_at",
    "source_identity",
    "container_facts",
    "source_documentation",
    "metadata_status_ledger",
    "local_annotations",
    "inspection_findings",
}

_SOURCE_IDENTITY_KEYS: Set[str] = {
    "source_id",
    "source_locator",
    "source_filename",
    "raw_byte_sha256",
    "raw_byte_size",
}

_CONTAINER_FACTS_KEYS: Set[str] = {
    "container_format",
    "available_variables",
    "target_variable",
    "numeric_dtype",
    "numeric_shape",
    "sample_count",
    "nonfinite_sample_count",
    "observed_raw_extrema",
}

_SOURCE_DOC_KEYS: Set[str] = {
    "sensor_physical_location",
    "test_rig_description",
    "nominal_motor_load_hp",
    "nominal_motor_speed_rpm",
    "recording_condition_label",
}

_METADATA_LEDGER_KEYS: Set[str] = {
    "physical_acceleration_unit",
    "native_sampling_rate_hz",
    "acquisition_full_scale_limit",
    "physical_specimen_identity",
    "legal_usage_grant",
}

_METADATA_ENTRY_KEYS: Set[str] = {
    "status",
    "resolved_value",
    "evidence_reference",
    "evidence_gap_statement",
}

_EVIDENCE_REFERENCE_KEYS: Set[str] = {
    "source_document",
    "passage_or_table",
    "access_provenance",
}

_LOCAL_ANNOTATIONS_KEYS: Set[str] = {
    "precautionary_specimen_group",
    "candidate_partition_role",
    "provenance_classification",
}

_INSPECTION_FINDINGS_KEYS: Set[str] = {
    "structural_status",
    "measurement_readiness",
    "detector_admission_eligible",
    "diagnostic_notes",
}

_STATUS_READINESS_MAP: Dict[str, str] = {
    "STRUCTURAL_SUCCESS": "BLOCKED_UNKNOWN_METADATA",
    "NONFINITE_VALUES_DETECTED": "BLOCKED_NONFINITE_VALUES",
    "TARGET_VARIABLE_NOT_FOUND": "BLOCKED_TARGET_NOT_FOUND",
    "MALFORMED_CONTAINER": "BLOCKED_CORRUPTED_FILE",
    "CORRUPTED_RAW_BYTES": "BLOCKED_CORRUPTED_FILE",
    "UNSUPPORTED_STRUCTURE": "BLOCKED_UNSUPPORTED_FORMAT",
    "RESOURCE_LIMIT_EXCEEDED": "BLOCKED_RESOURCE_LIMIT",
}

_DIAGNOSTIC_OUTCOME_FIELDS: Set[str] = {
    "outcome",
    "quality_status",
    "score",
    "threshold",
    "window_features",
    "reason_codes",
}


def _check_numeric_binary64(val: Any, field_name: str) -> None:
    """Validate that a value is numeric and within the finite IEEE 754 binary64 domain."""
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
                f"Field '{field_name}' exceeds finite binary64 numeric domain "
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


def _safe_repr(val: Any) -> str:
    """Safely format a value for error messages without triggering integer decimal conversion limits."""
    if isinstance(val, int) and not isinstance(val, bool):
        if val > _BINARY64_MAX_INT or val < -_BINARY64_MAX_INT:
            return f"<integer exceeding binary64 range, bit_length={val.bit_length()}>"
    return repr(val)


def _validate_json_structure(val: Any, path: str = "") -> None:
    """Recursively validate JSON containers, keys, and values before schema and key operations.

    Ensures:
    - Allowed containers are strictly dict and list.
    - All dict keys are strings.
    - Allowed leaves are JSON scalar types (None, str, bool, int, float).
    - Float values are finite (no NaN, Infinity).
    - Integer values are within the finite binary64 domain [-sys.float_info.max, sys.float_info.max].
      Oversized integers are rejected without decimal string formatting.
    """
    if isinstance(val, dict):
        for k, v in val.items():
            if not isinstance(k, str):
                field_name = path if path else "object_key"
                loc = f" in '{path}'" if path else " at root"
                raise ContractValidationError(
                    field_name,
                    f"Object key{loc} must be a string, got key of type {type(k).__name__}",
                    k,
                )
            subpath = f"{path}.{k}" if path else k
            _validate_json_structure(v, subpath)
    elif isinstance(val, list):
        for idx, item in enumerate(val):
            subpath = f"{path}[{idx}]"
            _validate_json_structure(item, subpath)
    elif val is None or isinstance(val, str) or isinstance(val, bool):
        return
    elif isinstance(val, int):  # Note: bool is already handled above
        if val > _BINARY64_MAX_INT or val < -_BINARY64_MAX_INT:
            raise ContractValidationError(
                path if path else "integer_value",
                f"Integer value at '{path}' exceeds finite binary64 numeric domain "
                f"[-{_FLOAT_INFO_MAX}, {_FLOAT_INFO_MAX}]",
                val,
            )
    elif isinstance(val, float):
        if not math.isfinite(val):
            raise ContractValidationError(
                path if path else "numeric_value",
                f"Numeric value at '{path}' must be a finite number (no NaN or Infinity), got {val}",
                val,
            )
    else:
        raise ContractValidationError(
            path if path else "payload",
            f"Unsupported data type '{type(val).__name__}' at '{path}'. "
            f"Allowed types are JSON dict, list, string, number, boolean, and null.",
            val,
        )


def _check_json_values(obj: Any, path: str = "") -> None:
    """Ensure no NaN, Infinity, or out-of-range binary64 integers exist anywhere in payload."""
    _validate_json_structure(obj, path)


def _validate_iso8601_datetime(dt_str: Any) -> None:
    """Validate strict RFC 3339 / ISO 8601 syntax with timezone offset."""
    if not isinstance(dt_str, str):
        raise ContractValidationError("inspected_at", "inspected_at must be a string", dt_str)
    try:
        cleaned = dt_str.replace("Z", "+00:00") if dt_str.endswith("Z") else dt_str
        dt = datetime.datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            raise ContractValidationError(
                "inspected_at", "inspected_at must include timezone offset (e.g. Z or +00:00)", dt_str
            )
    except Exception as exc:
        raise ContractValidationError(
            "inspected_at", f"Invalid date-time syntax in inspected_at: {exc}", dt_str
        ) from exc


def validate_exploratory_source_inspection(report: Any) -> None:
    """Validate an ExploratorySourceInspectionV1 payload against schema and semantic invariants.

    Enforces:
    - schema_version == "1.0.0"
    - Valid inspection_id pattern and strict RFC 3339 inspected_at date-time syntax
    - Exact 64-hex SHA-256 with rejection of trailing newlines or whitespace
    - Strict JSON-compatible values (no NaN, Infinity, booleans in numeric fields)
    - Rejection of unknown object keys at all nested levels
    - detector_admission_eligible is unconditionally False
    - Rejection of any diagnostic outcome fields (e.g. outcome, score, quality_status)
    - Metadata status ledger invariants:
      * Unknown status requires null resolved_value, null evidence_reference, and non-empty gap statement
      * Documented known status requires correctly typed non-null value, structured evidence reference,
        and null gap statement
      * Rate and full-scale values must be positive, finite binary64 numbers (not booleans)
    - Status-dependent structural invariants:
      * STRUCTURAL_SUCCESS and NONFINITE_VALUES_DETECTED require MATLAB_V5, dtype float32/float64,
        2D positive-integer shape with at least one dimension equal to 1, unique available_variables,
        nonempty target_variable present in available_variables, sample_count == rows * cols <= 10,000,000,
        nonfinite_sample_count in [0, sample_count] (zero for SUCCESS, positive for NONFINITE),
        and finite extrema (min <= max) if finite_count > 0, or null extrema if finite_count == 0.
      * All other statuses require null dtype, shape, sample_count, nonfinite_sample_count, and extrema.
      * TARGET_VARIABLE_NOT_FOUND requires MATLAB_V5 and target_variable absent from available_variables.
      * MALFORMED_CONTAINER and CORRUPTED_RAW_BYTES require UNRECOGNIZED format and empty available_variables.
    - Exact status to measurement readiness mapping
    - Immutability of caller input (no in-place mutations)
    - Deterministic, side-effect free execution (no disk reads or network calls)

    Args:
        report: Dict representation of exploratory source inspection report.

    Returns:
        None on success.

    Raises:
        ContractValidationError: On any schema or semantic invariant violation.
    """
    if not isinstance(report, dict):
        raise ContractValidationError(
            None, f"Payload must be a dictionary, got {type(report).__name__}", report
        )

    # 1. Strict recursive JSON-type, container, object-key, and numeric bounds validation.
    # Must execute before any key sorting or schema operations to safely handle non-JSON in-memory inputs.
    _validate_json_structure(report)

    # 2. Reject diagnostic outcome fields
    for diag_field in _DIAGNOSTIC_OUTCOME_FIELDS:
        if diag_field in report:
            raise ContractValidationError(
                diag_field,
                f"Exploratory source inspection reports must not contain diagnostic outcome field '{diag_field}'",
                report[diag_field],
            )

    # 3. Top-level keys: unknown and missing (all keys are guaranteed to be strings)
    unknown_top = set(report.keys()) - _TOP_LEVEL_KEYS
    if unknown_top:
        raise ContractValidationError(
            "unknown_fields",
            f"Disallowed unknown field(s): {sorted(unknown_top)}. additionalProperties is false.",
            unknown_top,
        )

    missing_top = _TOP_LEVEL_KEYS - set(report.keys())
    if missing_top:
        raise ContractValidationError(
            "missing_fields",
            f"Missing required contract field(s): {sorted(missing_top)}",
            missing_top,
        )

    # 4. JSON Schema validation
    try:
        validator = jsonschema.Draft202012Validator(
            _EXPLORATORY_SCHEMA, format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER
        )
        errors = list(validator.iter_errors(report))
        if errors:
            first_err = errors[0]
            field = ".".join(str(p) for p in first_err.path) if first_err.path else None
            raise ContractValidationError(field, first_err.message, first_err.instance)
    except jsonschema.ValidationError as err:
        field = ".".join(str(p) for p in err.path) if err.path else None
        raise ContractValidationError(field, err.message, err.instance) from err

    # 5. Schema version
    version = report.get("schema_version")
    if version != "1.0.0":
        raise ContractValidationError(
            "schema_version", f"Unsupported schema version: {version!r}. Expected '1.0.0'.", version
        )

    # 6. Inspection ID and date-time validation
    insp_id = report.get("inspection_id")
    if not isinstance(insp_id, str) or not _INSPECTION_ID_REGEX.match(insp_id):
        raise ContractValidationError(
            "inspection_id", f"Field 'inspection_id' must match pattern '^[a-z0-9_-]+$', got {insp_id!r}", insp_id
        )

    _validate_iso8601_datetime(report.get("inspected_at"))

    # 7. Source identity validation
    ident = report.get("source_identity", {})
    unknown_ident = set(ident.keys()) - _SOURCE_IDENTITY_KEYS
    if unknown_ident:
        raise ContractValidationError(
            "source_identity.unknown_fields",
            f"Disallowed unknown field(s) in source_identity: {sorted(unknown_ident)}",
            unknown_ident,
        )

    sha = ident.get("raw_byte_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or not _SHA256_HEX_REGEX.match(sha) or "\n" in sha:
        raise ContractValidationError(
            "source_identity.raw_byte_sha256",
            f"Field 'raw_byte_sha256' must be exact 64 hex characters without newlines, got {sha!r}",
            sha,
        )

    raw_size = ident.get("raw_byte_size")
    if isinstance(raw_size, bool) or not isinstance(raw_size, int) or raw_size < 0:
        raise ContractValidationError(
            "source_identity.raw_byte_size",
            f"Field 'raw_byte_size' must be a non-negative integer, got {_safe_repr(raw_size)}",
            raw_size,
        )

    # 8. Local annotations validation
    ann = report.get("local_annotations", {})
    unknown_ann = set(ann.keys()) - _LOCAL_ANNOTATIONS_KEYS
    if unknown_ann:
        raise ContractValidationError(
            "local_annotations.unknown_fields",
            f"Disallowed unknown field(s) in local_annotations: {sorted(unknown_ann)}",
            unknown_ann,
        )

    prov = ann.get("provenance_classification")
    if prov != "recorded":
        raise ContractValidationError(
            "local_annotations.provenance_classification",
            f"Field 'provenance_classification' must be 'recorded', got {prov!r}",
            prov,
        )

    # 9. Inspection findings validation
    findings = report.get("inspection_findings", {})
    unknown_findings = set(findings.keys()) - _INSPECTION_FINDINGS_KEYS
    if unknown_findings:
        raise ContractValidationError(
            "inspection_findings.unknown_fields",
            f"Disallowed unknown field(s) in inspection_findings: {sorted(unknown_findings)}",
            unknown_findings,
        )

    det_eligible = findings.get("detector_admission_eligible")
    if det_eligible is not False:
        raise ContractValidationError(
            "inspection_findings.detector_admission_eligible",
            f"Field 'detector_admission_eligible' must be strictly False, got {det_eligible!r}",
            det_eligible,
        )

    status = findings.get("structural_status")
    if status not in _STATUS_READINESS_MAP:
        raise ContractValidationError(
            "inspection_findings.structural_status",
            f"Unsupported structural_status: {status!r}",
            status,
        )

    readiness = findings.get("measurement_readiness")
    expected_readiness = _STATUS_READINESS_MAP[status]
    if readiness != expected_readiness:
        raise ContractValidationError(
            "inspection_findings.measurement_readiness",
            f"Structural status '{status}' requires measurement_readiness '{expected_readiness}', got '{readiness}'",
            readiness,
        )

    # 10. Source documentation validation
    doc = report.get("source_documentation", {})
    unknown_doc = set(doc.keys()) - _SOURCE_DOC_KEYS
    if unknown_doc:
        raise ContractValidationError(
            "source_documentation.unknown_fields",
            f"Disallowed unknown field(s) in source_documentation: {sorted(unknown_doc)}",
            unknown_doc,
        )

    for doc_field in ("sensor_physical_location", "test_rig_description"):
        entry = doc.get(doc_field, {})
        if set(entry.keys()) != {"value", "evidence_reference"}:
            raise ContractValidationError(
                f"source_documentation.{doc_field}",
                f"Field '{doc_field}' must have exact keys ('value', 'evidence_reference')",
                entry,
            )

    for num_doc_field in ("nominal_motor_load_hp", "nominal_motor_speed_rpm"):
        entry = doc.get(num_doc_field, {})
        if set(entry.keys()) != {"value", "evidence_reference"}:
            raise ContractValidationError(
                f"source_documentation.{num_doc_field}",
                f"Field '{num_doc_field}' must have exact keys ('value', 'evidence_reference')",
                entry,
            )
        val = entry.get("value")
        if val is not None:
            _check_numeric_binary64(val, f"source_documentation.{num_doc_field}.value")

    cond_label = doc.get("recording_condition_label", {})
    if set(cond_label.keys()) != {"value", "evidence_reference", "scope"}:
        raise ContractValidationError(
            "source_documentation.recording_condition_label",
            "Field 'recording_condition_label' must have exact keys ('value', 'evidence_reference', 'scope')",
            cond_label,
        )
    if cond_label.get("scope") != "WHOLE_RECORDING_ONLY":
        raise ContractValidationError(
            "source_documentation.recording_condition_label.scope",
            f"Scope must be 'WHOLE_RECORDING_ONLY', got {cond_label.get('scope')!r}",
            cond_label.get("scope"),
        )

    # 11. Metadata status ledger validation
    ledger = report.get("metadata_status_ledger", {})
    unknown_ledger = set(ledger.keys()) - _METADATA_LEDGER_KEYS
    if unknown_ledger:
        raise ContractValidationError(
            "metadata_status_ledger.unknown_fields",
            f"Disallowed unknown field(s) in metadata_status_ledger: {sorted(unknown_ledger)}",
            unknown_ledger,
        )

    for field_name in _METADATA_LEDGER_KEYS:
        entry = ledger.get(field_name)
        if not isinstance(entry, dict):
            raise ContractValidationError(
                f"metadata_status_ledger.{field_name}",
                f"Metadata ledger entry '{field_name}' must be an object",
                entry,
            )
        unknown_entry = set(entry.keys()) - _METADATA_ENTRY_KEYS
        if unknown_entry:
            raise ContractValidationError(
                f"metadata_status_ledger.{field_name}.unknown_fields",
                f"Disallowed unknown field(s) in metadata entry '{field_name}': {sorted(unknown_entry)}",
                unknown_entry,
            )

        st = entry.get("status")
        val = entry.get("resolved_value")
        ref = entry.get("evidence_reference")
        gap = entry.get("evidence_gap_statement")

        if st in ("UNKNOWN_UNSTATED", "UNKNOWN_UNPROVEN"):
            if val is not None:
                raise ContractValidationError(
                    f"metadata_status_ledger.{field_name}.resolved_value",
                    f"resolved_value must be null when status is {st}, got {_safe_repr(val)}",
                    val,
                )
            if ref is not None:
                raise ContractValidationError(
                    f"metadata_status_ledger.{field_name}.evidence_reference",
                    f"evidence_reference must be null when status is {st}, got {ref!r}",
                    ref,
                )
            if not isinstance(gap, str) or len(gap.strip()) == 0:
                raise ContractValidationError(
                    f"metadata_status_ledger.{field_name}.evidence_gap_statement",
                    f"evidence_gap_statement must be a nonempty string when status is {st}",
                    gap,
                )
        elif st == "DOCUMENTED_KNOWN":
            if gap is not None:
                raise ContractValidationError(
                    f"metadata_status_ledger.{field_name}.evidence_gap_statement",
                    f"evidence_gap_statement must be null when status is DOCUMENTED_KNOWN, got {gap!r}",
                    gap,
                )
            if val is None:
                raise ContractValidationError(
                    f"metadata_status_ledger.{field_name}.resolved_value",
                    f"resolved_value must not be null when status is DOCUMENTED_KNOWN",
                    val,
                )

            # Field-specific typing and bounds
            if field_name == "physical_acceleration_unit":
                if val not in ("g", "m/s^2"):
                    raise ContractValidationError(
                        f"metadata_status_ledger.{field_name}.resolved_value",
                        f"Known physical unit must be 'g' or 'm/s^2', got {val!r}",
                        val,
                    )
            elif field_name in ("native_sampling_rate_hz", "acquisition_full_scale_limit"):
                _check_numeric_binary64(val, f"metadata_status_ledger.{field_name}.resolved_value")
                if val <= 0:
                    raise ContractValidationError(
                        f"metadata_status_ledger.{field_name}.resolved_value",
                        f"Field '{field_name}' must be positive, got {_safe_repr(val)}",
                        val,
                    )
            elif field_name in ("physical_specimen_identity", "legal_usage_grant"):
                if not isinstance(val, str) or len(val.strip()) == 0:
                    raise ContractValidationError(
                        f"metadata_status_ledger.{field_name}.resolved_value",
                        f"Field '{field_name}' must be a nonempty string, got {val!r}",
                        val,
                    )

            # Structured evidence reference validation
            if not isinstance(ref, dict):
                raise ContractValidationError(
                    f"metadata_status_ledger.{field_name}.evidence_reference",
                    "evidence_reference must be a structured object for DOCUMENTED_KNOWN status",
                    ref,
                )
            unknown_ref = set(ref.keys()) - _EVIDENCE_REFERENCE_KEYS
            if unknown_ref:
                raise ContractValidationError(
                    f"metadata_status_ledger.{field_name}.evidence_reference.unknown_fields",
                    f"Disallowed unknown field(s) in evidence_reference: {sorted(unknown_ref)}",
                    unknown_ref,
                )
            missing_ref = _EVIDENCE_REFERENCE_KEYS - set(ref.keys())
            if missing_ref:
                raise ContractValidationError(
                    f"metadata_status_ledger.{field_name}.evidence_reference.missing_fields",
                    f"Missing required field(s) in evidence_reference: {sorted(missing_ref)}",
                    missing_ref,
                )
            for ref_k in _EVIDENCE_REFERENCE_KEYS:
                ref_v = ref.get(ref_k)
                if not isinstance(ref_v, str) or len(ref_v.strip()) == 0:
                    raise ContractValidationError(
                        f"metadata_status_ledger.{field_name}.evidence_reference.{ref_k}",
                        f"Field '{ref_k}' in evidence_reference must be a nonempty string, got {ref_v!r}",
                        ref_v,
                    )
        else:
            raise ContractValidationError(
                f"metadata_status_ledger.{field_name}.status",
                f"Unsupported metadata status: {st!r}",
                st,
            )

    # 12. Container facts validation
    facts = report.get("container_facts", {})
    unknown_facts = set(facts.keys()) - _CONTAINER_FACTS_KEYS
    if unknown_facts:
        raise ContractValidationError(
            "container_facts.unknown_fields",
            f"Disallowed unknown field(s) in container_facts: {sorted(unknown_facts)}",
            unknown_facts,
        )

    fmt = facts.get("container_format")
    avail = facts.get("available_variables")
    if not isinstance(avail, list) or len(avail) != len(set(avail)):
        raise ContractValidationError(
            "container_facts.available_variables",
            "available_variables must be a list of unique variable strings",
            avail,
        )

    target = facts.get("target_variable")
    dtype = facts.get("numeric_dtype")
    shape = facts.get("numeric_shape")
    count = facts.get("sample_count")
    nonfinite = facts.get("nonfinite_sample_count")
    extrema = facts.get("observed_raw_extrema")

    if status in ("STRUCTURAL_SUCCESS", "NONFINITE_VALUES_DETECTED"):
        if fmt != "MATLAB_V5":
            raise ContractValidationError(
                "container_facts.container_format",
                f"Successful/nonfinite structural inspection requires container_format 'MATLAB_V5', got {fmt!r}",
                fmt,
            )

        if dtype not in ("float32", "float64"):
            raise ContractValidationError(
                "container_facts.numeric_dtype",
                f"numeric_dtype must be 'float32' or 'float64' for status {status}, got {dtype!r}",
                dtype,
            )

        if not isinstance(shape, (list, tuple)) or len(shape) != 2:
            raise ContractValidationError(
                "container_facts.numeric_shape",
                f"numeric_shape must be a 2-element list [rows, cols], got {shape!r}",
                shape,
            )

        rows, cols = shape
        if isinstance(rows, bool) or isinstance(cols, bool) or not isinstance(rows, int) or not isinstance(cols, int):
            raise ContractValidationError(
                "container_facts.numeric_shape",
                "Dimensions in numeric_shape must be non-boolean integers",
                shape,
            )
        if rows < 1 or cols < 1:
            raise ContractValidationError(
                "container_facts.numeric_shape",
                f"Dimensions in numeric_shape must be positive integers, got {_safe_repr(shape)}",
                shape,
            )
        if rows != 1 and cols != 1:
            raise ContractValidationError(
                "container_facts.numeric_shape",
                f"numeric_shape must be a 2D row/column vector with at least one dimension equal to 1, got {_safe_repr(shape)}",
                shape,
            )

        if not isinstance(target, str) or len(target.strip()) == 0:
            raise ContractValidationError(
                "container_facts.target_variable",
                f"target_variable must be a nonempty string for status {status}, got {target!r}",
                target,
            )
        if target not in avail:
            raise ContractValidationError(
                "container_facts.target_variable",
                f"target_variable '{target}' must be present in available_variables for status {status}",
                target,
            )

        expected_count = rows * cols
        if isinstance(count, bool) or not isinstance(count, int) or count != expected_count:
            raise ContractValidationError(
                "container_facts.sample_count",
                f"sample_count ({count}) must equal integer product of shape dimensions ({expected_count})",
                count,
            )
        if count > 10_000_000:
            raise ContractValidationError(
                "container_facts.sample_count",
                f"sample_count ({count}) exceeds maximum allowed limit (10,000,000)",
                count,
            )

        if isinstance(nonfinite, bool) or not isinstance(nonfinite, int) or nonfinite < 0 or nonfinite > count:
            raise ContractValidationError(
                "container_facts.nonfinite_sample_count",
                f"nonfinite_sample_count ({nonfinite}) must be an integer in range [0, sample_count ({count})]",
                nonfinite,
            )

        if status == "STRUCTURAL_SUCCESS" and nonfinite != 0:
            raise ContractValidationError(
                "container_facts.nonfinite_sample_count",
                f"STRUCTURAL_SUCCESS requires nonfinite_sample_count == 0, got {nonfinite}",
                nonfinite,
            )
        if status == "NONFINITE_VALUES_DETECTED" and nonfinite <= 0:
            raise ContractValidationError(
                "container_facts.nonfinite_sample_count",
                f"NONFINITE_VALUES_DETECTED requires positive nonfinite_sample_count, got {nonfinite}",
                nonfinite,
            )

        finite_count = count - nonfinite
        if finite_count > 0:
            if not isinstance(extrema, dict) or set(extrema.keys()) != {"min", "max"}:
                raise ContractValidationError(
                    "container_facts.observed_raw_extrema",
                    "observed_raw_extrema must be an object with exact keys ('min', 'max') when finite samples exist",
                    extrema,
                )
            mn = extrema.get("min")
            mx = extrema.get("max")
            _check_numeric_binary64(mn, "container_facts.observed_raw_extrema.min")
            _check_numeric_binary64(mx, "container_facts.observed_raw_extrema.max")
            if mn > mx:
                raise ContractValidationError(
                    "container_facts.observed_raw_extrema",
                    f"observed_raw_extrema min ({mn}) cannot exceed max ({mx})",
                    extrema,
                )
        else:  # finite_count == 0 (all samples are nonfinite)
            if extrema is not None:
                raise ContractValidationError(
                    "container_facts.observed_raw_extrema",
                    "observed_raw_extrema must be null when finite sample count is zero",
                    extrema,
                )

    else:
        # For all other statuses, container facts for target array must be null
        if dtype is not None:
            raise ContractValidationError(
                "container_facts.numeric_dtype",
                f"numeric_dtype must be null for status '{status}', got {dtype!r}",
                dtype,
            )
        if shape is not None:
            raise ContractValidationError(
                "container_facts.numeric_shape",
                f"numeric_shape must be null for status '{status}', got {shape!r}",
                shape,
            )
        if count is not None:
            raise ContractValidationError(
                "container_facts.sample_count",
                f"sample_count must be null for status '{status}', got {_safe_repr(count)}",
                count,
            )
        if nonfinite is not None:
            raise ContractValidationError(
                "container_facts.nonfinite_sample_count",
                f"nonfinite_sample_count must be null for status '{status}', got {_safe_repr(nonfinite)}",
                nonfinite,
            )
        if extrema is not None:
            raise ContractValidationError(
                "container_facts.observed_raw_extrema",
                f"observed_raw_extrema must be null for status '{status}', got {extrema!r}",
                extrema,
            )

        if status == "TARGET_VARIABLE_NOT_FOUND":
            if not isinstance(target, str) or len(target.strip()) == 0:
                raise ContractValidationError(
                    "container_facts.target_variable",
                    "TARGET_VARIABLE_NOT_FOUND requires a nonempty requested target_variable string",
                    target,
                )
            if fmt != "MATLAB_V5":
                raise ContractValidationError(
                    "container_facts.container_format",
                    f"TARGET_VARIABLE_NOT_FOUND requires container_format 'MATLAB_V5', got {fmt!r}",
                    fmt,
                )
            if target in avail:
                raise ContractValidationError(
                    "container_facts.target_variable",
                    f"TARGET_VARIABLE_NOT_FOUND requires target '{target}' to be absent from available_variables",
                    target,
                )
        elif status in ("MALFORMED_CONTAINER", "CORRUPTED_RAW_BYTES"):
            if fmt != "UNRECOGNIZED":
                raise ContractValidationError(
                    "container_facts.container_format",
                    f"Status '{status}' requires container_format 'UNRECOGNIZED', got {fmt!r}",
                    fmt,
                )
            if avail != []:
                raise ContractValidationError(
                    "container_facts.available_variables",
                    f"Status '{status}' requires empty available_variables list, got {avail!r}",
                    avail,
                )

    return None
