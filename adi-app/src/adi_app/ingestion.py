"""Strict CSV ingestion adapter for ADI Edge Sentinel vibration recordings.

Loads local vibration CSV files with explicit caller-provided metadata into the
canonical VibrationRecordingV1 contract. Preserves source-byte integrity by
hashing raw file bytes once before decoding or newline normalization.
Enforces strict parser rules and delegates contract validation to adi-common.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Set, Union

from adi_common import ContractValidationError, validate_vibration_recording


class RecordingIngestionError(Exception):
    """Base exception for all vibration recording ingestion failures."""
    pass


class IngestionFileNotFoundError(RecordingIngestionError, FileNotFoundError):
    """Raised when the specified CSV file cannot be located or is not a regular file."""
    pass


class IngestionMetadataError(RecordingIngestionError, ValueError):
    """Raised when metadata is incomplete, contains unknown fields, or violates rules."""
    pass


class IngestionParseError(RecordingIngestionError, ValueError):
    """Raised when CSV decoding, structure, or cell content violates parser rules.

    Attributes:
        filename: Path or name of the file being parsed.
        row: 1-based row index where parse failure occurred, or None if file-level.
        column: 1-based column index where parse failure occurred, or None if row-level.
        message: Detailed explanation of the parse failure.
    """

    def __init__(
        self,
        filename: Optional[Union[str, Path]],
        row: Optional[int],
        column: Optional[Union[int, str]],
        message: str,
    ):
        self.filename = str(filename) if filename is not None else None
        self.row = row
        self.column = column
        self.message = message

        parts = []
        if self.filename:
            parts.append(f"file '{self.filename}'")
        if self.row is not None:
            parts.append(f"row {self.row}")
        if self.column is not None:
            parts.append(f"column {self.column}")
        location = f" [{', '.join(parts)}]" if parts else ""
        super().__init__(f"CSV parse error{location}: {message}")


class IngestionDigestMismatchError(RecordingIngestionError, ValueError):
    """Raised when expected_sha256 syntax is invalid or mismatches computed source byte digest."""
    pass


REQUIRED_METADATA_FIELDS: Set[str] = {
    "schema_version",
    "recording_id",
    "machine_id",
    "session_id",
    "source_id",
    "provenance",
    "sample_rate_hz",
    "acceleration_unit",
}

_ALLOWED_CHANNELS: Set[str] = {"x", "y", "z"}
_SHA256_REGEX = re.compile(r"^[0-9a-fA-F]{64}$")
_BARE_CR_REGEX = re.compile(rb"\r(?!\n)")
_ASCII_WHITESPACE = " \t"
_NON_FINITE_TOKENS: Set[str] = {
    "nan", "+nan", "-nan",
    "inf", "+inf", "-inf",
    "infinity", "+infinity", "-infinity",
}

# Strict ASCII decimal and scientific notation grammar:
# - Optional + or - sign
# - Decimal mantissa: 12, 12., 12.34, or .34
# - Optional exponent: e/E with optional sign and at least one ASCII digit
_NUMERIC_PATTERN = re.compile(
    r"^[+-]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][+-]?[0-9]+)?$"
)


def _validate_metadata(metadata: Any) -> None:
    """Validate that caller metadata contains exactly required keys and valid types."""
    if not isinstance(metadata, dict):
        raise IngestionMetadataError(f"Metadata must be a dictionary, got {type(metadata).__name__}")

    metadata_keys = set(metadata.keys())
    missing = REQUIRED_METADATA_FIELDS - metadata_keys
    if missing:
        raise IngestionMetadataError(f"Missing required metadata field(s): {sorted(missing)}")

    unknown = metadata_keys - REQUIRED_METADATA_FIELDS
    if unknown:
        raise IngestionMetadataError(f"Unknown metadata field(s): {sorted(unknown)}")

    if metadata["schema_version"] != "1.0.0":
        raise IngestionMetadataError(
            f"Unsupported schema_version '{metadata['schema_version']}'; expected '1.0.0'"
        )

    for id_field in ("recording_id", "machine_id", "session_id", "source_id"):
        val = metadata[id_field]
        if not isinstance(val, str) or not val.strip():
            raise IngestionMetadataError(f"Metadata field '{id_field}' must be a nonempty string")

    provenance = metadata["provenance"]
    if provenance not in ("synthetic", "recorded"):
        raise IngestionMetadataError(
            f"Invalid provenance '{provenance}'; must be 'synthetic' or 'recorded'"
        )

    rate = metadata["sample_rate_hz"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise IngestionMetadataError(
            f"Metadata field 'sample_rate_hz' must be a numeric value, got {type(rate).__name__}"
        )
    # Check integer bounds before float conversion or math.isfinite to prevent OverflowError
    if isinstance(rate, int):
        if rate > sys.float_info.max or rate < -sys.float_info.max:
            raise IngestionMetadataError(
                "Metadata field 'sample_rate_hz' is out of binary64 finite domain"
            )
    if not math.isfinite(rate) or rate <= 0:
        raise IngestionMetadataError(
            f"Metadata field 'sample_rate_hz' must be positive and finite, got {rate}"
        )
    if rate > sys.float_info.max:
        raise IngestionMetadataError(
            "Metadata field 'sample_rate_hz' exceeds binary64 float maximum"
        )

    unit = metadata["acceleration_unit"]
    if unit not in ("g", "m/s^2"):
        raise IngestionMetadataError(
            f"Invalid acceleration_unit '{unit}'; must be 'g' or 'm/s^2'"
        )


def parse_vibration_csv_bytes(
    raw_bytes: bytes,
    metadata: Dict[str, Any],
    source_path_context: Optional[Union[str, Path]] = None,
    expected_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Parse raw CSV bytes and caller metadata into a validated VibrationRecordingV1 dictionary.

    Computes SHA-256 over exact bytes, verifies optional expected digest,
    strictly parses comma-delimited rows, and calls validate_vibration_recording before returning.

    Args:
        raw_bytes: Raw byte contents of the vibration CSV.
        metadata: Explicit metadata dictionary (must provide all 8 required contract fields).
        source_path_context: Optional path or label used for descriptive error formatting.
        expected_sha256: Optional expected SHA-256 hex string to match against source bytes.

    Returns:
        Dict[str, Any]: Validated recording payload compliant with VibrationRecordingV1.

    Raises:
        IngestionMetadataError: If metadata violates required fields or constraints.
        IngestionParseError: If CSV decoding, structure, or cell content violates parser rules.
        IngestionDigestMismatchError: If expected_sha256 is invalid or does not match source bytes.
        RecordingIngestionError: Base exception for all ingestion failures.
    """
    _validate_metadata(metadata)

    if not isinstance(raw_bytes, (bytes, bytearray)):
        raise IngestionParseError(
            filename=source_path_context,
            row=None,
            column=None,
            message=f"CSV source bytes must be bytes or bytearray, got {type(raw_bytes).__name__}",
        )

    if len(raw_bytes) == 0:
        raise IngestionParseError(
            filename=source_path_context,
            row=None,
            column=None,
            message="CSV file is empty (0 bytes)",
        )

    # Explicit newline policy: reject bare CR (\r not followed by \n)
    if _BARE_CR_REGEX.search(raw_bytes):
        raise IngestionParseError(
            filename=source_path_context,
            row=None,
            column=None,
            message="Bare CR ('\\r') line endings are unsupported; use standard LF ('\\n') or CRLF ('\\r\\n')",
        )

    computed_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not _SHA256_REGEX.fullmatch(expected_sha256):
            raise IngestionDigestMismatchError(
                f"Invalid expected_sha256 syntax: '{expected_sha256}'. Expected 64 hexadecimal characters."
            )
        if expected_sha256.lower() != computed_sha256.lower():
            target_desc = f" for {source_path_context}" if source_path_context else ""
            raise IngestionDigestMismatchError(
                f"Computed SHA-256 '{computed_sha256}' does not match expected '{expected_sha256}'{target_desc}"
            )

    # Decode UTF-8 bytes (handling optional BOM)
    try:
        if raw_bytes.startswith(b"\xef\xbb\xbf"):
            text = raw_bytes.decode("utf-8-sig")
        else:
            text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestionParseError(
            filename=source_path_context,
            row=None,
            column=None,
            message=f"Invalid UTF-8 encoding: {exc}",
        ) from exc

    # Parse CSV with strict=True
    reader = csv.reader(io.StringIO(text), delimiter=",", strict=True)

    row_idx = 0
    channels: List[str] = []
    samples: List[List[float]] = []

    try:
        for row in reader:
            row_idx += 1

            # Check for blank line or empty row
            if len(row) == 0 or all(cell.strip(_ASCII_WHITESPACE) == "" for cell in row):
                raise IngestionParseError(
                    filename=source_path_context,
                    row=row_idx,
                    column=None,
                    message=f"Blank line or empty row detected at line {row_idx}; blank lines are forbidden",
                )

            # Header row (row 1)
            if row_idx == 1:
                if len(row) == 0:
                    raise IngestionParseError(
                        filename=source_path_context, row=1, column=None, message="Empty header row"
                    )
                for c_idx, col in enumerate(row):
                    col_num = c_idx + 1
                    if not col:
                        raise IngestionParseError(
                            filename=source_path_context, row=1, column=col_num, message="Empty channel name in header"
                        )
                    if "\n" in col or "\r" in col:
                        raise IngestionParseError(
                            filename=source_path_context,
                            row=1,
                            column=col_num,
                            message=f"Embedded newline detected inside header field '{col}'",
                        )
                    if col != col.strip(_ASCII_WHITESPACE):
                        raise IngestionParseError(
                            filename=source_path_context,
                            row=1,
                            column=col_num,
                            message=f"Channel name '{col}' contains forbidden whitespace",
                        )
                    if col not in _ALLOWED_CHANNELS:
                        raise IngestionParseError(
                            filename=source_path_context,
                            row=1,
                            column=col_num,
                            message=f"Invalid channel name '{col}'; must be unique subset of ('x', 'y', 'z')",
                        )
                    if col in channels:
                        raise IngestionParseError(
                            filename=source_path_context,
                            row=1,
                            column=col_num,
                            message=f"Duplicate channel name '{col}' in CSV header",
                        )
                    channels.append(col)

                if len(channels) < 1 or len(channels) > 3:
                    raise IngestionParseError(
                        filename=source_path_context,
                        row=1,
                        column=None,
                        message=f"Header must specify 1 to 3 channels, got {len(channels)}",
                    )
                continue

            # Data rows (row 2, 3, ...)
            if len(row) != len(channels):
                raise IngestionParseError(
                    filename=source_path_context,
                    row=row_idx,
                    column=None,
                    message=f"Row has {len(row)} columns; expected {len(channels)}",
                )

            row_samples: List[float] = []
            for c_idx, cell in enumerate(row):
                col_num = c_idx + 1

                # Embedded newlines inside quoted cell are strictly rejected
                if "\n" in cell or "\r" in cell:
                    raise IngestionParseError(
                        filename=source_path_context,
                        row=row_idx,
                        column=col_num,
                        message=f"Embedded newline detected inside quoted cell in row {row_idx}, column {col_num}",
                    )

                # Trim ONLY ASCII spaces and tabs
                token = cell.strip(_ASCII_WHITESPACE)
                if not token:
                    raise IngestionParseError(
                        filename=source_path_context,
                        row=row_idx,
                        column=col_num,
                        message=f"Empty cell detected in row {row_idx}, column {col_num} ('{channels[c_idx]}')",
                    )

                # Check for explicit non-finite tokens (NaN, Infinity)
                if token.lower() in _NON_FINITE_TOKENS:
                    raise IngestionParseError(
                        filename=source_path_context,
                        row=row_idx,
                        column=col_num,
                        message=f"Non-finite numeric value '{cell}' in row {row_idx}, column {col_num} ('{channels[c_idx]}')",
                    )

                # Strict numeric grammar check
                if not _NUMERIC_PATTERN.fullmatch(token):
                    raise IngestionParseError(
                        filename=source_path_context,
                        row=row_idx,
                        column=col_num,
                        message=f"Invalid decimal numeric syntax '{cell}' in row {row_idx}, column {col_num} ('{channels[c_idx]}')",
                    )

                try:
                    val = float(token)
                except ValueError as exc:
                    raise IngestionParseError(
                        filename=source_path_context,
                        row=row_idx,
                        column=col_num,
                        message=f"Invalid decimal numeric syntax '{cell}' in row {row_idx}, column {col_num} ('{channels[c_idx]}')",
                    ) from exc

                if not math.isfinite(val):
                    raise IngestionParseError(
                        filename=source_path_context,
                        row=row_idx,
                        column=col_num,
                        message=f"Numeric value '{cell}' overflows binary64 domain in row {row_idx}, column {col_num}",
                    )

                if val < -sys.float_info.max or val > sys.float_info.max:
                    raise IngestionParseError(
                        filename=source_path_context,
                        row=row_idx,
                        column=col_num,
                        message=f"Numeric value '{cell}' out of binary64 domain in row {row_idx}, column {col_num}",
                    )

                row_samples.append(val)
            samples.append(row_samples)

    except csv.Error as exc:
        line_num = getattr(reader, "line_num", None)
        raise IngestionParseError(
            filename=source_path_context,
            row=line_num if line_num is not None and line_num > 0 else (row_idx + 1),
            column=None,
            message=f"CSV parser error: {exc}",
        ) from exc

    if len(samples) == 0:
        raise IngestionParseError(
            filename=source_path_context,
            row=None,
            column=None,
            message="CSV contains header but no data rows; at least one data row is required",
        )

    recording: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "recording_id": metadata["recording_id"],
        "machine_id": metadata["machine_id"],
        "session_id": metadata["session_id"],
        "provenance": metadata["provenance"],
        "sample_rate_hz": float(metadata["sample_rate_hz"]),
        "acceleration_unit": metadata["acceleration_unit"],
        "channels": channels,
        "samples": samples,
        "source_id": metadata["source_id"],
        "source_sha256": computed_sha256,
    }

    try:
        return validate_vibration_recording(recording)
    except ContractValidationError as exc:
        raise IngestionMetadataError(f"Contract validation failed for recording: {exc}") from exc


