"""Deterministic evidence validation and measurement admission decision engine.

Enforces versioned contracts, evidence document authentication, deterministic
precedence resolution across the eight Stage 11 admission statuses, signal integrity
analysis, and decoupled diagnostic evaluation eligibility.

Safety boundary: All real-data admission and operational execution remain strictly
disabled in Stage 12. Only fictional test evaluation (FICTIONAL_TEST_ONLY) is supported.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import math
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from adi_common.inspection import validate_exploratory_source_inspection
from adi_common.validators import (
    ContractValidationError,
    validate_calibration_measurement_evidence,
    validate_measurement_admission_decision,
    validate_signal_integrity_evidence,
)

# The eight mutually exclusive measurement admission outcomes defined in Stage 11 (Section 6.1)
VALID_ADMISSION_STATUSES: Set[str] = {
    "ADMITTED_FOR_MEASUREMENT",
    "BLOCKED_UNSUPPORTED_FORMAT",
    "BLOCKED_SIGNAL_INTEGRITY",
    "BLOCKED_USAGE_RESTRICTIONS",
    "BLOCKED_PROVENANCE",
    "BLOCKED_UNKNOWN_METADATA",
    "BLOCKED_CONFLICTING_EVIDENCE",
    "BLOCKED_CALIBRATION",
}

# The eight deterministic precedence gates (Section 6.2)
PRECEDENCE_GATES: Sequence[str] = (
    "STRUCTURAL_FORMAT",
    "SIGNAL_INTEGRITY",
    "USAGE_PERMISSIONS",
    "SOURCE_PROVENANCE",
    "PHYSICAL_METADATA",
    "CONFLICTING_EVIDENCE",
    "CALIBRATION_VALIDITY",
    "ADMISSION",
)

# The five supported operational use contexts (Section 8.1)
VALID_INTENDED_USES: Set[str] = {
    "ACADEMIC_RESEARCH_NONCOMMERCIAL",
    "COMMERCIAL_R_AND_D",
    "INTERNAL_TESTING",
    "DATASET_REDISTRIBUTION",
    "ARTIFACT_PUBLICATION",
}


class AdmissionError(Exception):
    """Base exception for measurement admission engine failures."""
    pass


class OperationalAdmissionDisabledError(AdmissionError):
    """Raised when an operational real-data admission is attempted in Stage 12."""
    pass


class EvidenceRegistry:
    """Minimal offline evidence registry verifying authentic reference documentation.

    Separates curator-supplied evidence claims from independently authenticated documents.
    The default registry contains NO trusted external evidence, enforcing fail-closed
    security against fabricated or unauthenticated claims.
    """

    def __init__(self, trusted_documents: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._trusted_by_title: Dict[str, Dict[str, Any]] = {}
        self._trusted_by_uri: Dict[str, Dict[str, Any]] = {}
        if trusted_documents:
            for key, doc in trusted_documents.items():
                self.register_document(doc, key=key)

    def register_document(self, doc: Dict[str, Any], key: Optional[str] = None) -> None:
        """Register a verified reference document record.

        Args:
            doc: Dictionary containing document identity and verification facts.
            key: Optional lookup key. If omitted, uses document_title or locator_uri.
        """
        if not isinstance(doc, dict):
            raise TypeError(f"Document record must be a dict, got {type(doc).__name__}")
        title = doc.get("document_title")
        uri = doc.get("locator_uri")
        if not title or not isinstance(title, str) or not uri or not isinstance(uri, str):
            raise ValueError("Document record must have valid non-empty 'document_title' and 'locator_uri'")

        doc_sha = (doc.get("document_sha256") or doc.get("expected_sha256") or "").lower()

        # Duplicate/conflicting identifier check
        if title in self._trusted_by_title:
            existing = self._trusted_by_title[title]
            existing_sha = (existing.get("document_sha256") or existing.get("expected_sha256") or "").lower()
            if existing.get("locator_uri") != uri:
                raise ValueError(
                    f"Conflicting registry entry: document title '{title}' already registered with URI '{existing.get('locator_uri')}', cannot overwrite with '{uri}'"
                )
            if existing_sha and doc_sha and existing_sha != doc_sha:
                raise ValueError(
                    f"Conflicting registry entry: document title '{title}' already registered with digest '{existing_sha}', cannot overwrite with '{doc_sha}'"
                )

        if uri in self._trusted_by_uri:
            existing = self._trusted_by_uri[uri]
            if existing.get("document_title") != title:
                raise ValueError(
                    f"Conflicting registry entry: locator URI '{uri}' already registered with title '{existing.get('document_title')}', cannot overwrite with '{title}'"
                )

        doc_copy = copy.deepcopy(doc)
        self._trusted_by_title[title] = doc_copy
        self._trusted_by_uri[uri] = doc_copy

    def is_authenticated(
        self,
        reference: Any,
        required_scope: Optional[str] = None,
    ) -> bool:
        """Check whether an evidence reference is authenticated in this registry.

        Enforces multi-field cryptographic binding:
        - Exact match on document_title.
        - Exact match on locator_uri (forged URIs fail closed).
        - Verification status must be AUTHENTICATED, VERIFIED_PRIMARY, or VERIFIED_DERIVATION.
        - Required scope must be present in accepted_scopes.
        - Exact match on document_sha256 when registered or provided (incorrect digests fail closed).

        Args:
            reference: An evidence_reference_object dictionary.
            required_scope: Optional required scope (e.g. 'TRANSDUCER_SPEC', 'LEGAL_GRANT').

        Returns:
            True if authenticated by a trusted entry, False otherwise.
        """
        if not isinstance(reference, dict):
            return False

        title = reference.get("document_title")
        uri = reference.get("locator_uri")
        if not title or not isinstance(title, str) or not uri or not isinstance(uri, str):
            return False

        # Find entry by title
        entry = self._trusted_by_title.get(title)
        if not entry:
            return False

        # Fail closed if locator_uri does not match registered URI
        if entry.get("locator_uri") != uri:
            return False

        # Fail closed if verification status is unverified
        v_status = entry.get("verification_status")
        if v_status not in ("AUTHENTICATED", "VERIFIED_PRIMARY", "VERIFIED_DERIVATION"):
            return False

        # Fail closed if required scope is not granted
        if required_scope:
            scopes = entry.get("accepted_scopes", [])
            if required_scope not in scopes and "ALL" not in scopes:
                return False

        # Fail closed on document content digest mismatch
        expected_sha = (entry.get("document_sha256") or entry.get("expected_sha256") or "").lower()
        ref_sha = (reference.get("document_sha256") or "").lower()
        if not expected_sha or not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
            return False
        if not ref_sha or not re.fullmatch(r"[a-f0-9]{64}", ref_sha) or ref_sha != expected_sha:
            return False

        return True

    def identity_digest(self) -> str:
        """Return a stable identity for the complete trusted registry contents."""
        canonical = [
            self._trusted_by_title[title]
            for title in sorted(self._trusted_by_title)
        ]
        return hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @classmethod
    def create_empty(cls) -> "EvidenceRegistry":
        """Create an empty registry with zero trusted documents (default secure baseline)."""
        return cls()

    @classmethod
    def create_fictional_test_registry(cls) -> "EvidenceRegistry":
        """Create a registry pre-loaded with fictional test fixture authorities."""
        reg = cls()
        reg.register_document({
            "document_title": "PCB 352C33 Spec Sheet",
            "locator_uri": "https://example.com/datasheets/352C33.pdf",
            "document_sha256": "05d1dc0142929ada23a90f34d0688c69472bb41c6863bde9d832112ed1576434",
            "authority_level": 2,
            "verification_status": "AUTHENTICATED",
            "accepted_scopes": ["TRANSDUCER_SPEC", "PHYSICAL_METADATA", "CALIBRATION"],
            "verified_publisher": "PCB Piezotronics",
            "verification_record_id": "vr_fictional_pcb_01"
        })
        reg.register_document({
            "document_title": "Laboratory Test Rig Documentation",
            "locator_uri": "https://example.com/docs/test_rig_specs.pdf",
            "document_sha256": "7929d21c2357a5ee8dbafcbcab13431cbc32b670b0343dedecd47c10f46d39ef",
            "authority_level": 2,
            "verification_status": "AUTHENTICATED",
            "accepted_scopes": ["DAQ_CONFIG", "PHYSICAL_METADATA", "OPERATING_CONDITIONS"],
            "verified_publisher": "Analog Devices Edge AI Laboratory",
            "verification_record_id": "vr_fictional_rig_01"
        })
        reg.register_document({
            "document_title": "Test Bench Teardown Logbook",
            "locator_uri": "https://example.com/logs/teardown_log_2026.pdf",
            "document_sha256": "65f9f8f982e5cbfccc25722376e4933975ee932432ba568eb540430d9a4feec4",
            "authority_level": 1,
            "verification_status": "AUTHENTICATED",
            "accepted_scopes": ["TEARDOWN_LOG", "SPECIMEN_DISTINCTNESS"],
            "verified_publisher": "Independent Metrology Inspection Team",
            "verification_record_id": "vr_fictional_teardown_01"
        })
        reg.register_document({
            "document_title": "Precision Bearing Test Rig Benchmark",
            "locator_uri": "https://example.com/datasheets/benchmark.pdf",
            "document_sha256": "c06f0b4fce8b3d19420526f3f4aeaa744f6a1cf60fda5478345b0307caf24458",
            "authority_level": 2,
            "verification_status": "AUTHENTICATED",
            "accepted_scopes": ["LEGAL_GRANT", "PUBLISHER_IDENTITY", "DATASET_METADATA"],
            "verified_publisher": "Analog Devices Edge AI Laboratory",
            "verification_record_id": "vr_fictional_license_01"
        })
        reg.register_document({
            "document_title": "Metrology Inspection Certificate",
            "locator_uri": "https://example.com/certs/metrology_01.pdf",
            "document_sha256": "883289be69ae546a5aaf08988f8524ca74ca9bf78d3f46ddad5d48010cb76610",
            "authority_level": 1,
            "verification_status": "AUTHENTICATED",
            "accepted_scopes": ["GROUND_TRUTH_AUTHORITY", "FAULT_LABEL"],
            "verified_publisher": "Independent Metrology Inspection Team",
            "verification_record_id": "vr_fictional_label_auth_01"
        })
        return reg


MAX_FICTIONAL_SOURCE_BYTES = 5 * 1024 * 1024
MAX_FICTIONAL_SOURCE_SAMPLES = 1_000_000


def evaluate_fictional_source_artifact(
    source_artifact: Any,
    evidence: Dict[str, Any],
    stage10_report: Dict[str, Any],
) -> Dict[str, Any]:
    """Load and analyze an explicit bounded fictional source artifact.

    This is an application-level trust boundary: the artifact bytes, inspection
    digest, evidence digest, channel, units, sample count, and full-scale facts
    must agree before the lower policy analyzer receives samples.
    """
    if isinstance(source_artifact, (str, Path)):
        path = Path(source_artifact)
        if not path.is_file():
            raise FileNotFoundError(f"Fictional source artifact not found: {path}")
        if path.stat().st_size > MAX_FICTIONAL_SOURCE_BYTES:
            raise ValueError("Fictional source artifact exceeds the 5 MiB resource limit")
        raw = path.read_bytes()
        try:
            artifact = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Fictional source artifact is not valid UTF-8 JSON: {exc}") from exc
    elif isinstance(source_artifact, dict):
        artifact = copy.deepcopy(source_artifact)
        raw = json.dumps(
            artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if len(raw) > MAX_FICTIONAL_SOURCE_BYTES:
            raise ValueError("Fictional source artifact exceeds the 5 MiB resource limit")
    else:
        raise ValueError("Fictional source artifact must be a supported local path or dict")

    if not isinstance(artifact, dict) or artifact.get("artifact_format") != "ADI_FICTIONAL_SOURCE_V1":
        raise ValueError("Unsupported fictional source artifact format")
    if artifact.get("schema_version") != "1.0.0":
        raise ValueError("Unsupported fictional source artifact schema_version")

    actual_digest = hashlib.sha256(raw).hexdigest()
    s10_digest = stage10_report.get("source_identity", {}).get("raw_byte_sha256")
    evidence_digest = evidence.get("target_source_sha256")
    if actual_digest != s10_digest or actual_digest != evidence_digest:
        raise ContractValidationError(
            "source_sha256",
            "Fictional source artifact actual byte digest does not match Stage 10 and evidence source digests.",
            actual_digest,
        )

    target_channel = evidence.get("target_variable_name")
    s10_channel = stage10_report.get("container_facts", {}).get("target_variable")
    if target_channel != s10_channel:
        raise ContractValidationError(
            "target_channel",
            "Evidence and Stage 10 target channels contradict one another.",
            target_channel,
        )
    channels = artifact.get("channels")
    if not isinstance(channels, dict) or target_channel not in channels:
        raise ContractValidationError(
            "target_channel",
            f"Fictional source artifact does not contain designated channel {target_channel!r}.",
            target_channel,
        )
    samples = channels[target_channel]
    if not isinstance(samples, list):
        raise TypeError("Fictional source channel must be a JSON array")
    if len(samples) > MAX_FICTIONAL_SOURCE_SAMPLES:
        raise ValueError("Fictional source channel exceeds the 1,000,000-sample resource limit")

    expected_count = stage10_report.get("container_facts", {}).get("sample_count")
    evidence_count = evidence.get("acquisition_signal_interpretation", {}).get("sample_count")
    if len(samples) != expected_count or len(samples) != evidence_count:
        raise ContractValidationError(
            "sample_count",
            "Fictional source extracted sample count differs from Stage 10 or evidence.",
            len(samples),
        )

    artifact_units = artifact.get("units")
    evidence_units_entry = evidence.get("acquisition_signal_interpretation", {}).get("calibrated_acceleration_unit", {})
    evidence_units = evidence_units_entry.get("resolved_value") if isinstance(evidence_units_entry, dict) else None
    if artifact_units != evidence_units:
        raise ContractValidationError(
            "full_scale_units",
            "Fictional source units do not match the authenticated calibrated acceleration units.",
            artifact_units,
        )

    artifact_full_scale = artifact.get("full_scale_limit")
    evidence_full_scale_entry = evidence.get("sensor_identity", {}).get("full_scale_limit_physical", {})
    evidence_full_scale = evidence_full_scale_entry.get("resolved_value") if isinstance(evidence_full_scale_entry, dict) else None
    if artifact_full_scale != evidence_full_scale:
        raise ContractValidationError(
            "full_scale_limit",
            "Fictional source full-scale limit does not match the evidence full-scale limit.",
            artifact_full_scale,
        )

    return analyze_signal_integrity(
        samples,
        actual_digest,
        target_channel,
        evidence_full_scale,
        evidence_units,
    )


def analyze_signal_integrity(
    samples: Sequence[Any],
    source_digest: str,
    target_channel: str,
    full_scale_limit: Optional[float],
    full_scale_units: Optional[str] = "g",
    rail_tolerance: float = 0.001,
) -> Dict[str, Any]:
    """Deterministically analyze raw synthetic/fictional waveform for nonfinite values and clipping.

    Applies the Stage 11 & Stage 12 fail-closed policy:
    - Scans all samples for non-finite values (NaN, Inf) and malformed structures.
    - 3 or more contiguous rail samples block admission (SUSTAINED_RAIL_SATURATION).
    - Total rail-sample count above 0.01% of sample count blocks admission (EXCESSIVE_CLIPPING).
    - Non-finite samples (NaN or Infinity) block admission (NONFINITE_DETECTED).
    - Conservative precedence: NONFINITE_DETECTED takes precedence over UNKNOWN_FULL_SCALE.
    - When full scale limit is unknown or non-positive, clipping cannot be assessed (UNKNOWN_FULL_SCALE).

    Args:
        samples: Sequence of numeric values.
        source_digest: 64-hex SHA-256 digest of container.
        target_channel: Inspected channel/variable name.
        full_scale_limit: Transducer physical full-scale limit (peak).
        full_scale_units: Units of full scale (default 'g').
        rail_tolerance: Fraction below full scale considered rail (default 0.001 -> 99.9%).

    Returns:
        SignalIntegrityEvidenceV1 dictionary.

    Raises:
        TypeError: If samples is not a sequence, contains nested or non-numeric types, or full_scale_limit is invalid type.
        ValueError: If rail_tolerance is not in (0, 1).
    """
    if not isinstance(samples, (list, tuple, Sequence)) or isinstance(samples, (str, bytes)):
        raise TypeError(f"samples must be a sequence of scalar numbers, got {type(samples).__name__}")

    if isinstance(rail_tolerance, bool) or not isinstance(rail_tolerance, (int, float)) or rail_tolerance <= 0 or rail_tolerance >= 1:
        raise ValueError(f"rail_tolerance must be a float between 0 and 1 exclusive, got {rail_tolerance!r}")

    if full_scale_limit is not None:
        if isinstance(full_scale_limit, bool) or not isinstance(full_scale_limit, (int, float)):
            raise TypeError(f"full_scale_limit must be a numeric value or None, got {type(full_scale_limit).__name__}")
        if not math.isfinite(full_scale_limit):
            raise ValueError(f"full_scale_limit must be finite when provided, got {full_scale_limit!r}")
        if full_scale_limit <= 0:
            full_scale_limit = None

    if full_scale_units is not None and full_scale_units not in ("g", "m/s^2"):
        raise ValueError(f"full_scale_units must be 'g' or 'm/s^2', got {full_scale_units!r}")
    if isinstance(rail_tolerance, bool) or not isinstance(rail_tolerance, (int, float)) or not math.isfinite(rail_tolerance) or rail_tolerance <= 0 or rail_tolerance >= 1:
        raise ValueError(f"rail_tolerance must be a finite float between 0 and 1 exclusive, got {rail_tolerance!r}")
    sample_count = len(samples)
    nonfinite_count = 0
    rail_sample_count = 0
    longest_contiguous_rail_run = 0
    current_rail_run = 0

    rail_threshold = (1.0 - rail_tolerance) * full_scale_limit if full_scale_limit is not None else None

    for idx, val in enumerate(samples):
        if isinstance(val, (list, tuple, dict, set)):
            raise TypeError(f"Unsupported sample structure at index {idx}: nested {type(val).__name__} is forbidden")
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise TypeError(f"Unsupported sample type at index {idx}: {type(val).__name__} is not a scalar numeric")

        if math.isnan(val) or math.isinf(val):
            nonfinite_count += 1
            current_rail_run = 0
            continue

        if rail_threshold is not None:
            if abs(val) >= rail_threshold:
                rail_sample_count += 1
                current_rail_run += 1
                if current_rail_run > longest_contiguous_rail_run:
                    longest_contiguous_rail_run = current_rail_run
            else:
                current_rail_run = 0

    if full_scale_limit is None:
        fs_limit_val = None
        rail_sample_count_val = None
        longest_run_val = None
        clipping_ratio_val = None
        if nonfinite_count > 0:
            verification_status = "NONFINITE_DETECTED"
        else:
            verification_status = "UNKNOWN_FULL_SCALE"
    else:
        fs_limit_val = float(full_scale_limit)
        rail_sample_count_val = rail_sample_count
        longest_run_val = longest_contiguous_rail_run
        clipping_ratio_val = (rail_sample_count / sample_count) if sample_count > 0 else 0.0

        if nonfinite_count > 0:
            verification_status = "NONFINITE_DETECTED"
        elif longest_contiguous_rail_run >= 3:
            verification_status = "SUSTAINED_RAIL_SATURATION"
        elif clipping_ratio_val > 0.0001:  # > 0.01%
            verification_status = "EXCESSIVE_CLIPPING"
        else:
            verification_status = "PASS"

    payload = {
        "schema_version": "1.0.0",
        "source_sha256": source_digest,
        "target_channel": target_channel,
        "sample_count": sample_count,
        "nonfinite_sample_count": nonfinite_count,
        "full_scale_limit": fs_limit_val,
        "full_scale_units": full_scale_units,
        "rail_sample_count": rail_sample_count_val,
        "longest_contiguous_rail_run": longest_run_val,
        "clipping_ratio": clipping_ratio_val,
        "data_quality_provenance": {
            "analyzer": "adi_common.admission.analyze_signal_integrity",
            "analysis_policy_version": "stage12.signal-integrity.v2",
            "source_sha256": source_digest,
            "target_channel": target_channel,
            "sample_count": sample_count,
            "rail_tolerance_fraction": rail_tolerance,
            "analyzed_at": "2026-11-20T00:00:00Z",
        },
        "verification_status": verification_status,
    }
    return payload


def verify_calibration_conversion_chain(
    sensor: Dict[str, Any],
    acq: Dict[str, Any],
    registry: EvidenceRegistry,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Validate metadata-level conversion chain, physical units, and dimensional math.

    Enforces:
    - Specified, supported raw stored units and calibrated acceleration units.
    - Verified positive transducer sensitivity.
    - Consistency between transducer physical full-scale limit and measurement range peak.
    - Analog voltage conversion path: dimensional sensitivity matching, positive AFE gain,
      and positive ADC scaling when ADC input range is documented.
    - Digital counts conversion path (digital MEMS): dimensional sensitivity matching,
      positive ADC bit depth, and positive scaling factor.
    - Fail-closed rejection of unrecognized raw units or missing mandatory conversion facts.

    Returns:
        (is_valid, failure_status, diagnostic_reason)
    """
    raw_units_entry = acq.get("raw_stored_units", {})
    raw_units = raw_units_entry.get("resolved_value")
    if not raw_units or not isinstance(raw_units, str):
        return False, "BLOCKED_UNKNOWN_METADATA", "RAW_UNITS_UNVERIFIED: Raw stored units must be specified and resolved."
    raw_units_ref = raw_units_entry.get("evidence_reference")
    if not registry.is_authenticated(raw_units_ref, "CALIBRATION"):
        return False, "BLOCKED_CALIBRATION", "RAW_UNITS_UNAUTHENTICATED: Raw stored units lack authenticated calibration evidence."

    cal_unit_entry = acq.get("calibrated_acceleration_unit", {})
    cal_unit = cal_unit_entry.get("resolved_value")
    if cal_unit not in ("g", "m/s^2"):
        return False, "BLOCKED_UNKNOWN_METADATA", f"UNITS_UNVERIFIED: Invalid or unstated calibrated acceleration units '{cal_unit}'"
    if not registry.is_authenticated(cal_unit_entry.get("evidence_reference"), "CALIBRATION"):
        return False, "BLOCKED_CALIBRATION", "UNITS_UNAUTHENTICATED: Calibrated units lack authenticated calibration evidence."

    sens_entry = sensor.get("sensitivity", {})
    sens_val = sens_entry.get("resolved_value")
    if sens_val is None or sens_val <= 0:
        return False, "BLOCKED_UNKNOWN_METADATA", "SENSITIVITY_UNVERIFIED: Transducer sensitivity must be a verified positive number"
    if not registry.is_authenticated(sens_entry.get("evidence_reference"), "CALIBRATION"):
        return False, "BLOCKED_CALIBRATION", "SENSITIVITY_UNAUTHENTICATED: Sensitivity lacks authenticated calibration evidence."

    sens_units_entry = sensor.get("sensitivity_units", {})
    sens_units = sens_units_entry.get("resolved_value")
    if not sens_units or not isinstance(sens_units, str):
        return False, "BLOCKED_UNKNOWN_METADATA", "SENSITIVITY_UNITS_UNVERIFIED: Transducer sensitivity units must be specified."
    if not registry.is_authenticated(sens_units_entry.get("evidence_reference"), "CALIBRATION"):
        return False, "BLOCKED_CALIBRATION", "SENSITIVITY_UNITS_UNAUTHENTICATED: Sensitivity units lack authenticated calibration evidence."

    # Full scale limit consistency with measurement range
    fs_entry = sensor.get("full_scale_limit_physical", {})
    fs_val = fs_entry.get("resolved_value")
    range_entry = sensor.get("measurement_range_peak_g", {})
    range_val = range_entry.get("resolved_value")
    if fs_val is not None and not registry.is_authenticated(fs_entry.get("evidence_reference"), "CALIBRATION"):
        return False, "BLOCKED_CALIBRATION", "FULL_SCALE_UNAUTHENTICATED: Physical full-scale limit lacks authenticated calibration evidence."
    if range_val is not None and not registry.is_authenticated(range_entry.get("evidence_reference"), "CALIBRATION"):
        return False, "BLOCKED_CALIBRATION", "MEASUREMENT_RANGE_UNAUTHENTICATED: Measurement range lacks authenticated calibration evidence."
    if fs_val is not None and range_val is not None and fs_val != range_val:
        return False, "BLOCKED_CALIBRATION", f"FULL_SCALE_INCONSISTENCY: Sensor full-scale limit ({fs_val}) does not match measurement range peak ({range_val})."

    # Analog Voltage Conversion Path
    if raw_units == "VOLTS":
        if sens_units not in ("mV/g", "V/g", "mV/(m/s^2)", "V/(m/s^2)"):
            return False, "BLOCKED_CALIBRATION", f"DIMENSIONAL_MATH_ERROR: Raw stored units are VOLTS but sensitivity units '{sens_units}' are dimensionally incompatible"
        if cal_unit == "g" and "m/s^2" in str(sens_units):
            return False, "BLOCKED_CALIBRATION", f"DIMENSIONAL_MATH_ERROR: Acceleration unit is 'g' but sensitivity is specified in '{sens_units}'"
        if cal_unit == "m/s^2" and "/g" in str(sens_units):
            return False, "BLOCKED_CALIBRATION", f"DIMENSIONAL_MATH_ERROR: Acceleration unit is 'm/s^2' but sensitivity is specified in '{sens_units}'"

        # Analog front end gain check
        afe_entry = acq.get("analog_front_end_gain", {})
        afe_gain = afe_entry.get("resolved_value")
        if afe_gain is None or afe_gain <= 0:
            return False, "BLOCKED_CALIBRATION", "AFE_GAIN_UNVERIFIED: Analog front-end gain must be a verified positive number for voltage conversion."
        if not registry.is_authenticated(afe_entry.get("evidence_reference"), "CALIBRATION"):
            return False, "BLOCKED_CALIBRATION", "AFE_GAIN_UNAUTHENTICATED: Analog front-end gain lacks authenticated calibration evidence."

        # ADC scaling factor check if ADC range is specified
        adc_range = acq.get("adc_input_range_volts", {}).get("resolved_value")
        adc_scale = acq.get("adc_scaling_factor", {}).get("resolved_value")
        if adc_range is not None and (adc_scale is None or adc_scale <= 0):
            return False, "BLOCKED_CALIBRATION", "ADC_SCALING_UNVERIFIED: ADC scaling factor must be verified positive number when ADC input range is specified."
        if adc_range is not None and not registry.is_authenticated(
            acq.get("adc_input_range_volts", {}).get("evidence_reference"), "CALIBRATION"
        ):
            return False, "BLOCKED_CALIBRATION", "ADC_RANGE_UNAUTHENTICATED: ADC input range lacks authenticated calibration evidence."
        if adc_range is not None and not registry.is_authenticated(
            acq.get("adc_scaling_factor", {}).get("evidence_reference"), "CALIBRATION"
        ):
            return False, "BLOCKED_CALIBRATION", "ADC_SCALING_UNAUTHENTICATED: ADC scaling lacks authenticated calibration evidence."

    # Digital Counts Conversion Path (Digital MEMS)
    elif raw_units in ("RAW_ADC_COUNTS", "ACCELERATION_DIGITAL_COUNTS", "COUNTS", "LSB"):
        bit_depth_entry = acq.get("adc_bit_depth", {})
        bit_depth = bit_depth_entry.get("resolved_value") if isinstance(bit_depth_entry, dict) else None
        if bit_depth is None or isinstance(bit_depth, bool) or not isinstance(bit_depth, (int, float)) or not math.isfinite(bit_depth) or bit_depth <= 0:
            return False, "BLOCKED_CALIBRATION", "ADC_ENCODING_UNVERIFIED: Digital counts conversion requires verified positive ADC bit depth."
        if not registry.is_authenticated(bit_depth_entry.get("evidence_reference"), "CALIBRATION"):
            return False, "BLOCKED_CALIBRATION", "ADC_ENCODING_UNAUTHENTICATED: Digital ADC encoding lacks authenticated calibration evidence."

        adc_scale_entry = acq.get("adc_scaling_factor", {})
        adc_scale = adc_scale_entry.get("resolved_value") if isinstance(adc_scale_entry, dict) else None
        if adc_scale is None or isinstance(adc_scale, bool) or not isinstance(adc_scale, (int, float)) or not math.isfinite(adc_scale) or adc_scale <= 0:
            return False, "BLOCKED_CALIBRATION", "DIGITAL_SCALING_UNVERIFIED: Digital counts conversion requires verified positive scaling factor."
        if not registry.is_authenticated(adc_scale_entry.get("evidence_reference"), "CALIBRATION"):
            return False, "BLOCKED_CALIBRATION", "DIGITAL_SCALING_UNAUTHENTICATED: Digital scaling lacks authenticated calibration evidence."

        supported_digital_units = ("LSB/g", "mg/LSB", "g/LSB", "counts/g", "LSB/(m/s^2)", "m/s^2/LSB")
        if sens_units not in supported_digital_units:
            return False, "BLOCKED_CALIBRATION", f"DIMENSIONAL_MATH_ERROR: Digital raw units '{raw_units}' require compatible digital sensitivity units, got '{sens_units}'."
        if cal_unit == "g" and "m/s^2" in sens_units:
            return False, "BLOCKED_CALIBRATION", f"DIMENSIONAL_MATH_ERROR: Acceleration unit is 'g' but digital sensitivity is '{sens_units}'."
        if cal_unit == "m/s^2" and sens_units in ("LSB/g", "counts/g", "mg/LSB", "g/LSB"):
            return False, "BLOCKED_CALIBRATION", f"DIMENSIONAL_MATH_ERROR: Acceleration unit is 'm/s^2' but digital sensitivity is '{sens_units}'."

    else:
        return False, "BLOCKED_CALIBRATION", f"UNRECOGNIZED_RAW_UNITS: Raw stored units '{raw_units}' are unrecognized or lack valid conversion path."

    return True, None, None


