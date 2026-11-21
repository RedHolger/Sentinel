"""Bounded, deterministic offline source inspection for local MATLAB containers.

Implements structural preflight, resource bounds, and safe numeric decoding
for ExploratorySourceInspectionV1 reports.
"""

from __future__ import annotations

import argparse
import datetime
import errno
import hashlib
import io
import json
import math
import os
from pathlib import Path
import selectors
import stat
import struct
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import scipy.io

from adi_common import ContractValidationError, validate_exploratory_source_inspection

# Operational and Resource Limits
MAX_SNAPSHOT_BYTES: int = 50_000_000  # 50 MB
MAX_VARIABLES: int = 1024
MAX_VARIABLE_NAME_BYTES: int = 256
MAX_SELECTED_ELEMENTS: int = 10_000_000
MAX_WORKER_IPC_STDOUT: int = 2_000_000  # 2 MB
MAX_WORKER_IPC_STDERR: int = 1_000_000  # 1 MB
DEFAULT_TIMEOUT_SECONDS: float = 10.0
INSPECTION_POLICY_VERSION: str = "v1"

# MATLAB MAT Constants
MAT_HEADER_SIZE: int = 128
HDF5_SIGNATURE: bytes = b"\x89HDF\r\n\x1a\n"

# Element Data Types
MI_INT8 = 1
MI_UINT8 = 2
MI_INT16 = 3
MI_UINT16 = 4
MI_INT32 = 5
MI_UINT32 = 6
MI_SINGLE = 7
MI_DOUBLE = 9
MI_INT64 = 12
MI_UINT64 = 13
MI_MATRIX = 14
MI_COMPRESSED = 15
MI_UTF8 = 16
MI_UTF16 = 17
MI_UTF32 = 18

# Array Classes
MX_CELL_CLASS = 1
MX_STRUCT_CLASS = 2
MX_OBJECT_CLASS = 3
MX_CHAR_CLASS = 4
MX_SPARSE_CLASS = 5
MX_DOUBLE_CLASS = 6
MX_SINGLE_CLASS = 7

# Status to Readiness Mapping
_STATUS_TO_READINESS = {
    "STRUCTURAL_SUCCESS": "BLOCKED_UNKNOWN_METADATA",
    "NONFINITE_VALUES_DETECTED": "BLOCKED_NONFINITE_VALUES",
    "TARGET_VARIABLE_NOT_FOUND": "BLOCKED_TARGET_NOT_FOUND",
    "MALFORMED_CONTAINER": "BLOCKED_CORRUPTED_FILE",
    "CORRUPTED_RAW_BYTES": "BLOCKED_CORRUPTED_FILE",
    "UNSUPPORTED_STRUCTURE": "BLOCKED_UNSUPPORTED_FORMAT",
    "RESOURCE_LIMIT_EXCEEDED": "BLOCKED_RESOURCE_LIMIT",
}


class SourceInspectionError(Exception):
    """Raised for input, I/O, argument, or operational snapshot refusal errors."""
    pass


