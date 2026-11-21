"""ADI Runtime - execution runtime and signal processing."""

from adi_runtime.features import (
    WindowFeatureError,
    compute_channel_metrics,
    extract_window_features,
    stable_mean,
    stable_peak_abs,
    stable_rms,
)
from adi_runtime.threshold import (
    DETECTOR_ID,
    DETECTOR_VERSION,
    EVALUATION_SCOPE,
    REASON_CHANNELS_MISMATCH,
    REASON_INSUFFICIENT_LENGTH,
    REASON_SAMPLE_RATE_MISMATCH,
    REASON_SENSOR_SATURATION_POSSIBLE,
    REASON_UNIT_MISMATCH,
    ConfigurationError,
    ThresholdReferenceError,
    compute_canonical_configuration_fingerprint,
    compute_canonical_recording_fingerprint,
    derive_decision_id,
    evaluate_rms_threshold,
    validate_threshold_configuration,
)

__version__ = "0.1.0"

__all__ = [
    "ConfigurationError",
    "DETECTOR_ID",
    "DETECTOR_VERSION",
    "EVALUATION_SCOPE",
    "REASON_CHANNELS_MISMATCH",
    "REASON_INSUFFICIENT_LENGTH",
    "REASON_SAMPLE_RATE_MISMATCH",
    "REASON_SENSOR_SATURATION_POSSIBLE",
    "REASON_UNIT_MISMATCH",
    "ThresholdReferenceError",
    "WindowFeatureError",
    "__version__",
    "compute_canonical_configuration_fingerprint",
    "compute_canonical_recording_fingerprint",
    "compute_channel_metrics",
    "derive_decision_id",
    "evaluate_rms_threshold",
    "extract_window_features",
    "stable_mean",
    "stable_peak_abs",
    "stable_rms",
    "validate_threshold_configuration",
]