def verify_diagnostic_partition_leakage(
    partition_assignments: Any,
    stage10_copy: Dict[str, Any],
    evidence_copy: Dict[str, Any],
    target_specimen_group: str,
) -> Tuple[bool, str, List[str]]:
    """Verify independently established target/specimen relationships and partitions."""
    if partition_assignments is None or not isinstance(partition_assignments, dict) or len(partition_assignments) == 0:
        return (
            False,
            "BLOCKED_PARTITION_LEAKAGE",
            ["PARTITION_EVIDENCE_MISSING: Diagnostic evaluation eligibility requires explicit partition assignments."],
        )

    rec_to_partition = partition_assignments.get("recording_partitions")
    rec_to_specimen = partition_assignments.get("recording_specimens")
    if not isinstance(rec_to_partition, dict) or not isinstance(rec_to_specimen, dict):
        return (
            False,
            "BLOCKED_PARTITION_LEAKAGE",
            ["PARTITION_RECORDING_RELATIONSHIP_MISSING: Explicit recording-to-partition and recording-to-specimen mappings are required."],
        )

    if len(rec_to_partition) == 0:
        return (False, "BLOCKED_PARTITION_LEAKAGE",
                ["PARTITION_EVIDENCE_MISSING: No valid partition assignments found."])

    if set(rec_to_specimen) != set(rec_to_partition):
        return (
            False,
            "BLOCKED_PARTITION_LEAKAGE",
            ["PARTITION_SPECIMEN_MAPPING_MISSING: Every participating recording must have exactly one explicit specimen mapping."],
        )

    supported_roles = {"TRAIN", "TEST", "VALIDATION", "HELD_OUT"}
    for recording_id, partition in rec_to_partition.items():
        if not isinstance(recording_id, str) or not recording_id.strip():
            return (False, "BLOCKED_PARTITION_LEAKAGE",
                    ["PARTITION_EVIDENCE_INVALID: Recording identifiers must be non-empty strings."])
        if not isinstance(partition, str) or partition not in supported_roles:
            return (False, "BLOCKED_PARTITION_LEAKAGE",
                    [f"PARTITION_ROLE_INVALID: Unsupported partition role {partition!r}; supported roles are {sorted(supported_roles)}."])

    for recording_id, specimen_id in rec_to_specimen.items():
        if not isinstance(recording_id, str) or not recording_id.strip():
            return (False, "BLOCKED_PARTITION_LEAKAGE",
                    ["PARTITION_EVIDENCE_INVALID: Recording identifiers must be non-empty strings."])
        if not isinstance(specimen_id, str) or not specimen_id.strip():
            return (False, "BLOCKED_PARTITION_LEAKAGE",
                    ["PARTITION_SPECIMEN_MAPPING_INVALID: Specimen mappings must be non-empty strings; null, empty, and malformed values are rejected."])

    # Establish the target recording and specimen from independently established
    # source facts, never from a caller-selected partition field.
    source_identity = stage10_copy.get("source_identity", {})
    target_candidate_ids = []
    for key in ("source_filename", "source_id"):
        value = source_identity.get(key)
        if isinstance(value, str) and value.strip():
            target_candidate_ids.append(value)
    local_annotations = stage10_copy.get("local_annotations", {})
    recording_id = local_annotations.get("recording_id")
    if isinstance(recording_id, str) and recording_id.strip():
        target_candidate_ids.append(recording_id)

    specimen_entry = evidence_copy.get("experiment_specimen_identity", {}).get("physical_specimen_id", {})
    independent_specimen = specimen_entry.get("resolved_value") if isinstance(specimen_entry, dict) else None
    if not isinstance(independent_specimen, str) or not independent_specimen.strip():
        return (
            False,
            "BLOCKED_PARTITION_LEAKAGE",
            ["SPECIMEN_MAPPING_INVALID: Independently established target specimen identity is missing or malformed."],
        )

    matched_target_id = None
    for cand in target_candidate_ids:
        if cand in rec_to_partition:
            matched_target_id = cand
            break

    if not matched_target_id:
        return (
            False,
            "BLOCKED_PARTITION_LEAKAGE",
            [f"UNRELATED_PARTITION_MAPPING: Independently established target recording is not present in partition assignments {sorted(rec_to_partition)}."],
        )

    mapped_spec = rec_to_specimen[matched_target_id]
    if mapped_spec != independent_specimen:
        return (False, "BLOCKED_PARTITION_LEAKAGE",
                [f"SPECIMEN_MAPPING_MISMATCH: Target recording '{matched_target_id}' maps to '{mapped_spec}', contradicting established specimen '{independent_specimen}'."])

    spec_partitions: Dict[str, Set[str]] = {}
    for recording_id, specimen_id in rec_to_specimen.items():
        spec_partitions.setdefault(specimen_id, set()).add(rec_to_partition[recording_id])

    leaked = [specimen_id for specimen_id, roles in spec_partitions.items() if len(roles) > 1]
    if leaked:
        return (False, "BLOCKED_PARTITION_LEAKAGE",
                [f"CROSS_SPECIMEN_LEAKAGE: Specimen(s) {sorted(leaked)} assigned across multiple benchmark partitions."])

    assigned_part = rec_to_partition[matched_target_id]
    return (True, "ELIGIBLE_FOR_DIAGNOSTIC_EVALUATION",
            [f"Distinct physical specimens, verified ground-truth labels, and disjoint partition '{assigned_part}' confirmed on fictional fixture."])