def _unpack_mat_tag(data: bytes, pos: int, endian: str) -> Tuple[int, int, int, int]:
    """Unpack a MAT element tag at pos.

    Supports both standard format (8-byte tag) and small data element format.

    Returns:
        (tag_type, tag_length, data_offset, next_tag_pos)
    """
    if pos + 8 > len(data):
        raise ValueError(f"Truncated tag header at offset {pos} (data length {len(data)})")
    w1, w2 = struct.unpack(endian + "II", data[pos : pos + 8])
    if (w1 >> 16) != 0:
        # Small data element format: lower 16 bits = type, upper 16 bits = length (<= 4)
        tag_type = w1 & 0xFFFF
        tag_len = w1 >> 16
        if tag_len > 4:
            raise ValueError(f"Invalid small data element length {tag_len} > 4")
        data_offset = pos + 4
        next_pos = pos + 8
    else:
        # Standard format
        tag_type = w1
        tag_len = w2
        data_offset = pos + 8
        padded_len = ((tag_len + 7) // 8) * 8
        next_pos = pos + 8 + padded_len
    return tag_type, tag_len, data_offset, next_pos


def _run_structural_preflight(
    raw_bytes: bytes, target_var: str
) -> Dict[str, Any]:
    """Perform bounded structural preflight over raw MAT container bytes.

    Validates entire container structure, header, tags, class flags, dimensions,
    variable count, and names before any array allocation or SciPy numeric decoding.
    """
    if len(raw_bytes) < MAT_HEADER_SIZE:
        if len(raw_bytes) >= len(HDF5_SIGNATURE) and raw_bytes[: len(HDF5_SIGNATURE)] == HDF5_SIGNATURE:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V7_3_HDF5",
                "available_variables": [],
                "notes": ["MATLAB v7.3 HDF5 container format is unsupported."],
            }
        if len(raw_bytes) >= 20:
            mopt = struct.unpack("<I", raw_bytes[:4])[0]
            if mopt in (0, 1000, 1, 1001, 2, 1002, 3, 1003, 4, 1004):
                return {
                    "status": "UNSUPPORTED_STRUCTURE",
                    "container_format": "MATLAB_V4",
                    "available_variables": [],
                    "notes": ["MATLAB v4 container format is unsupported."],
                }
        return {
            "status": "MALFORMED_CONTAINER",
            "container_format": "UNRECOGNIZED",
            "available_variables": [],
            "notes": [f"Raw container size ({len(raw_bytes)} bytes) is smaller than MAT Level 5 header (128 bytes)."],
        }

    # Check for HDF5 / v7.3 signature
    if raw_bytes[: len(HDF5_SIGNATURE)] == HDF5_SIGNATURE:
        return {
            "status": "UNSUPPORTED_STRUCTURE",
            "container_format": "MATLAB_V7_3_HDF5",
            "available_variables": [],
            "notes": ["MATLAB v7.3 HDF5 container format is unsupported."],
        }

    # Check Endian Indicator (bytes 126-127)
    endian_indicator = raw_bytes[126:128]
    if endian_indicator == b"IM":
        endian = "<"
    elif endian_indicator == b"MI":
        endian = ">"
    else:
        # Check if v4
        mopt = struct.unpack("<I", raw_bytes[:4])[0]
        if mopt in (0, 1000, 1, 1001, 2, 1002, 3, 1003, 4, 1004):
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V4",
                "available_variables": [],
                "notes": ["MATLAB v4 container format is unsupported."],
            }
        return {
            "status": "MALFORMED_CONTAINER",
            "container_format": "UNRECOGNIZED",
            "available_variables": [],
            "notes": [f"Invalid endian indicator {endian_indicator!r} in MAT header."],
        }

    # Check Version (bytes 124-125)
    version = struct.unpack(endian + "H", raw_bytes[124:126])[0]
    if version != 0x0100:
        return {
            "status": "MALFORMED_CONTAINER",
            "container_format": "UNRECOGNIZED",
            "available_variables": [],
            "notes": [f"Unsupported MAT version field 0x{version:04x}; expected 0x0100."],
        }

    pos = MAT_HEADER_SIZE
    total_len = len(raw_bytes)
    available_variables: List[str] = []
    seen_names: set[str] = set()
    target_info: Optional[Dict[str, Any]] = None
    total_declared_payload: int = 0

    while pos < total_len:
        if pos + 8 > total_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Truncated MAT element tag header at offset {pos}."],
            }

        try:
            tag_type, tag_len, data_offset, next_pos = _unpack_mat_tag(raw_bytes, pos, endian)
        except Exception as exc:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Failed to decode tag header at offset {pos}: {exc}"],
            }

        # Check for compressed element
        if tag_type == MI_COMPRESSED:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Compressed MAT elements (miCOMPRESSED=15) are unsupported in initial uncompressed Level 5 inspection."],
            }

        if data_offset + tag_len > total_len or next_pos > total_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Truncated element data or padding at offset {pos} (declared length {tag_len})."],
            }

        # Top-level elements must be miMATRIX (14)
        if tag_type != MI_MATRIX:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Top-level element type {tag_type} is unsupported; expected miMATRIX (14)."],
            }

        # Preflight inside miMATRIX
        mat_bytes = raw_bytes[data_offset : data_offset + tag_len]
        sub_pos = 0
        mat_len = len(mat_bytes)

        # 1. Array Flags
        if sub_pos + 8 > mat_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Truncated array flags sub-element in miMATRIX."],
            }
        try:
            stype, slen, sdata_off, snext_pos = _unpack_mat_tag(mat_bytes, sub_pos, endian)
        except Exception as exc:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Failed to unpack array flags sub-element: {exc}"],
            }

        if sdata_off + slen > mat_len or snext_pos > mat_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Truncated array flags data or padding in miMATRIX."],
            }

        if stype != MI_UINT32 or slen != 8:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Array flags sub-element must be miUINT32 with exactly 8 bytes data."],
            }
        flags_data = mat_bytes[sdata_off : sdata_off + slen]
        flags, nzmax = struct.unpack(endian + "II", flags_data[:8])
        arr_class = flags & 0xFF
        is_logical = bool(flags & 0x0200)
        is_global = bool(flags & 0x0400)
        is_complex = bool(flags & 0x0800)

        if is_complex:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Complex numeric arrays are unsupported."],
            }
        if is_logical:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Logical arrays are unsupported."],
            }
        if is_global:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Global arrays are unsupported."],
            }
        if nzmax != 0:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Non-zero nzmax ({nzmax}) is unsupported for dense numeric arrays."],
            }
        if arr_class == MX_CELL_CLASS:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Cell arrays are unsupported."],
            }
        if arr_class == MX_STRUCT_CLASS:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Structure arrays are unsupported."],
            }
        if arr_class == MX_OBJECT_CLASS:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Object arrays are unsupported."],
            }
        if arr_class == MX_CHAR_CLASS:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Character arrays are unsupported."],
            }
        if arr_class == MX_SPARSE_CLASS:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Sparse arrays are unsupported."],
            }
        if arr_class not in (MX_DOUBLE_CLASS, MX_SINGLE_CLASS):
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Unsupported array numeric class {arr_class}; only real float32/float64 vectors supported."],
            }
        sub_pos = snext_pos

        # 2. Dimensions Array
        if sub_pos + 8 > mat_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Truncated dimensions sub-element in miMATRIX."],
            }
        try:
            stype, slen, sdata_off, snext_pos = _unpack_mat_tag(mat_bytes, sub_pos, endian)
        except Exception as exc:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Failed to unpack dimensions sub-element: {exc}"],
            }

        if sdata_off + slen > mat_len or snext_pos > mat_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Truncated dimensions data or padding in miMATRIX."],
            }

        if stype != MI_INT32:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Dimensions sub-element must be miINT32."],
            }
        num_dims = slen // 4
        if num_dims != 2 or slen != 8:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Unsupported array rank {num_dims}; only 2D row or column vectors supported."],
            }
        dim_data = mat_bytes[sdata_off : sdata_off + slen]
        dims = struct.unpack(endian + "2i", dim_data[:8])
        d0, d1 = dims
        if d0 <= 0 or d1 <= 0:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Invalid array dimensions: {dims}."],
            }
        if d0 > 1 and d1 > 1:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Higher-dimensional matrices {dims} are unsupported; only 2D row or column vectors supported."],
            }

        # Checked arithmetic bounds
        sample_count = d0 * d1
        if sample_count > MAX_SELECTED_ELEMENTS:
            return {
                "status": "RESOURCE_LIMIT_EXCEEDED",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Declared dimensions {dims} ({sample_count} elements) exceed {MAX_SELECTED_ELEMENTS} element limit."],
            }
        elem_size = 8 if arr_class == MX_DOUBLE_CLASS else 4
        declared_bytes = sample_count * elem_size
        if declared_bytes > MAX_SNAPSHOT_BYTES:
            return {
                "status": "RESOURCE_LIMIT_EXCEEDED",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Declared array payload ({declared_bytes} bytes) exceeds {MAX_SNAPSHOT_BYTES} byte snapshot cap."],
            }
        total_declared_payload += declared_bytes
        if total_declared_payload > MAX_SNAPSHOT_BYTES:
            return {
                "status": "RESOURCE_LIMIT_EXCEEDED",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Aggregate declared array payload ({total_declared_payload} bytes) exceeds {MAX_SNAPSHOT_BYTES} byte snapshot cap."],
            }
        sub_pos = snext_pos

        # 3. Array Name
        if sub_pos + 8 > mat_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Truncated array name sub-element in miMATRIX."],
            }
        try:
            stype, slen, sdata_off, snext_pos = _unpack_mat_tag(mat_bytes, sub_pos, endian)
        except Exception as exc:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Failed to unpack array name sub-element: {exc}"],
            }

        if stype != MI_INT8:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Unsupported variable name subelement tag type {stype}; expected miINT8 (1)."],
            }

        if slen > MAX_VARIABLE_NAME_BYTES:
            return {
                "status": "RESOURCE_LIMIT_EXCEEDED",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Variable name length ({slen} bytes) exceeds {MAX_VARIABLE_NAME_BYTES}-byte limit."],
            }

        if sdata_off + slen > mat_len or snext_pos > mat_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Truncated array name data or padding in miMATRIX."],
            }
        name_bytes = mat_bytes[sdata_off : sdata_off + slen]
        try:
            var_name = name_bytes.decode("ascii")
        except UnicodeDecodeError:
            try:
                var_name = name_bytes.decode("utf-8")
            except UnicodeDecodeError:
                return {
                    "status": "MALFORMED_CONTAINER",
                    "container_format": "UNRECOGNIZED",
                    "available_variables": [],
                    "notes": ["Variable name is not valid ASCII or UTF-8."],
                }

        var_name = var_name.rstrip("\x00")
        if not var_name:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Empty variable name in MAT element."],
            }

        if "\x00" in var_name:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Variable name contains embedded null bytes."],
            }

        if var_name in seen_names:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Duplicate variable name '{var_name}' in container."],
            }

        seen_names.add(var_name)
        available_variables.append(var_name)
        if len(available_variables) > MAX_VARIABLES:
            return {
                "status": "RESOURCE_LIMIT_EXCEEDED",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Variable count exceeds {MAX_VARIABLES} limit."],
            }
        sub_pos = snext_pos

        # 4. Numeric Data (pr)
        if sub_pos + 8 > mat_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": ["Truncated numeric data sub-element in miMATRIX."],
            }
        try:
            ntype, nlen, ndata_off, nnext_pos = _unpack_mat_tag(mat_bytes, sub_pos, endian)
        except Exception as exc:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Failed to unpack numeric data sub-element: {exc}"],
            }

        if ntype == MI_COMPRESSED:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": ["Compressed numeric data sub-elements (miCOMPRESSED=15) are unsupported."],
            }

        expected_type = MI_DOUBLE if arr_class == MX_DOUBLE_CLASS else MI_SINGLE
        expected_type_name = "miDOUBLE" if arr_class == MX_DOUBLE_CLASS else "miSINGLE"
        if ntype != expected_type:
            return {
                "status": "UNSUPPORTED_STRUCTURE",
                "container_format": "MATLAB_V5",
                "available_variables": available_variables,
                "notes": [f"Unsupported numeric data sub-element type {ntype}; expected {expected_type_name}."],
            }

        expected_payload_bytes = sample_count * elem_size
        if nlen != expected_payload_bytes:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [
                    f"Numeric data payload length ({nlen} bytes) does not match declared dimensions "
                    f"({sample_count} * {elem_size} = {expected_payload_bytes} bytes)."
                ],
            }

        if ndata_off + nlen > mat_len or nnext_pos > mat_len:
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Truncated numeric data or padding in miMATRIX (declared length {nlen})."],
            }

        sub_pos = nnext_pos

        # Check for unexpected trailing subelements inside miMATRIX
        if sub_pos < mat_len:
            if sub_pos + 8 <= mat_len:
                try:
                    xtype, _, _, _ = _unpack_mat_tag(mat_bytes, sub_pos, endian)
                    return {
                        "status": "UNSUPPORTED_STRUCTURE",
                        "container_format": "MATLAB_V5",
                        "available_variables": available_variables,
                        "notes": [f"Unexpected trailing sub-element (type {xtype}) in miMATRIX."],
                    }
                except Exception:
                    pass
            return {
                "status": "MALFORMED_CONTAINER",
                "container_format": "UNRECOGNIZED",
                "available_variables": [],
                "notes": [f"Malformed trailing bytes ({mat_len - sub_pos} bytes) inside miMATRIX."],
            }

        if var_name == target_var:
            target_info = {
                "dtype": "float64" if arr_class == MX_DOUBLE_CLASS else "float32",
                "shape": [d0, d1],
                "sample_count": sample_count,
            }

        pos = next_pos

    if pos != total_len:
        return {
            "status": "MALFORMED_CONTAINER",
            "container_format": "UNRECOGNIZED",
            "available_variables": [],
            "notes": [f"Container has unaligned trailing bytes at offset {pos} (total length {total_len})."],
        }

    if target_var not in available_variables:
        return {
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": available_variables,
            "notes": [f"Requested target variable '{target_var}' not found in available variables: {available_variables}."],
        }

    return {
        "status": "PREFLIGHT_OK",
        "container_format": "MATLAB_V5",
        "available_variables": available_variables,
        "target_info": target_info,
        "notes": [],
    }


