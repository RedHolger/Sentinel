"""Deterministic host reference implementation for fixed-size vibration windowing

and per-channel time-domain feature extraction.

Operates purely in-memory with exact, cancellation- and overflow-aware rational
accumulation via fractions.Fraction, strict parameter validation, explicit
trailing-sample accounting, and zero input payload mutation.
"""

from __future__ import annotations

import copy
from fractions import Fraction
import math
import sys
from typing import Any, Dict, List, Sequence, Tuple

from adi_common import ContractValidationError, validate_vibration_recording

_BINARY64_MAX_INT = int(sys.float_info.max)


class WindowFeatureError(ValueError):
    """Raised when window feature extraction fails parameter, domain, or segmentation validation."""
    pass


def _validate_sample_sequence(values: Any, helper_name: str) -> None:
    """Validate that input is a non-empty sequence of finite, non-boolean numeric samples within binary64 domain.

    Args:
        values: Sequence to validate.
        helper_name: Name of calling helper function for error messages.

    Raises:
        WindowFeatureError: If values is not a sequence, is empty, contains booleans,
            non-numerics, non-finite values (NaN/Inf), or values exceeding binary64 domain.
    """
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray, dict, set)):
        raise WindowFeatureError(
            f"{helper_name}: expected a sequence of numeric samples, got {type(values).__name__}"
        )
    if len(values) == 0:
        raise WindowFeatureError(
            f"{helper_name}: sample sequence must not be empty; empty data cannot represent measured features"
        )
    for i, x in enumerate(values):
        if isinstance(x, bool):
            raise WindowFeatureError(
                f"{helper_name}: sample at index {i} is a boolean ({x!r}); booleans are not valid numeric samples"
            )
        if isinstance(x, int):
            if abs(x) > _BINARY64_MAX_INT:
                raise WindowFeatureError(
                    f"{helper_name}: integer sample at index {i} exceeds finite IEEE 754 binary64 range"
                )
        elif isinstance(x, float):
            if not math.isfinite(x):
                raise WindowFeatureError(
                    f"{helper_name}: non-finite sample value {x} at index {i}; NaN and Infinity are forbidden"
                )
        else:
            raise WindowFeatureError(
                f"{helper_name}: sample at index {i} has invalid type {type(x).__name__}; expected int or float"
            )


def stable_mean(values: Sequence[float]) -> float:
    """Compute the arithmetic mean using exact rational arithmetic via fractions.Fraction.

    Avoids intermediate summation overflow and catastrophic cancellation by accumulating
    exact dyadic rational representations of IEEE 754 binary64 floats with arbitrary precision.

    Args:
        values: Non-empty sequence of finite, non-boolean numeric samples.

    Returns:
        Exact arithmetic mean rounded to nearest IEEE 754 binary64 float.

    Raises:
        WindowFeatureError: If input sequence is empty, invalid, or computed mean is not finite.
    """
    _validate_sample_sequence(values, "stable_mean")
    total = sum(Fraction.from_float(float(x)) for x in values)
    mean_val = float(total / len(values))
    if not math.isfinite(mean_val):
        raise WindowFeatureError("stable_mean: computed mean exceeds finite IEEE 754 binary64 domain")
    return mean_val


def stable_rms(values: Sequence[float]) -> float:
    """Compute Root Mean Square (RMS) using scaled Euclidean norm (Blue's algorithm).

    Scales by maximum absolute value to avoid squaring overflow for values where
    naive x**2 would exceed sys.float_info.max (e.g. 1e200), and avoids underflow
    for small values (e.g. 1e-200).

    Args:
        values: Non-empty sequence of finite, non-boolean numeric samples.

    Returns:
        RMS value as a finite IEEE 754 binary64 float.

    Raises:
        WindowFeatureError: If input sequence is empty, invalid, or computed RMS is not finite.
    """
    _validate_sample_sequence(values, "stable_rms")
    scale = max(abs(float(x)) for x in values)
    if scale == 0.0:
        return 0.0
    # Scaled sum of squares: (x / scale) is in [-1, 1], so (x / scale)**2 is in [0, 1]
    sum_sq_scaled = sum((float(x) / scale) ** 2 for x in values)
    rms_val = scale * math.sqrt(sum_sq_scaled / len(values))
    if not math.isfinite(rms_val):
        raise WindowFeatureError("stable_rms: computed RMS exceeds finite IEEE 754 binary64 domain")
    return rms_val


def stable_peak_abs(values: Sequence[float]) -> float:
    """Compute the peak absolute amplitude: max(|x|).

    Args:
        values: Non-empty sequence of finite, non-boolean numeric samples.

    Returns:
        Peak absolute amplitude as a finite IEEE 754 binary64 float.

    Raises:
        WindowFeatureError: If input sequence is empty, invalid, or computed peak is not finite.
    """
    _validate_sample_sequence(values, "stable_peak_abs")
    peak_val = max(abs(float(x)) for x in values)
    if not math.isfinite(peak_val):
        raise WindowFeatureError("stable_peak_abs: computed peak exceeds finite IEEE 754 binary64 domain")
    return peak_val