def load_vibration_csv(
    csv_path: Union[str, Path],
    metadata: Dict[str, Any],
    expected_sha256: Optional[str] = None,
) -> Dict[str, Any]:
    """Load local vibration CSV and caller metadata into a validated VibrationRecordingV1 dictionary.

    Reads raw bytes once, computes SHA-256 over exact bytes, verifies optional expected digest,
    strictly parses comma-delimited rows, and calls validate_vibration_recording before returning.

    Args:
        csv_path: Local filesystem path to vibration CSV file.
        metadata: Explicit metadata dictionary (must provide all 8 required contract fields).
        expected_sha256: Optional expected SHA-256 hex string to match against source bytes.

    Returns:
        Dict[str, Any]: Validated recording payload compliant with VibrationRecordingV1.

    Raises:
        IngestionFileNotFoundError: If csv_path does not exist or is not a regular file.
        IngestionMetadataError: If metadata violates required fields or constraints.
        IngestionParseError: If CSV decoding, structure, or cell content violates parser rules.
        IngestionDigestMismatchError: If expected_sha256 is invalid or does not match source bytes.
        RecordingIngestionError: Base exception for all ingestion failures.
    """
    _validate_metadata(metadata)

    path = Path(csv_path)
    if not path.exists():
        raise IngestionFileNotFoundError(f"CSV file not found: {path}")
    if not path.is_file():
        raise IngestionFileNotFoundError(f"CSV path is not a regular file: {path}")

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise IngestionFileNotFoundError(f"Failed to read CSV file {path}: {exc}") from exc

    return parse_vibration_csv_bytes(
        raw_bytes=raw_bytes,
        metadata=metadata,
        source_path_context=path,
        expected_sha256=expected_sha256,
    )