def _worker_preflight_and_extract(raw_bytes: bytes, target_var: str) -> Dict[str, Any]:
    """Internal worker logic: preflights MAT bytes and safely extracts target variable."""
    preflight = _run_structural_preflight(raw_bytes, target_var)
    if preflight["status"] != "PREFLIGHT_OK":
        return preflight

    # Target variable is guaranteed verified by preflight; invoke SciPy loadmat on exact in-memory buffer
    try:
        loaded = scipy.io.loadmat(
            io.BytesIO(raw_bytes),
            variable_names=[target_var],
            squeeze_me=False,
        )
    except Exception as exc:
        return {
            "status": "MALFORMED_CONTAINER",
            "container_format": "UNRECOGNIZED",
            "available_variables": [],
            "notes": [f"SciPy failed to decode target array: {exc}"],
        }

    if target_var not in loaded:
        return {
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": preflight["available_variables"],
            "notes": [f"SciPy loader did not find variable '{target_var}'."],
        }

    arr = loaded[target_var]
    if not isinstance(arr, np.ndarray):
        return {
            "status": "UNSUPPORTED_STRUCTURE",
            "container_format": "MATLAB_V5",
            "available_variables": preflight["available_variables"],
            "notes": [f"Decoded target array is not a numpy ndarray: {type(arr).__name__}."],
        }

    if arr.ndim != 2:
        return {
            "status": "UNSUPPORTED_STRUCTURE",
            "container_format": "MATLAB_V5",
            "available_variables": preflight["available_variables"],
            "notes": [f"Decoded array has rank {arr.ndim}; expected 2."],
        }

    d0, d1 = arr.shape
    if d0 != 1 and d1 != 1:
        return {
            "status": "UNSUPPORTED_STRUCTURE",
            "container_format": "MATLAB_V5",
            "available_variables": preflight["available_variables"],
            "notes": [f"Decoded array shape [{d0}, {d1}] is not a 2D row or column vector."],
        }

    sample_count = int(arr.size)
    if sample_count <= 0 or sample_count > MAX_SELECTED_ELEMENTS:
        return {
            "status": "RESOURCE_LIMIT_EXCEEDED",
            "container_format": "MATLAB_V5",
            "available_variables": preflight["available_variables"],
            "notes": [f"Decoded array size {sample_count} exceeds element limit."],
        }

    if arr.dtype == np.float64:
        dtype_str = "float64"
    elif arr.dtype == np.float32:
        dtype_str = "float32"
    else:
        return {
            "status": "UNSUPPORTED_STRUCTURE",
            "container_format": "MATLAB_V5",
            "available_variables": preflight["available_variables"],
            "notes": [f"Decoded array dtype '{arr.dtype}' is not float32 or float64."],
        }

    # Nonfinite count and finite extrema calculation
    nonfinite_mask = ~np.isfinite(arr)
    nonfinite_count = int(np.sum(nonfinite_mask))
    finite_count = sample_count - nonfinite_count

    if finite_count > 0:
        finite_samples = arr[~nonfinite_mask]
        min_val = float(np.min(finite_samples))
        max_val = float(np.max(finite_samples))
        extrema: Optional[Dict[str, float]] = {"min": min_val, "max": max_val}
    else:
        extrema = None

    if nonfinite_count > 0:
        status = "NONFINITE_VALUES_DETECTED"
        notes = [f"Detected {nonfinite_count} nonfinite (NaN/Inf) values out of {sample_count} samples."]
    else:
        status = "STRUCTURAL_SUCCESS"
        notes = ["Structural preflight and array decoding succeeded. Measurement readiness blocked by unknown metadata."]

    return {
        "status": status,
        "container_format": "MATLAB_V5",
        "available_variables": preflight["available_variables"],
        "numeric_dtype": dtype_str,
        "numeric_shape": [d0, d1],
        "sample_count": sample_count,
        "nonfinite_sample_count": nonfinite_count,
        "observed_raw_extrema": extrema,
        "notes": notes,
    }


