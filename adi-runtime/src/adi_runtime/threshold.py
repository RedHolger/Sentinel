"""Deterministic host RMS-threshold anomaly detector reference with abstention.

Connects validated vibration recordings (VibrationRecordingV1) and window features
to standard anomaly decisions (AnomalyDecisionV1) with explicit two-level abstention.
Provides uncalibrated reference behavior only; no learned models, threshold fitting,
or whole-machine health aggregation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from typing import Any, Dict, List, Sequence

from adi_common import ContractValidationError, validate_anomaly_decision, validate_vibration_recording
from adi_runtime.features import stable_rms

_BINARY64_MAX_INT = int(sys.float_info.max)

DETECTOR_ID = "threshold_reference"
DETECTOR_VERSION = "1.0.0"
EVALUATION_SCOPE = "uncalibrated_engineering_reference"

REASON_SAMPLE_RATE_MISMATCH = "SAMPLE_RATE_MISMATCH"
REASON_UNIT_MISMATCH = "UNIT_MISMATCH"
REASON_CHANNELS_MISMATCH = "CHANNELS_MISMATCH"
REASON_INSUFFICIENT_LENGTH = "INSUFFICIENT_RECORDING_LENGTH"
REASON_SENSOR_SATURATION_POSSIBLE = "SENSOR_SATURATION_POSSIBLE"

_CANONICAL_CONFIG_KEYS = {
    "config_id",
    "config_version",
    "window_size",
    "hop_size",
    "expected_sample_rate_hz",
    "expected_acceleration_unit",
    "expected_channels",
    "full_scale",
    "threshold",
}

_ALLOWED_UNITS = ("g", "m/s^2")
_ALLOWED_CHANNELS = ("x", "y", "z")


class ThresholdReferenceError(ValueError):
    """Base exception for threshold reference errors."""
    pass


class ConfigurationError(ThresholdReferenceError):
    """Raised when detector configuration fails schema, range, or domain validation."""
    pass


def _canonicalize_numeric(val: Any) -> int | float:
    """Losslessly preserve numeric value and representation type for canonical serialization.

    Preserves exact Python integers (including arbitrary-precision integers within binary64 domain)
    and finite IEEE 754 binary64 floats without precision loss or truncation.
    Booleans and non-finite values are strictly forbidden.
    """
    if isinstance(val, bool):
        raise TypeError(f"Booleans are forbidden in canonical numeric serialization, got {val!r}")
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if not math.isfinite(val):
            raise ValueError(f"Non-finite float value {val!r} forbidden in canonical serialization")
        return val
    raise TypeError(f"Expected int or float, got {type(val).__name__}")


def _validate_numeric_scalar(
    val: Any,
    field_name: str,
    *,
    must_be_positive: bool = False,
    allow_zero: bool = True,
) -> int | float:
    """Validate that a numeric value is non-boolean, finite, and within binary64 range.

    Preserves exact integer types without floating-point conversion, preventing
    information loss for integers exceeding 2**53.
    """
    if isinstance(val, bool):
        raise ConfigurationError(
            f"Field '{field_name}' must be numeric, got boolean ({val!r})"
        )
    if not isinstance(val, (int, float)):
        raise ConfigurationError(
            f"Field '{field_name}' must be numeric, got {type(val).__name__}"
        )
    if isinstance(val, int) and abs(val) > _BINARY64_MAX_INT:
        raise ConfigurationError(
            f"Field '{field_name}' integer value exceeds finite IEEE 754 binary64 range"
        )
    if isinstance(val, float) and not math.isfinite(val):
        raise ConfigurationError(
            f"Field '{field_name}' must be finite, got {val!r}"
        )

    if must_be_positive and val <= 0:
        raise ConfigurationError(
            f"Field '{field_name}' must be strictly positive (> 0), got {val!r}"
        )
    if not allow_zero and val < 0:
        raise ConfigurationError(
            f"Field '{field_name}' must be non-negative (>= 0), got {val!r}"
        )
    return val


def validate_threshold_configuration(config: Any) -> Dict[str, Any]:
    """Validate an RMS threshold detector configuration against canonical schema.

    Strictly accepts only documented canonical keys. Unrequested aliases are rejected
    as unknown fields to prevent alias-bypass vulnerabilities. Booleans are rejected
    for all numeric parameters.

    Args:
        config: Dictionary containing required detector parameters.

    Returns:
        Validated configuration dictionary with canonical keys.

    Raises:
        ConfigurationError: On missing keys, unknown keys, invalid types, booleans,
            non-finite numbers, oversized integers, or invalid domain constraints.
    """
    if not isinstance(config, dict):
        raise ConfigurationError(
            f"Configuration must be a dictionary, got {type(config).__name__}"
        )

    # Check for unknown keys (strictly canonical keys only; no unrequested aliases)
    unknown_keys = set(config.keys()) - _CANONICAL_CONFIG_KEYS
    if unknown_keys:
        raise ConfigurationError(
            f"Disallowed unknown field(s) in configuration: {sorted(unknown_keys)}"
        )

    # Check for missing required canonical keys
    missing_keys = _CANONICAL_CONFIG_KEYS - set(config.keys())
    if missing_keys:
        raise ConfigurationError(
            f"Missing required configuration field(s): {sorted(missing_keys)}"
        )

    # 1. config_id
    config_id = config["config_id"]
    if not isinstance(config_id, str) or len(config_id.strip()) == 0:
        raise ConfigurationError(
            f"Field 'config_id' must be a non-empty string, got {config_id!r}"
        )

    # 2. config_version
    config_version = config["config_version"]
    if not isinstance(config_version, str) or len(config_version.strip()) == 0:
        raise ConfigurationError(
            f"Field 'config_version' must be a non-empty string, got {config_version!r}"
        )

    # 3. window_size
    window_size = config["window_size"]
    if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size <= 0:
        raise ConfigurationError(
            f"Field 'window_size' must be a positive integer, got {window_size!r}"
        )

    # 4. hop_size
    hop_size = config["hop_size"]
    if isinstance(hop_size, bool) or not isinstance(hop_size, int) or hop_size <= 0:
        raise ConfigurationError(
            f"Field 'hop_size' must be a positive integer, got {hop_size!r}"
        )
    if hop_size > window_size:
        raise ConfigurationError(
            f"Field 'hop_size' ({hop_size}) cannot be greater than 'window_size' ({window_size})"
        )

    # 5. expected_sample_rate_hz
    sample_rate = _validate_numeric_scalar(
        config["expected_sample_rate_hz"], "expected_sample_rate_hz", must_be_positive=True
    )

    # 6. expected_acceleration_unit
    unit = config["expected_acceleration_unit"]
    if unit not in _ALLOWED_UNITS:
        raise ConfigurationError(
            f"Field 'expected_acceleration_unit' must be one of {_ALLOWED_UNITS}, got {unit!r}"
        )

    # 7. expected_channels
    channels = config["expected_channels"]
    if not isinstance(channels, list) or len(channels) == 0:
        raise ConfigurationError(
            f"Field 'expected_channels' must be a non-empty list of channel strings, got {channels!r}"
        )
    if len(channels) > 3:
        raise ConfigurationError(
            f"Field 'expected_channels' cannot have more than 3 channels, got {len(channels)}"
        )
    for ch in channels:
        if ch not in _ALLOWED_CHANNELS:
            raise ConfigurationError(
                f"Invalid channel {ch!r} in 'expected_channels'. Allowed: {_ALLOWED_CHANNELS}"
            )
    if len(channels) != len(set(channels)):
        raise ConfigurationError(
            f"Duplicate channel names in 'expected_channels': {channels}"
        )

    # 8. full_scale
    full_scale = _validate_numeric_scalar(
        config["full_scale"], "full_scale", must_be_positive=True
    )

    # 9. threshold
    threshold = _validate_numeric_scalar(
        config["threshold"], "threshold", allow_zero=True, must_be_positive=False
    )
    if threshold < 0:
        raise ConfigurationError(
            f"Field 'threshold' must be non-negative (>= 0), got {threshold}"
        )

    return {
        "config_id": config_id,
        "config_version": config_version,
        "window_size": window_size,
        "hop_size": hop_size,
        "expected_sample_rate_hz": sample_rate,
        "expected_acceleration_unit": unit,
        "expected_channels": list(channels),
        "full_scale": full_scale,
        "threshold": threshold,
    }


def compute_canonical_recording_fingerprint(recording: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 fingerprint over canonical recording metadata and samples.

    Evaluated content and metadata are serialized to canonical UTF-8 JSON with sorted keys,
    compact separators, and allow_nan=False. Validated integer values are preserved losslessly
    without converting to float, preventing information collapse for integers above 2**53.

    Args:
        recording: Validated VibrationRecordingV1 dictionary.

    Returns:
        64-character lowercase hexadecimal SHA-256 digest string.
    """
    canonical_rec = {
        "schema_version": recording["schema_version"],
        "recording_id": recording["recording_id"],
        "machine_id": recording["machine_id"],
        "session_id": recording["session_id"],
        "source_id": recording["source_id"],
        "provenance": recording["provenance"],
        "sample_rate_hz": _canonicalize_numeric(recording["sample_rate_hz"]),
        "acceleration_unit": recording["acceleration_unit"],
        "channels": list(recording["channels"]),
        "samples": [[_canonicalize_numeric(v) for v in row] for row in recording["samples"]],
        "source_sha256": recording["source_sha256"],
    }
    rec_json_bytes = json.dumps(
        canonical_rec,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(rec_json_bytes).hexdigest()


def compute_canonical_configuration_fingerprint(cfg: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 fingerprint over canonical configuration parameters.

    Args:
        cfg: Validated configuration dictionary.

    Returns:
        64-character lowercase hexadecimal SHA-256 digest string.
    """
    canonical_cfg = {
        "config_id": cfg["config_id"],
        "config_version": cfg["config_version"],
        "window_size": int(cfg["window_size"]),
        "hop_size": int(cfg["hop_size"]),
        "expected_sample_rate_hz": _canonicalize_numeric(cfg["expected_sample_rate_hz"]),
        "expected_acceleration_unit": cfg["expected_acceleration_unit"],
        "expected_channels": list(cfg["expected_channels"]),
        "full_scale": _canonicalize_numeric(cfg["full_scale"]),
        "threshold": _canonicalize_numeric(cfg["threshold"]),
    }
    cfg_json_bytes = json.dumps(
        canonical_cfg,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(cfg_json_bytes).hexdigest()


def derive_decision_id(
    scope: str,
    rec_fingerprint: str,
    cfg_fingerprint: str,
    *,
    window_idx: int | None = None,
    start_sample: int | None = None,
    end_sample: int | None = None,
) -> str:
    """Derive a versioned, cryptographically collision-resistant deterministic decision ID.

    Combines canonical recording fingerprint, configuration fingerprint, detector identity,
    decision scope, and window boundaries into a canonical structured JSON object and hashes it.
    Changing the threshold, configuration version, sample content, or source digest strictly
    produces a distinct decision ID.

    The 32-hex character truncation retains 128 bits of SHA-256 collision resistance,
    balancing concise identifier length with cryptographic resistance against collisions.

    Args:
        scope: "window" or "recording_envelope".
        rec_fingerprint: 64-hex recording fingerprint.
        cfg_fingerprint: 64-hex configuration fingerprint.
        window_idx: Window index (required when scope == "window").
        start_sample: Window start sample offset (required when scope == "window").
        end_sample: Window end sample offset (required when scope == "window").

    Returns:
        Unique deterministic decision ID string adhering to contract invariants.
    """
    payload: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "scope": scope,
        "detector_id": DETECTOR_ID,
        "detector_version": DETECTOR_VERSION,
        "recording_fingerprint": rec_fingerprint,
        "configuration_fingerprint": cfg_fingerprint,
    }
    if scope == "window":
        payload["window_index"] = window_idx
        payload["start_sample"] = start_sample
        payload["end_sample"] = end_sample
        payload_bytes = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
        id_hash = hashlib.sha256(payload_bytes).hexdigest()[:32]
        return f"dec_v1_w{window_idx}_{start_sample}_{end_sample}_{id_hash}"
    elif scope == "recording_envelope":
        payload_bytes = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
        id_hash = hashlib.sha256(payload_bytes).hexdigest()[:32]
        return f"dec_v1_envelope_{id_hash}"
    else:
        raise ValueError(f"Unknown decision scope: {scope!r}")


def evaluate_rms_threshold(
    recording: Dict[str, Any],
    configuration: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluate a vibration recording against an RMS threshold configuration.

    Connects validated vibration recordings to AnomalyDecisionV1 outcomes with
    two-level abstention:
    1. Recording-level incompatibility (sample rate, unit, channels, or length)
       produces a single recording-level INSUFFICIENT_EVIDENCE decision without
       inventing window boundaries.
    2. Window-level possible saturation (|sample| >= full_scale) produces
       INSUFFICIENT_EVIDENCE for that window (quality_status: INDETERMINATE).
    3. Normal evaluation calculates max-RMS across channels. If max-RMS >= threshold,
       outcome is ANOMALY; otherwise NORMAL (quality_status: PASS).

    Decision IDs are bound to canonical fingerprints of the evaluated recording
    content/metadata, configuration, detector version, and window coordinates.

    Args:
        recording: Valid VibrationRecordingV1 dictionary.
        configuration: Detector configuration dictionary.

    Returns:
        Versioned result envelope containing recording provenance, configuration,
        window boundaries, decisions, trailing sample accounting, and scope notice.

    Raises:
        ContractValidationError: If recording violates VibrationRecordingV1 contract.
        ConfigurationError: If configuration violates schema or domain invariants.
    """
    # 1. Validate inputs (deterministic, fail closed, zero mutation)
    validate_vibration_recording(recording)
    cfg = validate_threshold_configuration(configuration)

    rec_id = recording["recording_id"]
    source_sha256 = recording["source_sha256"]
    provenance = recording["provenance"]
    config_id = cfg["config_id"]
    window_size = cfg["window_size"]
    hop_size = cfg["hop_size"]
    full_scale = cfg["full_scale"]
    threshold = cfg["threshold"]
    samples = recording["samples"]
    total_samples = len(samples)

    # Compute reusable fingerprints once per evaluation
    rec_fingerprint = compute_canonical_recording_fingerprint(recording)
    cfg_fingerprint = compute_canonical_configuration_fingerprint(cfg)

    # 2. Check recording-level operational envelope compatibility
    envelope_reasons: List[str] = []

    # Sample rate equality semantics: exact binary64 equality
    if recording["sample_rate_hz"] != cfg["expected_sample_rate_hz"]:
        envelope_reasons.append(REASON_SAMPLE_RATE_MISMATCH)

    # Acceleration unit equality semantics: exact string match ('g' or 'm/s^2')
    if recording["acceleration_unit"] != cfg["expected_acceleration_unit"]:
        envelope_reasons.append(REASON_UNIT_MISMATCH)

    # Channel list semantics: exact set and ordering match
    if recording["channels"] != cfg["expected_channels"]:
        envelope_reasons.append(REASON_CHANNELS_MISMATCH)

    # Minimum length semantics: at least one complete window required
    if total_samples < window_size:
        envelope_reasons.append(REASON_INSUFFICIENT_LENGTH)

    if envelope_reasons:
        # Produce recording-level abstention decision; do NOT invent window boundaries
        decision_id = derive_decision_id(
            "recording_envelope", rec_fingerprint, cfg_fingerprint
        )
        rec_decision = {
            "schema_version": "1.0.0",
            "decision_id": decision_id,
            "recording_ref": rec_id,
            "detector_id": DETECTOR_ID,
            "detector_version": DETECTOR_VERSION,
            "outcome": "INSUFFICIENT_EVIDENCE",
            "execution_environment": "HOST_CPU",
            "quality_status": "FAIL",
            "reason_codes": envelope_reasons,
            "score": None,
            "threshold": None,
        }
        validate_anomaly_decision(rec_decision)

        return {
            "schema_version": "1.0.0",
            "status": "INSUFFICIENT_EVIDENCE",
            "recording_id": rec_id,
            "source_sha256": source_sha256,
            "provenance": provenance,
            "configuration_id": config_id,
            "configuration": copy.deepcopy(cfg),
            "detector_id": DETECTOR_ID,
            "detector_version": DETECTOR_VERSION,
            "evaluation_scope": EVALUATION_SCOPE,
            "window_boundaries": [],
            "decisions": [rec_decision],
            "trailing_samples": total_samples,
            "execution_environment": "HOST_CPU",
        }

    # 3. Extract complete windows and evaluate each window
    window_boundaries: List[Dict[str, int]] = []
    decisions: List[Dict[str, Any]] = []
    start = 0
    window_idx = 0

    while start + window_size <= total_samples:
        end = start + window_size
        window_slice = samples[start:end]
        window_boundaries.append(
            {
                "window_index": window_idx,
                "start_sample": start,
                "end_sample": end,
            }
        )
        decision_id = derive_decision_id(
            "window",
            rec_fingerprint,
            cfg_fingerprint,
            window_idx=window_idx,
            start_sample=start,
            end_sample=end,
        )

        # Inspect all channel samples in window for potential sensor saturation
        is_saturated = False
        for row in window_slice:
            for val in row:
                if abs(val) >= full_scale:
                    is_saturated = True
                    break
            if is_saturated:
                break

        if is_saturated:
            # Saturation flags possible sensor saturation, not proven clipping
            dec = {
                "schema_version": "1.0.0",
                "decision_id": decision_id,
                "recording_ref": rec_id,
                "detector_id": DETECTOR_ID,
                "detector_version": DETECTOR_VERSION,
                "outcome": "INSUFFICIENT_EVIDENCE",
                "execution_environment": "HOST_CPU",
                "quality_status": "INDETERMINATE",
                "reason_codes": [REASON_SENSOR_SATURATION_POSSIBLE],
                "score": None,
                "threshold": None,
            }
        else:
            # Compute per-channel RMS using Blue's stable scaled Euclidean norm
            channel_rms_list: List[float] = []
            for c_idx in range(len(recording["channels"])):
                ch_samples = [row[c_idx] for row in window_slice]
                ch_rms = stable_rms(ch_samples)
                channel_rms_list.append(ch_rms)

            # Score is strictly maximum per-channel RMS in this window
            score = max(channel_rms_list)
            if score >= threshold:
                outcome = "ANOMALY"
            else:
                outcome = "NORMAL"

            dec = {
                "schema_version": "1.0.0",
                "decision_id": decision_id,
                "recording_ref": rec_id,
                "detector_id": DETECTOR_ID,
                "detector_version": DETECTOR_VERSION,
                "outcome": outcome,
                "execution_environment": "HOST_CPU",
                "quality_status": "PASS",
                "reason_codes": [],
                "score": float(score),
                "threshold": _canonicalize_numeric(threshold),
            }

        validate_anomaly_decision(dec)
        decisions.append(dec)

        start += hop_size
        window_idx += 1

    final_end = window_boundaries[-1]["end_sample"] if window_boundaries else 0
    trailing_samples = total_samples - final_end

    return {
        "schema_version": "1.0.0",
        "status": "COMPLETED",
        "recording_id": rec_id,
        "source_sha256": source_sha256,
        "provenance": provenance,
        "configuration_id": config_id,
        "configuration": copy.deepcopy(cfg),
        "detector_id": DETECTOR_ID,
        "detector_version": DETECTOR_VERSION,
        "evaluation_scope": EVALUATION_SCOPE,
        "window_boundaries": window_boundaries,
        "decisions": decisions,
        "trailing_samples": trailing_samples,
        "execution_environment": "HOST_CPU",
    }
