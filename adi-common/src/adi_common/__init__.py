"""ADI Common - canonical contracts and validators."""

from adi_common.admission import (
    AdmissionError,
    EvidenceRegistry,
    OperationalAdmissionDisabledError,
    PRECEDENCE_GATES,
    VALID_ADMISSION_STATUSES,
    VALID_INTENDED_USES,
    analyze_signal_integrity,
    evaluate_measurement_admission,
)
from adi_common.inspection import validate_exploratory_source_inspection
from adi_common.validators import (
    ContractValidationError,
    load_schema,
    validate_anomaly_decision,
    validate_calibration_measurement_evidence,
    validate_measurement_admission_decision,
    validate_signal_integrity_evidence,
    validate_vibration_recording,
)

__version__ = "0.1.0"

__all__ = [
    "AdmissionError",
    "ContractValidationError",
    "EvidenceRegistry",
    "OperationalAdmissionDisabledError",
    "PRECEDENCE_GATES",
    "VALID_ADMISSION_STATUSES",
    "VALID_INTENDED_USES",
    "analyze_signal_integrity",
    "evaluate_measurement_admission",
    "load_schema",
    "validate_anomaly_decision",
    "validate_calibration_measurement_evidence",
    "validate_exploratory_source_inspection",
    "validate_measurement_admission_decision",
    "validate_signal_integrity_evidence",
    "validate_vibration_recording",
    "__version__",
]