def _generate_deterministic_inspection_id(
    raw_sha256: str, variable: str, policy_version: str = INSPECTION_POLICY_VERSION
) -> str:
    """Derive deterministic, versioned domain-separated inspection identifier."""
    seed = f"adi_exploratory_inspection_v1:{raw_sha256}:{variable}:{policy_version}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"insp_{digest[:32]}"


def _communicate_bounded(
    proc: subprocess.Popen,
    snapshot: bytes,
    timeout: float,
) -> Tuple[bytes, bytes, bool, bool]:
    """Multiplex subprocess I/O with strict deadline and bounded stdout/stderr buffers.

    Returns:
        (stdout_bytes, stderr_bytes, timed_out, ipc_overflow)
    """
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()

    stdin_buf = memoryview(snapshot)
    stdin_offset = 0
    stdout_chunks: List[bytes] = []
    stderr_chunks: List[bytes] = []
    total_stdout = 0
    total_stderr = 0

    timed_out = False
    ipc_overflow = False

    stdin_fd = proc.stdin.fileno() if proc.stdin is not None else None
    stdout_fd = proc.stdout.fileno() if proc.stdout is not None else None
    stderr_fd = proc.stderr.fileno() if proc.stderr is not None else None

    # Set non-blocking on streams
    for fd in (stdin_fd, stdout_fd, stderr_fd):
        if fd is not None:
            os.set_blocking(fd, False)

    if stdin_fd is not None and len(stdin_buf) > 0:
        selector.register(stdin_fd, selectors.EVENT_WRITE, data="stdin")
    elif proc.stdin is not None:
        try:
            proc.stdin.close()
        except OSError:
            pass

    if stdout_fd is not None:
        selector.register(stdout_fd, selectors.EVENT_READ, data="stdout")
    if stderr_fd is not None:
        selector.register(stderr_fd, selectors.EVENT_READ, data="stderr")

    def _safe_close_stdin() -> None:
        nonlocal stdin_fd
        if stdin_fd is not None:
            try:
                selector.unregister(stdin_fd)
            except Exception:
                pass
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            stdin_fd = None

    def _safe_close_stdout() -> None:
        nonlocal stdout_fd
        if stdout_fd is not None:
            try:
                selector.unregister(stdout_fd)
            except Exception:
                pass
            if proc.stdout is not None:
                try:
                    proc.stdout.close()
                except OSError:
                    pass
            stdout_fd = None

    def _safe_close_stderr() -> None:
        nonlocal stderr_fd
        if stderr_fd is not None:
            try:
                selector.unregister(stderr_fd)
            except Exception:
                pass
            if proc.stderr is not None:
                try:
                    proc.stderr.close()
                except OSError:
                    pass
            stderr_fd = None

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break

            if not selector.get_map():
                break

            events = selector.select(timeout=max(0.01, min(remaining, 0.2)))
            for key, mask in events:
                fd = key.fileobj
                tag = key.data

                if tag == "stdin" and (mask & selectors.EVENT_WRITE):
                    try:
                        written = os.write(fd, stdin_buf[stdin_offset:])
                        stdin_offset += written
                        if stdin_offset >= len(stdin_buf):
                            _safe_close_stdin()
                    except (BlockingIOError, InterruptedError):
                        pass
                    except OSError:
                        _safe_close_stdin()

                elif tag == "stdout" and (mask & selectors.EVENT_READ):
                    try:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            _safe_close_stdout()
                        else:
                            total_stdout += len(chunk)
                            if total_stdout > MAX_WORKER_IPC_STDOUT:
                                ipc_overflow = True
                                break
                            stdout_chunks.append(chunk)
                    except (BlockingIOError, InterruptedError):
                        pass
                    except OSError:
                        _safe_close_stdout()

                elif tag == "stderr" and (mask & selectors.EVENT_READ):
                    try:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            _safe_close_stderr()
                        else:
                            total_stderr += len(chunk)
                            if total_stderr > MAX_WORKER_IPC_STDERR:
                                ipc_overflow = True
                                break
                            stderr_chunks.append(chunk)
                    except (BlockingIOError, InterruptedError):
                        pass
                    except OSError:
                        _safe_close_stderr()

            if ipc_overflow:
                break

            if proc.poll() is not None:
                # Process terminated: drain any residual bytes in stdout/stderr pipes
                if stdout_fd is not None:
                    while True:
                        try:
                            chunk = os.read(stdout_fd, 65536)
                            if not chunk:
                                break
                            total_stdout += len(chunk)
                            if total_stdout > MAX_WORKER_IPC_STDOUT:
                                ipc_overflow = True
                                break
                            stdout_chunks.append(chunk)
                        except (BlockingIOError, InterruptedError):
                            break
                        except OSError:
                            break
                    _safe_close_stdout()

                if stderr_fd is not None:
                    while True:
                        try:
                            chunk = os.read(stderr_fd, 65536)
                            if not chunk:
                                break
                            total_stderr += len(chunk)
                            if total_stderr > MAX_WORKER_IPC_STDERR:
                                ipc_overflow = True
                                break
                            stderr_chunks.append(chunk)
                        except (BlockingIOError, InterruptedError):
                            break
                        except OSError:
                            break
                    _safe_close_stderr()

                break

    finally:
        _safe_close_stdin()
        _safe_close_stdout()
        _safe_close_stderr()
        selector.close()

    if timed_out or ipc_overflow:
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
    else:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
        elif proc.poll() is None:
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
            except Exception:
                pass

        if timed_out or proc.poll() is None:
            timed_out = True
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=1.0)
                except Exception:
                    pass

    return b"".join(stdout_chunks), b"".join(stderr_chunks), timed_out, ipc_overflow


VALID_CONTAINER_FORMATS = (
    "MATLAB_V5",
    "MATLAB_V4",
    "MATLAB_V7_3_HDF5",
    "CSV_ASCII",
    "UNRECOGNIZED",
)