def evaluate_measurement_admission(
    evidence: Dict[str, Any],
    stage10_report: Dict[str, Any],
    intended_use: str,
    evidence_registry: Optional[EvidenceRegistry] = None,
    signal_integrity_evidence: Optional[Dict[str, Any]] = None,
    operational_mode: bool = False,
    partition_assignments: Optional[Dict[str, Any]] = None,
    source_artifact: Optional[Any] = None,
) -> Dict[str, Any]:
    """Execute the deterministic measurement admission evaluation engine.

    Evaluates candidate vibration measurement evidence against authentic Stage 10
    inspection findings, offline authenticated evidence registry documents, signal
    integrity facts, and intended operational use context.

    Safety Boundary:
    If operational_mode is True, immediately raises OperationalAdmissionDisabledError.
    Stage 12 strictly disables operational real-data admission.

    Returns:
        Validated MeasurementAdmissionDecisionV1 payload dictionary.

    Raises:
        OperationalAdmissionDisabledError: If operational_mode is True.
        ContractValidationError: If evidence, stage10_report, or intended_use is malformed.
    """
    # 0. Operational Safety Boundary Gate
    if operational_mode:
        raise OperationalAdmissionDisabledError(
            "OPERATIONAL_ADMISSION_DISABLED: Real-data admission pipeline is strictly disabled in Stage 12. "
            "Actual real-dataset admission, physical measurement conversion, and detector execution remain disabled. "
            "Only fictional test evaluation (FICTIONAL_TEST_ONLY) is supported."
        )

    # 1. Contract Validation & Non-Mutation Preflight
    evidence_copy = copy.deepcopy(evidence)
    stage10_copy = copy.deepcopy(stage10_report)

    # Validate evidence contract
    validate_calibration_measurement_evidence(evidence_copy)

    # Validate Stage 10 contract
    validate_exploratory_source_inspection(stage10_copy)

    # Validate intended use
    if intended_use not in VALID_INTENDED_USES:
        raise ContractValidationError(
            "intended_use",
            f"Intended use '{intended_use}' is invalid. Allowed uses: {sorted(VALID_INTENDED_USES)}",
            intended_use,
        )

    registry = evidence_registry if evidence_registry is not None else EvidenceRegistry.create_empty()

    # Default decision fields
    final_status = "ADMITTED_FOR_MEASUREMENT"
    gate_reached = "ADMISSION"
    diagnostic_reason = "DESIGN_ONLY_PROVISIONAL_PASS: Physical measurement criteria satisfied on fictional fixture."
    precautionary_specimen_group = None

    # ==========================================================================
    # PRECEDENCE GATE 1: STRUCTURAL FORMAT (Stage 10 Container Facts)
    # ==========================================================================
    s10_findings = stage10_copy.get("inspection_findings", {})
    s10_struct_status = s10_findings.get("structural_status")
    if s10_struct_status == "NONFINITE_VALUES_DETECTED":
        final_status = "BLOCKED_SIGNAL_INTEGRITY"
        gate_reached = "SIGNAL_INTEGRITY"
        diagnostic_reason = "NONFINITE_VALUES_PRESENT: Stage 10 inspection detected non-finite values in target variable."
    elif s10_struct_status != "STRUCTURAL_SUCCESS":
        final_status = "BLOCKED_UNSUPPORTED_FORMAT"
        gate_reached = "STRUCTURAL_FORMAT"
        diagnostic_reason = (
            f"STAGE10_STRUCTURAL_FAILURE: Stage 10 structural status is '{s10_struct_status}' "
            "(must be 'STRUCTURAL_SUCCESS')"
        )

    # ==========================================================================
    # PRECEDENCE GATE 2: SIGNAL INTEGRITY (Finite Values & Rail Saturation)
    # ==========================================================================
    if gate_reached == "ADMISSION":
        s10_facts = stage10_copy.get("container_facts", {})
        s10_nonfinite = s10_facts.get("nonfinite_sample_count", 0)
        if s10_nonfinite is not None and s10_nonfinite > 0:
            final_status = "BLOCKED_SIGNAL_INTEGRITY"
            gate_reached = "SIGNAL_INTEGRITY"
            diagnostic_reason = f"NONFINITE_VALUES_PRESENT: Stage 10 detected {s10_nonfinite} non-finite samples."
        else:
            s10_id = stage10_copy.get("source_identity", {})
            s10_facts = stage10_copy.get("container_facts", {})
            s10_raw_hash = s10_id.get("raw_byte_sha256")
            s10_var = s10_facts.get("target_variable")
            s10_count = s10_facts.get("sample_count")
            s10_nonfinite = s10_facts.get("nonfinite_sample_count", 0)

            if signal_integrity_evidence is None:
                final_status = "BLOCKED_SIGNAL_INTEGRITY"
                gate_reached = "SIGNAL_INTEGRITY"
                diagnostic_reason = "SIGNAL_INTEGRITY_UNASSESSED: No external claim is sufficient without an explicit source-bound artifact."
            elif gate_reached == "ADMISSION":
                try:
                    ev_target_hash = evidence_copy.get("target_source_sha256")
                    ev_var = evidence_copy.get("target_variable_name")
                    ev_count = evidence_copy.get("acquisition_signal_interpretation", {}).get("sample_count")
                    ev_nonfinite = evidence_copy.get("acquisition_signal_interpretation", {}).get("nonfinite_sample_count", 0)

                    identity_consistent = (
                        s10_raw_hash == ev_target_hash
                        and s10_var == ev_var
                        and s10_count == ev_count
                        and s10_nonfinite == ev_nonfinite
                    )
                    evidence_fs_entry = evidence_copy.get("sensor_identity", {}).get("full_scale_limit_physical", {})
                    evidence_fs = evidence_fs_entry.get("resolved_value") if isinstance(evidence_fs_entry, dict) else None
                    evidence_units_entry = evidence_copy.get("acquisition_signal_interpretation", {}).get("calibrated_acceleration_unit", {})
                    evidence_units = evidence_units_entry.get("resolved_value") if isinstance(evidence_units_entry, dict) else None

                    if not identity_consistent:
                        # Leave precedence to the existing source/variable/sample provenance gate.
                        pass
                    elif evidence_fs is None or evidence_units not in ("g", "m/s^2"):
                        # Leave precedence to the existing physical metadata gate.
                        pass
                    else:
                        if source_artifact is not None:
                            recomputed = evaluate_fictional_source_artifact(
                                source_artifact,
                                evidence_copy,
                                stage10_copy,
                            )
                            required_claim_fields = (
                                "source_sha256", "target_channel", "sample_count",
                                "nonfinite_sample_count", "full_scale_limit",
                                "full_scale_units", "rail_sample_count",
                                "longest_contiguous_rail_run", "clipping_ratio",
                                "verification_status", "data_quality_provenance",
                            )
                            if any(field not in signal_integrity_evidence for field in required_claim_fields):
                                raise ContractValidationError(
                                    None,
                                    "SIGNAL_INTEGRITY_CONTRACT_INVALID: External claim is missing required source-analysis fields.",
                                    signal_integrity_evidence,
                                )
                            for key in (
                                "source_sha256", "target_channel", "sample_count", "nonfinite_sample_count",
                                "full_scale_limit", "full_scale_units", "rail_sample_count",
                                "longest_contiguous_rail_run", "clipping_ratio", "verification_status",
                                "data_quality_provenance",
                            ):
                                if signal_integrity_evidence.get(key) != recomputed.get(key):
                                    raise ContractValidationError(
                                        key if key in ("source_sha256", "target_channel", "sample_count") else "data_quality_provenance",
                                        f"Caller tampered signal integrity claim field '{key}' differs from source-artifact computation.",
                                        signal_integrity_evidence,
                                    )
                            signal_integrity_evidence = recomputed
                        else:
                            raise ContractValidationError(
                                None,
                                "SIGNAL_INTEGRITY_EXTERNAL_CLAIM_REJECTED: Missing explicit supported fictional source artifact.",
                                signal_integrity_evidence,
                            )
                except ContractValidationError as exc:
                    if exc.field in ("source_sha256", "target_channel", "sample_count"):
                        final_status = "BLOCKED_PROVENANCE"
                        gate_reached = "SOURCE_PROVENANCE"
                    elif exc.field in ("full_scale_limit", "full_scale_units"):
                        final_status = "BLOCKED_UNKNOWN_METADATA"
                        gate_reached = "PHYSICAL_METADATA"
                    else:
                        final_status = "BLOCKED_SIGNAL_INTEGRITY"
                        gate_reached = "SIGNAL_INTEGRITY"
                        if "field 'rail_sample_count'" in str(exc) or "field 'longest_contiguous_rail_run'" in str(exc):
                            if isinstance(signal_integrity_evidence, dict):
                                if signal_integrity_evidence.get("longest_contiguous_rail_run", 0) >= 3:
                                    diagnostic_reason = "SUSTAINED_RAIL_SATURATION: Caller claim conflicts with source-artifact analysis."
                                elif signal_integrity_evidence.get("clipping_ratio", 0.0) > 0.0001:
                                    diagnostic_reason = "EXCESSIVE_CLIPPING_RATIO: Caller claim conflicts with source-artifact analysis."
                        else:
                            diagnostic_reason = f"SIGNAL_INTEGRITY_SOURCE_BOUND_REJECTION: {exc}"
                except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
                    final_status = "BLOCKED_SIGNAL_INTEGRITY"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"SIGNAL_INTEGRITY_SOURCE_BOUND_REJECTION: {exc}"

            ev_target_hash = evidence_copy.get("target_source_sha256")
            ev_var = evidence_copy.get("target_variable_name")
            ev_count = evidence_copy.get("acquisition_signal_interpretation", {}).get("sample_count")
            ev_nonfinite = evidence_copy.get("acquisition_signal_interpretation", {}).get("nonfinite_sample_count", 0)

            # Bind signal integrity against verified source facts when evidence and stage 10 agree
            exp_sha = s10_raw_hash if (s10_raw_hash and ev_target_hash == s10_raw_hash) else None
            exp_var = s10_var if (s10_var and ev_var == s10_var) else None
            exp_cnt = s10_count if (s10_count is not None and ev_count == s10_count) else None
            exp_nonfinite = s10_nonfinite if (s10_nonfinite is not None and ev_nonfinite == s10_nonfinite) else None

            try:
                validate_signal_integrity_evidence(
                    signal_integrity_evidence,
                    expected_source_sha256=exp_sha,
                    expected_target_channel=exp_var,
                    expected_sample_count=exp_cnt,
                    expected_nonfinite_count=exp_nonfinite,
                )
                sig_fs = signal_integrity_evidence.get("full_scale_limit")
                sig_units = signal_integrity_evidence.get("full_scale_units")
                evidence_fs = evidence_copy.get("sensor_identity", {}).get("full_scale_limit_physical", {})
                evidence_fs = evidence_fs.get("resolved_value") if isinstance(evidence_fs, dict) else None
                expected_unit = evidence_copy.get("acquisition_signal_interpretation", {}).get(
                    "calibrated_acceleration_unit", {}
                )
                expected_unit = expected_unit.get("resolved_value") if isinstance(expected_unit, dict) else None
                if evidence_fs is not None and sig_fs is not None and sig_fs != evidence_fs:
                    raise ContractValidationError(
                        "full_scale_limit",
                        f"Signal full_scale_limit ({sig_fs}) does not match authenticated sensor full scale ({evidence_fs}).",
                        sig_fs,
                    )
                if expected_unit is not None and sig_units != expected_unit:
                    raise ContractValidationError(
                        "full_scale_units",
                        f"Signal full_scale_units ({sig_units!r}) does not match authenticated calibrated units ({expected_unit!r}).",
                        sig_units,
                    )
            except ContractValidationError as exc:
                err_msg = str(exc)
                if gate_reached == "SIGNAL_INTEGRITY" and (
                    signal_integrity_evidence is None
                    or "SOURCE_BOUND_REJECTION" in diagnostic_reason
                ):
                    if signal_integrity_evidence is None:
                        final_status = "BLOCKED_SIGNAL_INTEGRITY"
                        gate_reached = "SIGNAL_INTEGRITY"
                    # Preserve the source-bound workflow diagnostic already established.
                    pass
                elif exc.field == "data_quality_provenance" and "tampered" in err_msg.lower():
                    final_status = "BLOCKED_SIGNAL_INTEGRITY"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"SIGNAL_INTEGRITY_TAMPER_DETECTED: {err_msg}"
                elif exc.field == "source_sha256":
                    final_status = "BLOCKED_PROVENANCE"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"SIGNAL_INTEGRITY_DIGEST_MISMATCH: {err_msg}"
                elif exc.field in ("target_channel", "sample_count"):
                    final_status = "BLOCKED_PROVENANCE"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"SIGNAL_INTEGRITY_TARGET_MISMATCH: {err_msg}"
                elif exc.field == "data_quality_provenance" and "does not match" in err_msg:
                    final_status = "BLOCKED_PROVENANCE"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"SIGNAL_INTEGRITY_PROVENANCE_MISMATCH: {err_msg}"
                elif exc.field == "full_scale_limit" or "full_scale_limit" in err_msg:
                    final_status = "BLOCKED_UNKNOWN_METADATA"
                    gate_reached = "PHYSICAL_METADATA"
                    diagnostic_reason = f"FULL_SCALE_UNVERIFIED: {err_msg}"
                elif exc.field == "clipping_ratio" and signal_integrity_evidence.get("longest_contiguous_rail_run", 0) >= 3:
                    final_status = "BLOCKED_SIGNAL_INTEGRITY"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = "SUSTAINED_RAIL_SATURATION: Waveform contains >= 3 contiguous rail samples; physical saturation detected."
                else:
                    final_status = "BLOCKED_SIGNAL_INTEGRITY"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"SIGNAL_INTEGRITY_CONTRACT_INVALID: {err_msg}"

            if gate_reached == "ADMISSION":
                sig_status = signal_integrity_evidence.get("verification_status")
                sig_nonfinite = signal_integrity_evidence.get("nonfinite_sample_count", 0)
                longest_run = signal_integrity_evidence.get("longest_contiguous_rail_run", 0)
                clipping_ratio = signal_integrity_evidence.get("clipping_ratio", 0.0)

                if sig_status == "NONFINITE_DETECTED" or sig_nonfinite > 0:
                    final_status = "BLOCKED_SIGNAL_INTEGRITY"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"NONFINITE_VALUES_PRESENT: Waveform contains {sig_nonfinite} non-finite samples."
                elif sig_status == "SUSTAINED_RAIL_SATURATION" or (longest_run is not None and longest_run >= 3):
                    final_status = "BLOCKED_SIGNAL_INTEGRITY"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = "SUSTAINED_RAIL_SATURATION: Waveform contains >= 3 contiguous rail samples; physical saturation detected."
                elif sig_status == "EXCESSIVE_CLIPPING" or (clipping_ratio is not None and clipping_ratio > 0.0001):
                    final_status = "BLOCKED_SIGNAL_INTEGRITY"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"EXCESSIVE_CLIPPING_RATIO: Waveform clipping ratio {clipping_ratio:.6f} exceeds 0.01% allowance."
                elif sig_status == "UNKNOWN_FULL_SCALE":
                    final_status = "BLOCKED_UNKNOWN_METADATA"
                    gate_reached = "PHYSICAL_METADATA"
                    diagnostic_reason = "FULL_SCALE_UNVERIFIED: Full scale limit unknown; clipping cannot be assessed."
                elif sig_status != "PASS":
                    final_status = "BLOCKED_SIGNAL_INTEGRITY"
                    gate_reached = "SIGNAL_INTEGRITY"
                    diagnostic_reason = f"UNKNOWN_SIGNAL_INTEGRITY_STATUS: Verification status '{sig_status}' is unrecognized."

    # ==========================================================================
    # PRECEDENCE GATE 3: USAGE PERMISSIONS (Legal Grant for Intended Use)
    # ==========================================================================
    legal_prov = evidence_copy.get("dataset_legal_provenance", {})
    if gate_reached == "ADMISSION":
        if intended_use in ("COMMERCIAL_R_AND_D", "INTERNAL_TESTING"):
            comm_grant = legal_prov.get("affirmative_commercial_grant", {})
            ref = comm_grant.get("evidence_reference") if isinstance(comm_grant, dict) else None
            is_auth = registry.is_authenticated(ref, "LEGAL_GRANT")
            if not (isinstance(comm_grant, dict) and
                    comm_grant.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                    comm_grant.get("evidence_level") in (1, 2) and
                    comm_grant.get("resolved_value") is True and
                    is_auth):
                final_status = "BLOCKED_USAGE_RESTRICTIONS"
                gate_reached = "USAGE_PERMISSIONS"
                diagnostic_reason = (
                    f"COMMERCIAL_GRANT_UNVERIFIED: Operational context is '{intended_use}' "
                    "but affirmative commercial grant lacks authenticated Tier 1/2 evidence."
                )

        elif intended_use == "DATASET_REDISTRIBUTION":
            redist_grant = legal_prov.get("redistribution_permitted", {})
            ref = redist_grant.get("evidence_reference") if isinstance(redist_grant, dict) else None
            is_auth = registry.is_authenticated(ref, "LEGAL_GRANT")
            if not (isinstance(redist_grant, dict) and
                    redist_grant.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                    redist_grant.get("resolved_value") is True and
                    is_auth):
                final_status = "BLOCKED_USAGE_RESTRICTIONS"
                gate_reached = "USAGE_PERMISSIONS"
                diagnostic_reason = (
                    "REDISTRIBUTION_GRANT_UNVERIFIED: Operational context is 'DATASET_REDISTRIBUTION' "
                    "but dataset redistribution lacks authenticated affirmative authorization."
                )

        elif intended_use in ("ACADEMIC_RESEARCH_NONCOMMERCIAL", "ARTIFACT_PUBLICATION"):
            res_grant = legal_prov.get("affirmative_research_grant", {})
            ref = res_grant.get("evidence_reference") if isinstance(res_grant, dict) else None
            is_auth = registry.is_authenticated(ref, "LEGAL_GRANT")
            if not (isinstance(res_grant, dict) and
                    res_grant.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                    res_grant.get("resolved_value") is True and
                    is_auth):
                final_status = "BLOCKED_USAGE_RESTRICTIONS"
                gate_reached = "USAGE_PERMISSIONS"
                diagnostic_reason = (
                    f"RESEARCH_GRANT_UNVERIFIED: Operational context is '{intended_use}' "
                    "but affirmative research grant lacks authenticated evidence."
                )

    # ==========================================================================
    # PRECEDENCE GATE 4: SOURCE PROVENANCE (SHA-256 Digest & Variable Identity)
    # ==========================================================================
    if gate_reached == "ADMISSION":
        ev_target_hash = evidence_copy.get("target_source_sha256")
        ev_prov_hash = legal_prov.get("source_raw_byte_sha256")
        s10_raw_hash = stage10_copy.get("source_identity", {}).get("raw_byte_sha256")

        # 4a. Target vs Provenance equality
        if ev_target_hash != ev_prov_hash:
            final_status = "BLOCKED_PROVENANCE"
            gate_reached = "SOURCE_PROVENANCE"
            diagnostic_reason = f"PROVENANCE_HASH_MISMATCH: target_source_sha256 ({ev_target_hash}) != legal_provenance.source_raw_byte_sha256 ({ev_prov_hash})"

        # 4b. Target vs Stage 10 raw digest equality
        elif ev_target_hash != s10_raw_hash:
            final_status = "BLOCKED_PROVENANCE"
            gate_reached = "SOURCE_PROVENANCE"
            diagnostic_reason = f"STAGE10_DIGEST_MISMATCH: target_source_sha256 ({ev_target_hash}) != Stage 10 raw_byte_sha256 ({s10_raw_hash})"

        # 4c. Variable identity equality
        else:
            ev_var = evidence_copy.get("target_variable_name")
            s10_var = stage10_copy.get("container_facts", {}).get("target_variable")
            acq_var = evidence_copy.get("acquisition_signal_interpretation", {}).get("channel_identity", {}).get("resolved_value")
            if ev_var != s10_var or (acq_var and ev_var != acq_var):
                final_status = "BLOCKED_PROVENANCE"
                gate_reached = "SOURCE_PROVENANCE"
                diagnostic_reason = f"VARIABLE_TARGET_MISMATCH: target_variable_name '{ev_var}' != Stage 10 target_variable '{s10_var}'"

            # 4d. Sample count equality
            else:
                ev_count = evidence_copy.get("acquisition_signal_interpretation", {}).get("sample_count")
                s10_count = stage10_copy.get("container_facts", {}).get("sample_count")
                if ev_count != s10_count:
                    final_status = "BLOCKED_PROVENANCE"
                    gate_reached = "SOURCE_PROVENANCE"
                    diagnostic_reason = f"SAMPLE_COUNT_MISMATCH: Evidence sample_count ({ev_count}) != Stage 10 sample_count ({s10_count})"

                # 4e. Publisher verification
                else:
                    pub_entry = legal_prov.get("publisher_entity", {})
                    ref = pub_entry.get("evidence_reference") if isinstance(pub_entry, dict) else None
                    if not (isinstance(pub_entry, dict) and
                            pub_entry.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                            registry.is_authenticated(ref, "PUBLISHER_IDENTITY")):
                        final_status = "BLOCKED_PROVENANCE"
                        gate_reached = "SOURCE_PROVENANCE"
                        diagnostic_reason = "PUBLISHER_IDENTITY_UNVERIFIED: Publisher entity reference lacks authenticated registry record."

    # ==========================================================================
    # PRECEDENCE GATES 5 & 6: REQUIRED PHYSICAL METADATA & CONFLICTING EVIDENCE
    # ==========================================================================
    acq = evidence_copy.get("acquisition_signal_interpretation", {})
    sensor = evidence_copy.get("sensor_identity", {})

    if gate_reached == "ADMISSION":
        conflicting_fields = []
        for tree_name, tree in (
            ("sensor_identity", sensor),
            ("acquisition_signal_interpretation", acq),
            ("dataset_legal_provenance", legal_prov),
            ("experiment_specimen_identity", evidence_copy.get("experiment_specimen_identity", {})),
        ):
            for k, val in tree.items():
                if isinstance(val, dict) and val.get("status") == "CONFLICTING_EVIDENCE":
                    conflicting_fields.append(f"{tree_name}.{k}")

        if conflicting_fields:
            final_status = "BLOCKED_CONFLICTING_EVIDENCE"
            gate_reached = "CONFLICTING_EVIDENCE"
            diagnostic_reason = f"CONFLICTING_PRIMARY_RECORDS: Irreconcilable conflict reported between primary documentation sources for {conflicting_fields}."

        # If no conflicts, check for required physical metadata completeness
        else:
            # 5a. Native sampling rate
            rate_entry = acq.get("native_sampling_frequency_hz", {})
            rate_ref = rate_entry.get("evidence_reference") if isinstance(rate_entry, dict) else None
            rate_val = rate_entry.get("resolved_value") if isinstance(rate_entry, dict) else None
            if not (isinstance(rate_entry, dict) and
                    rate_entry.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                    isinstance(rate_val, (int, float)) and rate_val > 0 and
                    registry.is_authenticated(rate_ref, "PHYSICAL_METADATA")):
                final_status = "BLOCKED_UNKNOWN_METADATA"
                gate_reached = "PHYSICAL_METADATA"
                diagnostic_reason = "SAMPLING_RATE_UNVERIFIED: Native sampling frequency lacks authenticated positive resolved value."

            # 5b. Calibrated acceleration units
            else:
                unit_entry = acq.get("calibrated_acceleration_unit", {})
                unit_ref = unit_entry.get("evidence_reference") if isinstance(unit_entry, dict) else None
                unit_val = unit_entry.get("resolved_value") if isinstance(unit_entry, dict) else None
                if not (isinstance(unit_entry, dict) and
                        unit_entry.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                        unit_val in ("g", "m/s^2") and
                        registry.is_authenticated(unit_ref, "PHYSICAL_METADATA")):
                    final_status = "BLOCKED_UNKNOWN_METADATA"
                    gate_reached = "PHYSICAL_METADATA"
                    diagnostic_reason = "UNITS_UNVERIFIED: Calibrated acceleration unit lacks authenticated resolved value ('g' or 'm/s^2')."

                # 5c. Full scale limit
                else:
                    fs_entry = sensor.get("full_scale_limit_physical", {})
                    fs_ref = fs_entry.get("evidence_reference") if isinstance(fs_entry, dict) else None
                    fs_val = fs_entry.get("resolved_value") if isinstance(fs_entry, dict) else None
                    if not (isinstance(fs_entry, dict) and
                            fs_entry.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                            isinstance(fs_val, (int, float)) and fs_val > 0 and
                            registry.is_authenticated(fs_ref, "PHYSICAL_METADATA")):
                        final_status = "BLOCKED_UNKNOWN_METADATA"
                        gate_reached = "PHYSICAL_METADATA"
                        diagnostic_reason = "FULL_SCALE_UNVERIFIED: Transducer physical full-scale limit lacks authenticated positive resolved value."

                    # 5d. Transducer sensitivity
                    else:
                        sens_entry = sensor.get("sensitivity", {})
                        sens_ref = sens_entry.get("evidence_reference") if isinstance(sens_entry, dict) else None
                        sens_val = sens_entry.get("resolved_value") if isinstance(sens_entry, dict) else None
                        if not (isinstance(sens_entry, dict) and
                                sens_entry.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                                isinstance(sens_val, (int, float)) and sens_val > 0 and
                                registry.is_authenticated(sens_ref, "PHYSICAL_METADATA")):
                            final_status = "BLOCKED_UNKNOWN_METADATA"
                            gate_reached = "PHYSICAL_METADATA"
                            diagnostic_reason = "SENSITIVITY_UNVERIFIED: Transducer sensitivity lacks authenticated positive resolved value."

    # ==========================================================================
    # PRECEDENCE GATE 7: CALIBRATION VALIDITY (Dimensional Math & Transfer Function)
    # ==========================================================================
    if gate_reached == "ADMISSION":
        is_cal_valid, cal_fail_status, cal_diag = verify_calibration_conversion_chain(sensor, acq, registry)
        if not is_cal_valid:
            final_status = cal_fail_status or "BLOCKED_CALIBRATION"
            gate_reached = "CALIBRATION_VALIDITY"
            diagnostic_reason = cal_diag or "DIMENSIONAL_MATH_ERROR: Invalid calibration transfer function or dimensional mismatch."

    # ==========================================================================
    # DIAGNOSTIC EVALUATION ELIGIBILITY GATE (Decoupled from Physical Measurement)
    # ==========================================================================
    specimen = evidence_copy.get("experiment_specimen_identity", {})
    spec_proven = specimen.get("specimen_distinctness_proven", {})
    spec_ref = spec_proven.get("evidence_reference") if isinstance(spec_proven, dict) else None
    spec_auth = registry.is_authenticated(spec_ref, "SPECIMEN_DISTINCTNESS")

    fault_cond = specimen.get("fault_condition", {})
    label_auth = fault_cond.get("ground_truth_label_authority", {})
    label_ref = label_auth.get("evidence_reference") if isinstance(label_auth, dict) else None
    label_auth_ok = registry.is_authenticated(label_ref, "GROUND_TRUTH_AUTHORITY")

    precautionary_specimen_group = (
        stage10_copy.get("local_annotations", {}).get("precautionary_specimen_group")
        or specimen.get("physical_specimen_id", {}).get("resolved_value")
    )

    if final_status != "ADMITTED_FOR_MEASUREMENT":
        diag_eligibility = {
            "eligible": False,
            "status": "PRECONDITION_FAILED",
            "specimen_distinctness_proven": False,
            "ground_truth_fault_authority_verified": False,
            "precautionary_specimen_group": precautionary_specimen_group,
            "reasons": [
                f"Physical measurement admission blocked at gate '{gate_reached}' with status '{final_status}'."
            ],
        }
    else:
        # Check specimen distinctness
        if not (isinstance(spec_proven, dict) and
                spec_proven.get("status") == "DOCUMENTED_KNOWN" and
                spec_proven.get("evidence_level") in (1, 2) and
                spec_proven.get("resolved_value") is True and
                spec_auth):
            diag_eligibility = {
                "eligible": False,
                "status": "BLOCKED_AMBIGUOUS_SPECIMEN",
                "specimen_distinctness_proven": False,
                "ground_truth_fault_authority_verified": bool(label_auth_ok),
                "precautionary_specimen_group": precautionary_specimen_group,
                "reasons": [
                    "Physical specimen distinctness across load variations is unproven in primary sources; "
                    "recording cannot be used to evaluate cross-load generalization."
                ],
            }
        # Check ground-truth authority
        elif not (isinstance(label_auth, dict) and
                  label_auth.get("status") in ("DOCUMENTED_KNOWN", "DERIVED_KNOWN") and
                  label_auth.get("evidence_level") in (1, 2) and
                  label_auth.get("resolved_value") is not None and
                  label_auth_ok):
            diag_eligibility = {
                "eligible": False,
                "status": "BLOCKED_UNVERIFIED_FAULT_LABEL",
                "specimen_distinctness_proven": True,
                "ground_truth_fault_authority_verified": False,
                "precautionary_specimen_group": precautionary_specimen_group,
                "reasons": [
                    "Fault ground truth label authority lacks verified primary inspection evidence."
                ],
            }
        # Check explicit partition assignments and absence of cross-specimen leakage
        else:
            is_part_valid, part_status, part_reasons = verify_diagnostic_partition_leakage(
                partition_assignments=partition_assignments,
                stage10_copy=stage10_copy,
                evidence_copy=evidence_copy,
                target_specimen_group=str(precautionary_specimen_group),
            )
            diag_eligibility = {
                "eligible": is_part_valid,
                "status": part_status,
                "specimen_distinctness_proven": True,
                "ground_truth_fault_authority_verified": True,
                "precautionary_specimen_group": precautionary_specimen_group,
                "reasons": part_reasons,
            }

    # Signal integrity summary object
    sig_summary = None
    if signal_integrity_evidence is not None and isinstance(signal_integrity_evidence, dict):
        v_stat = signal_integrity_evidence.get("verification_status")
        if v_stat in ("PASS", "SUSTAINED_RAIL_SATURATION", "EXCESSIVE_CLIPPING", "NONFINITE_DETECTED", "UNKNOWN_FULL_SCALE"):
            sig_summary = {
                "sample_count": signal_integrity_evidence.get("sample_count", 0),
                "nonfinite_sample_count": signal_integrity_evidence.get("nonfinite_sample_count", 0),
                "rail_sample_count": signal_integrity_evidence.get("rail_sample_count"),
                "longest_contiguous_rail_run": signal_integrity_evidence.get("longest_contiguous_rail_run"),
                "clipping_ratio": signal_integrity_evidence.get("clipping_ratio"),
                "verification_status": v_stat,
            }

    # Generate deterministic decision ID and timestamp
    fingerprint_payload = {
        "domain": "ADI_EDGE_SENTINEL_STAGE12_ADMISSION_DECISION_V2",
        "policy_version": "stage12.admission-policy.v2",
        "evidence": evidence_copy,
        "stage10_report": stage10_copy,
        "signal_integrity_evidence": signal_integrity_evidence,
        "partition_assignments": partition_assignments,
        "registry_identity": registry.identity_digest(),
        "intended_use": intended_use,
        "evaluation_mode": "FICTIONAL_TEST_ONLY",
        "measurement_admission_status": final_status,
        "precedence_gate_reached": gate_reached,
        "diagnostic_reason": diagnostic_reason,
        "diagnostic_eligibility": diag_eligibility,
    }
    raw_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    decision_id = f"dec_adm_{raw_fingerprint[:16]}"
    decided_at = evidence_copy.get("created_at") or "2026-11-20T00:00:00Z"

    decision_record = {
        "schema_version": "1.0.0",
        "decision_id": decision_id,
        "decided_at": decided_at,
        "evidence_record_id": evidence_copy.get("evidence_record_id", "ev_rec_unknown"),
        "target_source_sha256": evidence_copy.get("target_source_sha256", ""),
        "target_variable_name": evidence_copy.get("target_variable_name", ""),
        "intended_use": intended_use,
        "evaluation_mode": "FICTIONAL_TEST_ONLY",
        "measurement_admission_status": final_status,
        "diagnostic_reason": diagnostic_reason,
        "precedence_gate_reached": gate_reached,
        "operational_execution_authorized": False,
        "diagnostic_evaluation_eligibility": diag_eligibility,
        "signal_integrity_summary": sig_summary,
    }

    # Validate output against canonical schema
    validate_measurement_admission_decision(decision_record)

    return decision_record