def compute_channel_metrics(samples: Sequence[float]) -> Dict[str, float]:
    """Compute mean, RMS, and peak absolute amplitude for a single 1D channel sequence.

    Args:
        samples: Non-empty 1D sequence of finite, non-boolean numeric acceleration samples.

    Returns:
        Dictionary with keys 'mean', 'rms', 'peak_abs', each guaranteed to be a finite float.

    Raises:
        WindowFeatureError: If samples sequence is empty, contains booleans/non-finite values,
            or if any computed metric is non-finite.
    """
    _validate_sample_sequence(samples, "compute_channel_metrics")
    mean_val = stable_mean(samples)
    rms_val = stable_rms(samples)
    peak_val = stable_peak_abs(samples)

    for k, v in [("mean", mean_val), ("rms", rms_val), ("peak_abs", peak_val)]:
        if not math.isfinite(v):
            raise WindowFeatureError(
                f"compute_channel_metrics: computed feature '{k}' is non-finite ({v})"
            )

    return {
        "mean": mean_val,
        "rms": rms_val,
        "peak_abs": peak_val,
    }


def extract_window_features(
    recording: Dict[str, Any], window_size: int, hop_size: int
) -> Dict[str, Any]:
    """Extract fixed-size windows and per-channel time-domain features from a vibration recording.

    Accepts a vibration recording matching V1 contract invariants.
    Segments the samples matrix [sample, channel] into complete windows of length window_size
    with hop step hop_size. Complete windows start at 0, hop_size, 2*hop_size, ... and are
    emitted only when start + window_size <= total_samples.

    Args:
        recording: Valid vibration recording dictionary.
        window_size: Length of each complete window in samples (positive integer).
        hop_size: Stride between window starts in samples (positive integer <= window_size).

    Returns:
        Versioned result dictionary containing window metadata, per-channel features,
        and trailing uncovered sample accounting. Every emitted metric is guaranteed finite.

    Raises:
        ContractValidationError: If recording fails V1 vibration recording contract.
        WindowFeatureError: If window_size or hop_size are invalid, or if any computed
            window metric is non-finite.
    """
    # 1. Validate recording contract (deterministic, fails closed, immutable)
    validate_vibration_recording(recording)

    # 2. Validate window_size and hop_size parameters
    if isinstance(window_size, bool) or not isinstance(window_size, int) or window_size <= 0:
        raise WindowFeatureError(
            f"window_size must be a positive integer, got {window_size!r}"
        )
    if isinstance(hop_size, bool) or not isinstance(hop_size, int) or hop_size <= 0:
        raise WindowFeatureError(
            f"hop_size must be a positive integer, got {hop_size!r}"
        )
    if hop_size > window_size:
        raise WindowFeatureError(
            f"hop_size ({hop_size}) cannot exceed window_size ({window_size})"
        )

    samples = recording["samples"]
    total_samples = len(samples)
    channels = recording["channels"]

    # 3. Check for insufficient length
    if total_samples < window_size:
        return {
            "schema_version": "1.0.0",
            "status": "INSUFFICIENT_LENGTH",
            "recording_id": recording["recording_id"],
            "provenance": recording["provenance"],
            "sample_rate_hz": recording["sample_rate_hz"],
            "acceleration_unit": recording["acceleration_unit"],
            "channels": list(channels),
            "window_size": window_size,
            "hop_size": hop_size,
            "window_count": 0,
            "trailing_samples": total_samples,
            "execution_environment": "HOST_CPU",
            "windows": [],
        }

    # 4. Extract complete windows
    windows_data = []
    start = 0
    window_idx = 0

    while start + window_size <= total_samples:
        end = start + window_size
        window_slice = samples[start:end]

        # Compute per-channel features independently
        channel_features: Dict[str, Dict[str, float]] = {}
        for c_idx, ch_name in enumerate(channels):
            ch_samples = [row[c_idx] for row in window_slice]
            metrics = compute_channel_metrics(ch_samples)
            # Ensure no non-finite metrics leak into emitted payload
            for m_key, m_val in metrics.items():
                if not math.isfinite(m_val):
                    raise WindowFeatureError(
                        f"Non-finite metric '{m_key}' ({m_val}) computed for channel '{ch_name}' in window {window_idx}"
                    )
            channel_features[ch_name] = metrics

        windows_data.append(
            {
                "window_index": window_idx,
                "start_sample": start,
                "end_sample": end,
                "features": channel_features,
            }
        )
        window_idx += 1
        start += hop_size

    final_end = windows_data[-1]["end_sample"]
    trailing_samples = total_samples - final_end

    return {
        "schema_version": "1.0.0",
        "status": "SUCCESS",
        "recording_id": recording["recording_id"],
        "provenance": recording["provenance"],
        "sample_rate_hz": recording["sample_rate_hz"],
        "acceleration_unit": recording["acceleration_unit"],
        "channels": list(channels),
        "window_size": window_size,
        "hop_size": hop_size,
        "window_count": len(windows_data),
        "trailing_samples": trailing_samples,
        "execution_environment": "HOST_CPU",
        "windows": windows_data,
    }