def _safe_repr_val(val: Any) -> str:
    """Safely format a value for error messages without triggering integer-string conversion limits."""
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, int):
        if val.bit_length() > 1024:
            return f"<oversized int ({val.bit_length()} bits)>"
        return str(val)
    if isinstance(val, float):
        return str(val)
    if isinstance(val, str):
        if len(val) > 100:
            return repr(val[:100] + "...")
        return repr(val)
    return f"<{type(val).__name__}>"


def _is_exact_int(val: Any) -> bool:
    """Return True if val is an exact integer within representation bounds and not a boolean."""
    if isinstance(val, bool) or not isinstance(val, int):
        return False
    if val.bit_length() > 1024:
        return False
    return True


def _is_finite_number(val: Any) -> bool:
    """Return True if val is a finite real number (int or float) and not a boolean."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    if isinstance(val, int):
        if val.bit_length() > 1024:
            return False
        try:
            f = float(val)
        except (OverflowError, ValueError):
            return False
        return math.isfinite(f)
    return math.isfinite(val)


def _validate_variable_name_string(name: Any, context: str = "Variable") -> str:
    """Validate that a variable name is a valid, non-empty, bounded UTF-8 string without surrogates or nulls.

    Raises:
        SourceInspectionError: If the variable name fails any contract, type, or encoding requirement.
    """
    if not isinstance(name, str):
        raise SourceInspectionError(f"{context} must be str, got {type(name).__name__}.")
    if len(name.strip()) == 0:
        raise SourceInspectionError(f"{context} cannot be empty or whitespace only.")
    if "\x00" in name:
        raise SourceInspectionError(f"{context} contains embedded null byte.")
    try:
        encoded = name.encode("utf-8")
    except (UnicodeEncodeError, UnicodeError) as exc:
        raise SourceInspectionError(
            f"{context} contains invalid Unicode or unpaired surrogate: {exc}"
        ) from exc
    if len(encoded) > MAX_VARIABLE_NAME_BYTES:
        raise SourceInspectionError(
            f"{context} exceeds {MAX_VARIABLE_NAME_BYTES}-byte limit ({len(encoded)} bytes)."
        )
    return name


def _validate_path_argument(path: Any, context: str = "Input path") -> Path:
    """Validate input path argument before filesystem operations.

    Ensures the path argument is a string or Path object, non-empty, contains no embedded
    null bytes, and is valid UTF-8 without lone/unpaired surrogates. Formats safe diagnostics
    using _safe_repr_val.

    Args:
        path: Path argument provided to inspect_source.
        context: Description of the path context for error messages.

    Returns:
        Path object corresponding to the validated path.

    Raises:
        SourceInspectionError: If the path argument fails type, null-byte, or Unicode validation.
    """
    if not isinstance(path, (str, Path)):
        raise SourceInspectionError(
            f"Path argument must be a string or Path object, got {type(path).__name__}."
        )

    path_str = str(path)

    if len(path_str.strip()) == 0:
        raise SourceInspectionError(f"{context} cannot be empty or whitespace only.")

    if "\x00" in path_str:
        raise SourceInspectionError(
            f"{context} contains embedded null byte: {_safe_repr_val(path_str)}"
        )

    try:
        path_str.encode("utf-8")
    except (UnicodeEncodeError, UnicodeError) as exc:
        raise SourceInspectionError(
            f"{context} contains invalid Unicode or unpaired surrogate: {_safe_repr_val(path_str)}"
        ) from exc

    return Path(path)


def _validate_worker_protocol_payload(
    raw_obj: Any, target_var: Optional[str] = None
) -> Dict[str, Any]:
    """Validate raw JSON payload returned by internal worker process.

    Ensures top-level dict type, recognized structural status, valid container format,
    bounded variables and notes, exact integer dimensions, finite extrema, target
    consistency, and absence of success metadata for unsuccessful inspections.

    Raises:
        SourceInspectionError: If the worker payload violates internal protocol rules.
    """
    if not isinstance(raw_obj, dict):
        raise SourceInspectionError(
            f"Worker process emitted invalid payload type {type(raw_obj).__name__}; expected dict."
        )

    required_keys = ("status", "container_format", "available_variables", "notes")
    for key in required_keys:
        if key not in raw_obj:
            raise SourceInspectionError(f"Worker protocol payload missing required key '{key}'.")

    # 1. Structural Status Check
    status = raw_obj["status"]
    if not isinstance(status, str):
        raise SourceInspectionError(
            f"Worker status must be str, got {type(status).__name__}."
        )
    if status not in _STATUS_TO_READINESS:
        raise SourceInspectionError(
            f"Worker process returned unknown structural status '{status}'."
        )

    # 2. Container Format Enum and Status Consistency
    container_format = raw_obj["container_format"]
    if not isinstance(container_format, str):
        raise SourceInspectionError(
            f"Worker container_format must be str, got {type(container_format).__name__}."
        )
    if container_format not in VALID_CONTAINER_FORMATS:
        raise SourceInspectionError(
            f"Worker returned invalid container_format '{container_format}'; expected one of {VALID_CONTAINER_FORMATS}."
        )

    if status in ("STRUCTURAL_SUCCESS", "NONFINITE_VALUES_DETECTED", "TARGET_VARIABLE_NOT_FOUND"):
        if container_format != "MATLAB_V5":
            raise SourceInspectionError(
                f"Worker status '{status}' requires container_format 'MATLAB_V5', got '{container_format}'."
            )
    elif status in ("MALFORMED_CONTAINER", "CORRUPTED_RAW_BYTES"):
        if container_format != "UNRECOGNIZED":
            raise SourceInspectionError(
                f"Worker status '{status}' requires container_format 'UNRECOGNIZED', got '{container_format}'."
            )
    elif status == "UNSUPPORTED_STRUCTURE":
        if container_format not in ("MATLAB_V5", "MATLAB_V4", "MATLAB_V7_3_HDF5"):
            raise SourceInspectionError(
                f"Worker status '{status}' returned incompatible container_format '{container_format}'."
            )
    elif status == "RESOURCE_LIMIT_EXCEEDED":
        if container_format not in ("MATLAB_V5", "UNRECOGNIZED"):
            raise SourceInspectionError(
                f"Worker status '{status}' returned incompatible container_format '{container_format}'."
            )

    # 3. Available Variables List, Names, and Bounds
    available_vars = raw_obj["available_variables"]
    if not isinstance(available_vars, list):
        raise SourceInspectionError(
            f"Worker available_variables must be a list, got {type(available_vars).__name__}."
        )
    max_allowed_vars = MAX_VARIABLES if status != "RESOURCE_LIMIT_EXCEEDED" else MAX_VARIABLES + 1
    if len(available_vars) > max_allowed_vars:
        raise SourceInspectionError(
            f"Worker available_variables count ({len(available_vars)}) exceeds limit of {max_allowed_vars}."
        )

    # Validate individual element types and values BEFORE any hashing or set operations
    for v in available_vars:
        _validate_variable_name_string(v, context="Worker variable name")

    # Check for duplicates only after verifying all elements are valid strings
    if len(set(available_vars)) != len(available_vars):
        raise SourceInspectionError("Worker available_variables contains duplicate variable names.")

    if status in ("MALFORMED_CONTAINER", "CORRUPTED_RAW_BYTES"):
        if len(available_vars) != 0:
            raise SourceInspectionError(
                f"Worker status '{status}' must have empty available_variables, got {available_vars}."
            )

    # 4. Target Variable Consistency
    if target_var is not None:
        target_var = _validate_variable_name_string(target_var, context="Target variable")
        if status in ("STRUCTURAL_SUCCESS", "NONFINITE_VALUES_DETECTED"):
            if target_var not in available_vars:
                raise SourceInspectionError(
                    f"Worker reported '{status}' but requested target variable '{target_var}' is not in available_variables."
                )
        elif status == "TARGET_VARIABLE_NOT_FOUND":
            if target_var in available_vars:
                raise SourceInspectionError(
                    f"Worker reported TARGET_VARIABLE_NOT_FOUND but target variable '{target_var}' is in available_variables."
                )

    # 5. Diagnostic Notes Validation
    notes = raw_obj["notes"]
    if not isinstance(notes, list):
        raise SourceInspectionError(f"Worker notes must be a list, got {type(notes).__name__}.")
    if len(notes) > 100:
        raise SourceInspectionError(f"Worker notes count ({len(notes)}) exceeds limit of 100.")
    for n in notes:
        if not isinstance(n, str):
            raise SourceInspectionError(f"Worker note entry must be str, got {type(n).__name__}.")
        if len(n) > 2048:
            raise SourceInspectionError(f"Worker note entry length ({len(n)}) exceeds 2048 characters.")
        try:
            n.encode("utf-8")
        except (UnicodeEncodeError, UnicodeError) as exc:
            raise SourceInspectionError(
                f"Worker note entry contains invalid Unicode or unpaired surrogate: {exc}"
            ) from exc

    # 6. Status-Dependent Metadata Invariants
    if status in ("STRUCTURAL_SUCCESS", "NONFINITE_VALUES_DETECTED"):
        dtype = raw_obj.get("numeric_dtype")
        if dtype not in ("float32", "float64"):
            raise SourceInspectionError(
                f"Worker returned invalid numeric_dtype '{dtype}' for status '{status}'."
            )

        shape = raw_obj.get("numeric_shape")
        if (
            not isinstance(shape, (list, tuple))
            or len(shape) != 2
            or not all(_is_exact_int(d) and 0 < d <= MAX_SELECTED_ELEMENTS for d in shape)
            or (shape[0] != 1 and shape[1] != 1)
        ):
            raise SourceInspectionError(
                f"Worker returned invalid numeric_shape {_safe_repr_val(shape)} for status '{status}'; expected 2-element vector of positive exact ints."
            )

        sample_count = raw_obj.get("sample_count")
        if (
            not _is_exact_int(sample_count)
            or sample_count <= 0
            or sample_count > MAX_SELECTED_ELEMENTS
            or sample_count != shape[0] * shape[1]
        ):
            raise SourceInspectionError(
                f"Worker sample_count {_safe_repr_val(sample_count)} invalid or does not match shape {_safe_repr_val(shape)} product."
            )

        nonfinite_count = raw_obj.get("nonfinite_sample_count")
        if (
            not _is_exact_int(nonfinite_count)
            or nonfinite_count < 0
            or nonfinite_count > sample_count
        ):
            raise SourceInspectionError(
                f"Worker nonfinite_sample_count {_safe_repr_val(nonfinite_count)} invalid for sample_count {_safe_repr_val(sample_count)}."
            )

        if status == "STRUCTURAL_SUCCESS" and nonfinite_count != 0:
            raise SourceInspectionError(
                f"Worker STRUCTURAL_SUCCESS reported non-zero nonfinite_sample_count: {nonfinite_count}."
            )
        if status == "NONFINITE_VALUES_DETECTED" and nonfinite_count == 0:
            raise SourceInspectionError(
                "Worker NONFINITE_VALUES_DETECTED reported zero nonfinite_sample_count."
            )

        extrema = raw_obj.get("observed_raw_extrema")
        if nonfinite_count == sample_count:
            if extrema is not None:
                raise SourceInspectionError(
                    "Worker returned non-null extrema when all samples are non-finite."
                )
        else:
            if (
                not isinstance(extrema, dict)
                or not all(isinstance(k, str) for k in extrema.keys())
                or set(extrema.keys()) != {"min", "max"}
            ):
                raise SourceInspectionError(
                    "Worker returned invalid observed_raw_extrema structure; expected dict with exact 'min' and 'max' keys."
                )
            min_val = extrema["min"]
            max_val = extrema["max"]
            if not _is_finite_number(min_val) or not _is_finite_number(max_val):
                raise SourceInspectionError(
                    f"Worker extrema values must be finite numbers, got min={_safe_repr_val(min_val)}, max={_safe_repr_val(max_val)}."
                )
            if min_val > max_val:
                raise SourceInspectionError(
                    f"Worker extrema min ({_safe_repr_val(min_val)}) > max ({_safe_repr_val(max_val)})."
                )
    else:
        # Non-success statuses must not leak success metadata (fields must be completely absent)
        success_keys = (
            "numeric_dtype",
            "numeric_shape",
            "sample_count",
            "nonfinite_sample_count",
            "observed_raw_extrema",
        )
        for key in success_keys:
            if key in raw_obj:
                raise SourceInspectionError(
                    f"Worker returned unexpected '{key}' for non-success status '{status}'."
                )

    return raw_obj


def inspect_source(
    path: Union[str, Path],
    variable: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    _worker_cmd: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Inspect local MATLAB container and produce an ExploratorySourceInspectionV1 report.

    Args:
        path: Path to local regular MAT file.
        variable: Name of candidate time-series variable to inspect.
        timeout: Subprocess execution deadline in wall-clock seconds (default 10.0s).
        _worker_cmd: Optional subprocess command override for timeout/mock testing.

    Returns:
        Validated ExploratorySourceInspectionV1 report dictionary.

    Raises:
        SourceInspectionError: On input path, I/O, argument, or snapshot cap refusal.
    """
    variable = _validate_variable_name_string(variable, context="Variable argument")
    path_obj = _validate_path_argument(path, context="Input path")
    path_str = str(path)

    # Pre-open symlink and existence checks
    try:
        lstat_initial = os.lstat(path_obj)
    except OSError as exc:
        raise SourceInspectionError(f"Input file not found: {_safe_repr_val(path_str)}") from exc
    except (ValueError, UnicodeError) as exc:
        raise SourceInspectionError(f"Invalid input file path {_safe_repr_val(path_str)}: {exc}") from exc

    if stat.S_ISLNK(lstat_initial.st_mode):
        raise SourceInspectionError(
            f"Symbolic link input paths are rejected by security policy: {_safe_repr_val(path_str)}"
        )

    if not stat.S_ISREG(lstat_initial.st_mode):
        raise SourceInspectionError(
            f"Input path is not a regular file: {_safe_repr_val(path_str)}"
        )

    if lstat_initial.st_size > MAX_SNAPSHOT_BYTES:
        raise SourceInspectionError(
            f"Input file size ({lstat_initial.st_size} bytes) exceeds snapshot cap of {MAX_SNAPSHOT_BYTES} bytes."
        )

    # Capture stable location metadata at acquisition time BEFORE worker execution
    try:
        acquired_resolved_path = path_obj.resolve()
        source_locator = acquired_resolved_path.as_uri()
    except OSError as exc:
        raise SourceInspectionError(
            f"Failed to resolve input file path {_safe_repr_val(path_str)}: {exc}"
        ) from exc
    except (ValueError, UnicodeError) as exc:
        raise SourceInspectionError(
            f"Invalid input file path {_safe_repr_val(path_str)}: {exc}"
        ) from exc

    try:
        resolved_stat_initial = os.stat(acquired_resolved_path)
    except OSError as exc:
        raise SourceInspectionError(
            f"Failed to stat resolved input file path {_safe_repr_val(str(acquired_resolved_path))}: {exc}"
        ) from exc
    except (ValueError, UnicodeError) as exc:
        raise SourceInspectionError(
            f"Invalid input file path {_safe_repr_val(str(acquired_resolved_path))}: {exc}"
        ) from exc

    if (
        resolved_stat_initial.st_dev != lstat_initial.st_dev
        or resolved_stat_initial.st_ino != lstat_initial.st_ino
    ):
        raise SourceInspectionError(
            f"Input path resolution is inconsistent with initial stat: {_safe_repr_val(path_str)}"
        )

    source_id = f"local_{path_obj.stem}"
    source_filename = path_obj.name

    # Descriptor-based bounded snapshot acquisition with O_NOFOLLOW
    fd: Optional[int] = None
    open_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        open_flags |= os.O_NOFOLLOW

    try:
        try:
            fd = os.open(path_obj, open_flags)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", 0)):
                raise SourceInspectionError(
                    f"Symbolic link input paths are rejected by security policy: {_safe_repr_val(path_str)}"
                ) from exc
            raise SourceInspectionError(
                f"Failed to open input file {_safe_repr_val(path_str)}: {exc}"
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise SourceInspectionError(
                f"Invalid input file path {_safe_repr_val(path_str)}: {exc}"
            ) from exc

        try:
            st_initial = os.fstat(fd)
        except OSError as exc:
            raise SourceInspectionError(
                f"Failed to stat input file descriptor for {_safe_repr_val(path_str)}: {exc}"
            ) from exc

        # Reconcile initial lstat file identity with opened file descriptor's fstat identity
        if (
            st_initial.st_dev != lstat_initial.st_dev
            or st_initial.st_ino != lstat_initial.st_ino
        ):
            raise SourceInspectionError(
                f"Input path identity changed before descriptor acquisition: {_safe_repr_val(path_str)}"
            )

        if not stat.S_ISREG(st_initial.st_mode):
            raise SourceInspectionError(f"Input path is not a regular file: {_safe_repr_val(path_str)}")

        if st_initial.st_size > MAX_SNAPSHOT_BYTES:
            raise SourceInspectionError(
                f"Input file size ({st_initial.st_size} bytes) exceeds snapshot cap of {MAX_SNAPSHOT_BYTES} bytes."
            )

        chunks: List[bytes] = []
        total_read = 0
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError as exc:
                raise SourceInspectionError(
                    f"Failed to read input file {_safe_repr_val(path_str)}: {exc}"
                ) from exc
            if not chunk:
                break
            chunks.append(chunk)
            total_read += len(chunk)
            if total_read > MAX_SNAPSHOT_BYTES:
                raise SourceInspectionError(
                    f"Input file read exceeded snapshot cap of {MAX_SNAPSHOT_BYTES} bytes."
                )

        snapshot = b"".join(chunks)

        try:
            st_final = os.fstat(fd)
        except OSError as exc:
            raise SourceInspectionError(
                f"Failed to verify file descriptor integrity for {_safe_repr_val(path_str)}: {exc}"
            ) from exc

        if (
            st_final.st_size != len(snapshot)
            or st_final.st_mtime_ns != st_initial.st_mtime_ns
            or st_final.st_ino != st_initial.st_ino
            or st_final.st_dev != st_initial.st_dev
        ):
            raise SourceInspectionError(
                f"Input file was modified during acquisition: {_safe_repr_val(path_str)}"
            )

    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    # Detect path replacement or symlink swap during read
    try:
        lstat_final = os.lstat(path_obj)
    except OSError as exc:
        raise SourceInspectionError(
            f"Failed to verify path identity for {_safe_repr_val(path_str)}: {exc}"
        ) from exc
    except (ValueError, UnicodeError) as exc:
        raise SourceInspectionError(
            f"Invalid input file path {_safe_repr_val(path_str)}: {exc}"
        ) from exc

    if stat.S_ISLNK(lstat_final.st_mode):
        raise SourceInspectionError(
            f"Symbolic link input paths are rejected by security policy: {_safe_repr_val(path_str)}"
        )

    if (
        lstat_final.st_ino != st_initial.st_ino
        or lstat_final.st_dev != st_initial.st_dev
    ):
        raise SourceInspectionError(
            f"Input path was replaced during acquisition: {_safe_repr_val(path_str)}"
        )

    # Re-verify resolved pathname identity has not changed target
    try:
        resolved_final = path_obj.resolve()
        resolved_final_stat = os.stat(resolved_final)
    except OSError as exc:
        raise SourceInspectionError(
            f"Failed to verify path resolution after acquisition: {_safe_repr_val(path_str)}: {exc}"
        ) from exc
    except (ValueError, UnicodeError) as exc:
        raise SourceInspectionError(
            f"Invalid input file path {_safe_repr_val(path_str)}: {exc}"
        ) from exc

    if (
        resolved_final != acquired_resolved_path
        or resolved_final_stat.st_ino != st_initial.st_ino
        or resolved_final_stat.st_dev != st_initial.st_dev
    ):
        raise SourceInspectionError(
            f"Input path resolution changed during acquisition: {_safe_repr_val(path_str)}"
        )

    raw_byte_sha256 = hashlib.sha256(snapshot).hexdigest()
    raw_byte_size = len(snapshot)

    # Subprocess execution with deadline enforcement and bounded IPC
    cmd = _worker_cmd or [
        sys.executable,
        "-m",
        "adi_app.source_inspection",
        "--internal-worker",
        "--variable",
        variable,
    ]

    worker_result: Dict[str, Any]

    worker_env = os.environ.copy()
    current_paths = [p for p in sys.path if p]
    existing_pythonpath = worker_env.get("PYTHONPATH", "")
    all_paths = [p for p in current_paths if p not in existing_pythonpath.split(os.pathsep)]
    if existing_pythonpath:
        worker_env["PYTHONPATH"] = existing_pythonpath + os.pathsep + os.pathsep.join(all_paths)
    else:
        worker_env["PYTHONPATH"] = os.pathsep.join(all_paths)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=worker_env,
        )
    except OSError as exc:
        raise SourceInspectionError(f"Failed to spawn source inspection worker process: {exc}") from exc

    stdout_data, stderr_data, timed_out, ipc_overflow = _communicate_bounded(
        proc, snapshot, timeout
    )

    if timed_out:
        worker_result = {
            "status": "RESOURCE_LIMIT_EXCEEDED",
            "container_format": "MATLAB_V5",
            "available_variables": [],
            "notes": [f"Preflight or parsing exceeded {timeout:.1f}-second execution deadline."],
        }
    elif ipc_overflow:
        raise SourceInspectionError("Worker process exceeded IPC payload limit.")
    elif proc.returncode != 0:
        err_msg = stderr_data.decode("utf-8", errors="replace").strip()
        raise SourceInspectionError(f"Worker process failed with exit code {proc.returncode}: {err_msg}")
    else:
        def _reject_json_constant(c: str) -> None:
            raise ValueError(f"Nonstandard JSON constant not permitted: '{c}'.")

        try:
            raw_json = json.loads(stdout_data.decode("utf-8"), parse_constant=_reject_json_constant)
        except Exception as exc:
            raise SourceInspectionError(f"Worker process emitted invalid JSON output: {exc}") from exc
        worker_result = _validate_worker_protocol_payload(raw_json, target_var=variable)

    structural_status = worker_result["status"]
    measurement_readiness = _STATUS_TO_READINESS[structural_status]
    container_format = worker_result["container_format"]

    # Status-dependent container facts
    if structural_status in ("STRUCTURAL_SUCCESS", "NONFINITE_VALUES_DETECTED"):
        numeric_dtype = worker_result.get("numeric_dtype")
        numeric_shape = worker_result.get("numeric_shape")
        sample_count = worker_result.get("sample_count")
        nonfinite_sample_count = worker_result.get("nonfinite_sample_count", 0)
        observed_raw_extrema = worker_result.get("observed_raw_extrema")
        available_variables = worker_result.get("available_variables", [])
    else:
        numeric_dtype = None
        numeric_shape = None
        sample_count = None
        nonfinite_sample_count = None
        observed_raw_extrema = None
        if structural_status in ("MALFORMED_CONTAINER", "CORRUPTED_RAW_BYTES"):
            container_format = "UNRECOGNIZED"
            available_variables = []
        else:
            available_variables = worker_result.get("available_variables", [])

    inspection_id = _generate_deterministic_inspection_id(raw_byte_sha256, variable)
    inspected_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    report: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "inspection_id": inspection_id,
        "inspected_at": inspected_at,
        "source_identity": {
            "source_id": source_id,
            "source_locator": source_locator,
            "source_filename": source_filename,
            "raw_byte_sha256": raw_byte_sha256,
            "raw_byte_size": raw_byte_size,
        },
        "container_facts": {
            "container_format": container_format,
            "available_variables": available_variables,
            "target_variable": variable,
            "numeric_dtype": numeric_dtype,
            "numeric_shape": numeric_shape,
            "sample_count": sample_count,
            "nonfinite_sample_count": nonfinite_sample_count,
            "observed_raw_extrema": observed_raw_extrema,
        },
        "source_documentation": {
            "sensor_physical_location": {
                "value": None,
                "evidence_reference": "Unstated in local source container; primary physical calibration documentation is required.",
            },
            "test_rig_description": {
                "value": None,
                "evidence_reference": "Unstated in local source container; primary physical calibration documentation is required.",
            },
            "nominal_motor_load_hp": {
                "value": None,
                "evidence_reference": "Unstated in local source container; primary physical calibration documentation is required.",
            },
            "nominal_motor_speed_rpm": {
                "value": None,
                "evidence_reference": "Unstated in local source container; primary physical calibration documentation is required.",
            },
            "recording_condition_label": {
                "value": "UNSTATED",
                "evidence_reference": "Unstated in local source container; primary physical calibration documentation is required.",
                "scope": "WHOLE_RECORDING_ONLY",
            },
        },
        "metadata_status_ledger": {
            "physical_acceleration_unit": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "Physical acceleration unit is unstated in local container bytes. File inspection confirms numeric values only.",
            },
            "native_sampling_rate_hz": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "Native sampling rate is unstated in local container bytes. Array length cannot establish temporal sampling frequency.",
            },
            "acquisition_full_scale_limit": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "Sensor full-scale range and acquisition clipping limits are unstated. Observed extrema cannot prove electrical dynamic range.",
            },
            "physical_specimen_identity": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "Physical specimen identity is unstated in local container. Test component independence cannot be inferred.",
            },
            "legal_usage_grant": {
                "status": "UNKNOWN_UNSTATED",
                "resolved_value": None,
                "evidence_reference": None,
                "evidence_gap_statement": "Legal licensing terms and usage grants are unstated in local container bytes.",
            },
        },
        "local_annotations": {
            "precautionary_specimen_group": "UNASSIGNED_LOCAL_SOURCE",
            "candidate_partition_role": "unassigned",
            "provenance_classification": "recorded",
        },
        "inspection_findings": {
            "structural_status": structural_status,
            "measurement_readiness": measurement_readiness,
            "detector_admission_eligible": False,
            "diagnostic_notes": worker_result.get("notes", []),
        },
    }

    # Contract verification with defensive error boundary
    try:
        validate_exploratory_source_inspection(report)
    except ContractValidationError as exc:
        raise SourceInspectionError(f"Inspection report failed contract validation: {exc}") from exc
    return report


def main() -> None:
    """Internal entrypoint for subprocess worker execution."""
    parser = argparse.ArgumentParser(description="Internal source inspection worker")
    parser.add_argument("--internal-worker", action="store_true", required=True)
    parser.add_argument("--variable", type=str, required=True)
    args = parser.parse_args()

    # Bounded stdin read
    raw_bytes = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw_bytes) > MAX_SNAPSHOT_BYTES:
        sys.stderr.write(f"Worker received input exceeding {MAX_SNAPSHOT_BYTES} bytes.\n")
        sys.exit(2)

    result = _worker_preflight_and_extract(raw_bytes, args.variable)
    json_bytes = json.dumps(result, allow_nan=False).encode("utf-8")
    sys.stdout.buffer.write(json_bytes)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
