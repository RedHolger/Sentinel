"""Comprehensive test suite for Stage 10: Local Source Inspection.

Verifies:
- Synthetic uncompressed MATLAB Level 5 fixtures (float32/64 row/column vectors)
- Extrema calculation and nonfinite handling (mixed, all-nonfinite with null extrema)
- Rejection of compressed MAT elements before SciPy numeric decoding
- Rejection of MATLAB v4 and v7.3/HDF5 containers
- Rejection of complex, cell, struct, object, char, sparse, and integer arrays
- Rejection of higher-dimensional arrays and non-vector matrices
- Structural preflight rejection of duplicate variable names
- Preflight rejection of oversized dimensions (>10M elements) and payloads (>50MB) before allocation
- Preflight rejection of variable names >256 bytes and variable count >1024
- Operational error handling (missing files, directories, snapshot cap >50MB, concurrent modification, invalid variable names)
- Subprocess deadline enforcement and worker process termination/reaping
- Deterministic domain-separated inspection ID and contract compliance (ExploratorySourceInspectionV1)
- Strict JSON serialization with allow_nan=False
- CLI exit codes (0, 1, 2), strict stdout JSON, empty stdout on errors, atomic --out file writing,
  non-overwrite protections, and preservation of existing commands.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
import unittest

import numpy as np
import scipy.io

from adi_common import ContractValidationError, validate_exploratory_source_inspection
from adi_app.portfolio import main
from adi_app.source_inspection import (
    MAX_SNAPSHOT_BYTES,
    SourceInspectionError,
    _generate_deterministic_inspection_id,
    _run_structural_preflight,
    _validate_path_argument,
    _validate_variable_name_string,
    _validate_worker_protocol_payload,
    inspect_source,
)


def _make_mat_level5_bytes(variables: Dict[str, Any], do_compression: bool = False, fmt: str = "5") -> bytes:
    """Helper to generate in-memory synthetic MAT file bytes."""
    buf = io.BytesIO()
    scipy.io.savemat(buf, variables, do_compression=do_compression, format=fmt)
    return buf.getvalue()


def _run_cli(args: List[str]) -> Tuple[int, str, str]:
    """Execute adi-portfolio via subprocess."""
    cmd = [sys.executable, "-m", "adi_app.portfolio"] + args
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return res.returncode, res.stdout, res.stderr


class TestSyntheticMATInspection(unittest.TestCase):
    """Unit tests for synthetic MAT file inspection and structural preflight."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_mat(self, filename: str, variables: Dict[str, Any], do_compression: bool = False, fmt: str = "5") -> Path:
        raw_bytes = _make_mat_level5_bytes(variables, do_compression=do_compression, fmt=fmt)
        p = self.dir_path / filename
        p.write_bytes(raw_bytes)
        return p

    def test_valid_float32_row_vector(self) -> None:
        arr = np.array([[1.5, -2.5, 3.0]], dtype=np.float32)
        p = self._write_mat("f32_row.mat", {"sensor_x": arr})

        rep = inspect_source(p, "sensor_x")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["container_format"], "MATLAB_V5")
        self.assertEqual(facts["numeric_dtype"], "float32")
        self.assertEqual(facts["numeric_shape"], [1, 3])
        self.assertEqual(facts["sample_count"], 3)
        self.assertEqual(facts["nonfinite_sample_count"], 0)
        self.assertAlmostEqual(facts["observed_raw_extrema"]["min"], -2.5)
        self.assertAlmostEqual(facts["observed_raw_extrema"]["max"], 3.0)

        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "STRUCTURAL_SUCCESS")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_UNKNOWN_METADATA")
        self.assertFalse(findings["detector_admission_eligible"])

    def test_valid_float32_col_vector(self) -> None:
        arr = np.array([[10.0], [-5.0], [0.0]], dtype=np.float32)
        p = self._write_mat("f32_col.mat", {"col_data": arr})

        rep = inspect_source(p, "col_data")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["numeric_shape"], [3, 1])
        self.assertEqual(facts["sample_count"], 3)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")

    def test_valid_float64_row_vector(self) -> None:
        arr = np.array([[0.001, 100.5, -50.25]], dtype=np.float64)
        p = self._write_mat("f64_row.mat", {"vibration": arr})

        rep = inspect_source(p, "vibration")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["numeric_dtype"], "float64")
        self.assertEqual(facts["numeric_shape"], [1, 3])
        self.assertAlmostEqual(facts["observed_raw_extrema"]["min"], -50.25)
        self.assertAlmostEqual(facts["observed_raw_extrema"]["max"], 100.5)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")

    def test_valid_float64_col_vector(self) -> None:
        arr = np.array([[1.0], [2.0], [3.0], [4.0]], dtype=np.float64)
        p = self._write_mat("f64_col.mat", {"ts": arr})

        rep = inspect_source(p, "ts")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["numeric_dtype"], "float64")
        self.assertEqual(facts["numeric_shape"], [4, 1])
        self.assertEqual(facts["sample_count"], 4)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")

    def test_single_element_vector(self) -> None:
        arr = np.array([[42.0]], dtype=np.float64)
        p = self._write_mat("single.mat", {"scalar_vec": arr})

        rep = inspect_source(p, "scalar_vec")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["numeric_shape"], [1, 1])
        self.assertEqual(facts["sample_count"], 1)
        self.assertEqual(facts["observed_raw_extrema"]["min"], 42.0)
        self.assertEqual(facts["observed_raw_extrema"]["max"], 42.0)

    def test_mixed_finite_and_nonfinite_values(self) -> None:
        arr = np.array([[1.0, np.nan, -10.5, np.inf, 20.0, -np.inf]], dtype=np.float64)
        p = self._write_mat("mixed_nonfinite.mat", {"sig": arr})

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["sample_count"], 6)
        self.assertEqual(facts["nonfinite_sample_count"], 3)
        self.assertEqual(facts["observed_raw_extrema"]["min"], -10.5)
        self.assertEqual(facts["observed_raw_extrema"]["max"], 20.0)

        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "NONFINITE_VALUES_DETECTED")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_NONFINITE_VALUES")

    def test_all_nonfinite_values_produce_null_extrema(self) -> None:
        arr = np.array([[np.nan, np.inf, -np.inf]], dtype=np.float64)
        p = self._write_mat("all_nonfinite.mat", {"bad": arr})

        rep = inspect_source(p, "bad")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["sample_count"], 3)
        self.assertEqual(facts["nonfinite_sample_count"], 3)
        self.assertIsNone(facts["observed_raw_extrema"])
        self.assertEqual(rep["inspection_findings"]["structural_status"], "NONFINITE_VALUES_DETECTED")

    def test_target_variable_not_found(self) -> None:
        arr = np.array([[1.0, 2.0]], dtype=np.float64)
        p = self._write_mat("has_a.mat", {"var_a": arr})

        rep = inspect_source(p, "var_b")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["container_format"], "MATLAB_V5")
        self.assertEqual(facts["available_variables"], ["var_a"])
        self.assertEqual(facts["target_variable"], "var_b")
        self.assertIsNone(facts["numeric_dtype"])
        self.assertIsNone(facts["numeric_shape"])
        self.assertIsNone(facts["sample_count"])
        self.assertIsNone(facts["observed_raw_extrema"])

        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "TARGET_VARIABLE_NOT_FOUND")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_TARGET_NOT_FOUND")

    def test_compressed_element_rejected_before_scipy_decoding(self) -> None:
        arr = np.array([[1.0, 2.0, 3.0]], dtype=np.float64)
        p = self._write_mat("compressed.mat", {"comp": arr}, do_compression=True)

        rep = inspect_source(p, "comp")
        validate_exploratory_source_inspection(rep)

        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "UNSUPPORTED_STRUCTURE")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_UNSUPPORTED_FORMAT")
        self.assertTrue(any("Compressed" in note for note in findings["diagnostic_notes"]))

    def test_matlab_v4_rejected(self) -> None:
        arr = np.array([[1.0, 2.0]], dtype=np.float64)
        p = self._write_mat("v4.mat", {"v4var": arr}, fmt="4")

        rep = inspect_source(p, "v4var")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["container_format"], "MATLAB_V4")
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_matlab_v7_3_hdf5_rejected(self) -> None:
        p = self.dir_path / "v73.mat"
        # HDF5 magic header
        p.write_bytes(b"\x89HDF\r\n\x1a\n" + b"\x00" * 200)

        rep = inspect_source(p, "var_hdf5")
        validate_exploratory_source_inspection(rep)

        facts = rep["container_facts"]
        self.assertEqual(facts["container_format"], "MATLAB_V7_3_HDF5")
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_complex_array_rejected(self) -> None:
        arr = np.array([[1.0 + 2.0j, 3.0 - 4.0j]], dtype=np.complex128)
        p = self._write_mat("complex.mat", {"cx": arr})

        rep = inspect_source(p, "cx")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_integer_array_rejected(self) -> None:
        arr = np.array([[1, 2, 3]], dtype=np.int32)
        p = self._write_mat("int.mat", {"ints": arr})

        rep = inspect_source(p, "ints")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_char_array_rejected(self) -> None:
        p = self._write_mat("char.mat", {"str_var": "vibration_signal"})

        rep = inspect_source(p, "str_var")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_2d_matrix_both_dims_gt_1_rejected(self) -> None:
        arr = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)
        p = self._write_mat("matrix2d.mat", {"mat2d": arr})

        rep = inspect_source(p, "mat2d")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_3d_array_rejected(self) -> None:
        arr = np.zeros((2, 2, 2), dtype=np.float64)
        p = self._write_mat("arr3d.mat", {"cube": arr})

        rep = inspect_source(p, "cube")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_cell_array_rejected(self) -> None:
        cell = np.empty((1, 2), dtype=object)
        cell[0, 0] = np.array([1.0])
        cell[0, 1] = np.array([2.0])
        p = self._write_mat("cell.mat", {"my_cell": cell})

        rep = inspect_source(p, "my_cell")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_struct_array_rejected(self) -> None:
        p = self._write_mat("struct.mat", {"my_struct": {"field1": 1.0, "field2": 2.0}})

        rep = inspect_source(p, "my_struct")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")

    def test_duplicate_variable_names_rejected(self) -> None:
        raw = _make_mat_level5_bytes({"dup": np.array([[1.0, 2.0]])})
        header = raw[:128]
        elem = raw[128:]
        dup_bytes = header + elem + elem
        p = self.dir_path / "dup.mat"
        p.write_bytes(dup_bytes)

        rep = inspect_source(p, "dup")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "UNSUPPORTED_STRUCTURE")
        self.assertTrue(any("Duplicate" in note for note in rep["inspection_findings"]["diagnostic_notes"]))

    def test_huge_dimensions_rejected_before_allocation(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        # Overwrite dimension tag data [1, 2] to [1, 20_000_000]
        raw[160:168] = struct.pack("<2i", 1, 20_000_000)
        p = self.dir_path / "huge_dims.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_RESOURCE_LIMIT")
        self.assertTrue(any("10000000 element limit" in note for note in findings["diagnostic_notes"]))

    def test_huge_payload_declared_rejected_before_allocation(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        # 7,000,000 float64 elements = 56 MB > 50 MB snapshot cap
        raw[160:168] = struct.pack("<2i", 1, 7_000_000)
        p = self.dir_path / "huge_payload.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertTrue(any("exceeds 50000000 byte snapshot cap" in note for note in findings["diagnostic_notes"]))

    def test_variable_name_exceeding_256_bytes_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        raw[168:176] = struct.pack("<II", 1, 300)
        p = self.dir_path / "long_name.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertTrue(any("256-byte limit" in note for note in findings["diagnostic_notes"]))

    def test_variable_count_exceeding_1024_rejected(self) -> None:
        raw = _make_mat_level5_bytes({"v": np.array([[1.0]], dtype=np.float64)})
        header = raw[:128]
        elem = raw[128:]

        elements = []
        for i in range(1025):
            b = bytearray(elem)
            c1 = chr(ord("a") + i // 676)
            c2 = chr(ord("a") + (i // 26) % 26)
            c3 = chr(ord("a") + i % 26)
            name = f"{c1}{c2}{c3}".encode("ascii")
            w1 = 1 | (3 << 16)
            b[40:44] = struct.pack("<I", w1)
            b[44:48] = name + b"\x00"
            elements.append(bytes(b))

        all_bytes = header + b"".join(elements)
        p = self.dir_path / "vars_1025.mat"
        p.write_bytes(all_bytes)

        rep = inspect_source(p, "aaa")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertTrue(any("Variable count exceeds 1024" in note for note in findings["diagnostic_notes"]))

    def test_malformed_truncated_container(self) -> None:
        # File shorter than 128 bytes
        p = self.dir_path / "short.mat"
        p.write_bytes(b"MATLAB 5.0")

        rep = inspect_source(p, "v")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["container_facts"]["container_format"], "UNRECOGNIZED")
        self.assertEqual(rep["container_facts"]["available_variables"], [])
        self.assertEqual(rep["inspection_findings"]["structural_status"], "MALFORMED_CONTAINER")
        self.assertEqual(rep["inspection_findings"]["measurement_readiness"], "BLOCKED_CORRUPTED_FILE")

    def test_malformed_endian_indicator(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"v": np.array([[1.0]])}))
        raw[126:128] = b"XX"
        p = self.dir_path / "bad_endian.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "v")
        validate_exploratory_source_inspection(rep)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "MALFORMED_CONTAINER")

    def test_numeric_tag_changed_to_int8_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Numeric tag is at offset 176 (type=9 for miDOUBLE). Mutate type to 1 (miINT8).
        raw[176:180] = struct.pack("<I", 1)
        p = self.dir_path / "int8_numeric.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "UNSUPPORTED_STRUCTURE")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_UNSUPPORTED_FORMAT")
        self.assertTrue(any("miDOUBLE" in note for note in findings["diagnostic_notes"]))

    def test_numeric_tag_changed_to_compressed_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Mutate type to 15 (miCOMPRESSED)
        raw[176:180] = struct.pack("<I", 15)
        p = self.dir_path / "compressed_numeric.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "UNSUPPORTED_STRUCTURE")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_UNSUPPORTED_FORMAT")
        self.assertTrue(any("Compressed numeric data" in note for note in findings["diagnostic_notes"]))

    def test_numeric_payload_declared_1gb_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Mutate numeric data length to 1 GB (1_000_000_000 bytes)
        raw[180:184] = struct.pack("<I", 1_000_000_000)
        p = self.dir_path / "numeric_1gb.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertIn(findings["structural_status"], ("MALFORMED_CONTAINER", "RESOURCE_LIMIT_EXCEEDED"))
        self.assertIn(findings["measurement_readiness"], ("BLOCKED_CORRUPTED_FILE", "BLOCKED_RESOURCE_LIMIT"))

    def test_numeric_payload_length_inconsistent_with_dims_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Dimensions declare [1, 2] -> 16 bytes. Mutate numeric data length to 24 bytes.
        raw[180:184] = struct.pack("<I", 24)
        p = self.dir_path / "numeric_inconsistent.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "MALFORMED_CONTAINER")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_CORRUPTED_FILE")
        self.assertTrue(any("does not match declared dimensions" in note for note in findings["diagnostic_notes"]))

    def test_truncated_top_level_padding_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Top-level tag is at offset 128: [type=14, len=64].
        # Change tag_len from 64 to 61.
        # Data length becomes 61 (ends at 136 + 61 = 197).
        # Padded length is 64 (needs 200 bytes total).
        # Truncate file at byte 197 so all 61 data bytes are present, but 3 padding bytes are truncated.
        raw[132:136] = struct.pack("<I", 61)
        truncated_raw = bytes(raw[:197])
        p = self.dir_path / "truncated_padding.mat"
        p.write_bytes(truncated_raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "MALFORMED_CONTAINER")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_CORRUPTED_FILE")

    def test_truncated_subelement_padding_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Sub 0 class=single (7), dims [1, 3] = 12 bytes.
        # Sub 3 len=12, outer matrix len=60 (missing 4 padding bytes).
        raw[144] = 7  # class = single
        raw[160:168] = struct.pack("<2i", 1, 3)  # dims [1, 3]
        raw[176:180] = struct.pack("<I", 7)  # type = MI_SINGLE
        raw[180:184] = struct.pack("<I", 12)  # len = 12
        raw[132:136] = struct.pack("<I", 60)  # outer matrix len = 60
        p = self.dir_path / "truncated_sub_padding.mat"
        p.write_bytes(bytes(raw[:196]))

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "MALFORMED_CONTAINER")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_CORRUPTED_FILE")

    def test_unexpected_trailing_subelement_in_matrix_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Outer matrix len is 64. Increase to 72 and append an extra 8-byte tag (type=1, len=0).
        raw[132:136] = struct.pack("<I", 72)
        raw.extend(struct.pack("<II", 1, 0))
        p = self.dir_path / "trailing_subelement.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "UNSUPPORTED_STRUCTURE")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_UNSUPPORTED_FORMAT")
        self.assertTrue(any("Unexpected trailing sub-element" in note for note in findings["diagnostic_notes"]))

    def test_malformed_trailing_bytes_in_matrix_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Outer matrix len is 64. Increase to 67 and append 3 junk bytes (plus 5 padding bytes).
        raw[132:136] = struct.pack("<I", 67)
        raw.extend(b"\xaa\xbb\xcc\x00\x00\x00\x00\x00")
        p = self.dir_path / "junk_subelement.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "MALFORMED_CONTAINER")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_CORRUPTED_FILE")

    def test_unexpected_trailing_bytes_at_eof_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        raw.extend(b"EXTRA_TRAILING_BYTES")
        p = self.dir_path / "trailing_eof.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "MALFORMED_CONTAINER")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_CORRUPTED_FILE")

    def test_aggregate_payload_exceeding_50mb_rejected(self) -> None:
        # Variable 1: 3,250,000 float64 elements = 26 MB
        arr = np.zeros((1, 3_250_000), dtype=np.float64)
        raw1 = _make_mat_level5_bytes({"a": arr})
        header = raw1[:128]
        var1_elem = raw1[128:]

        # Variable 2: Header whose dimensions declare 26 MB (3,250,000 elements)
        raw2 = _make_mat_level5_bytes({"b": np.array([[1.0, 2.0]], dtype=np.float64)})
        var2_elem = bytearray(raw2[128:])
        var2_elem[32:40] = struct.pack("<2i", 1, 3_250_000)

        p = self.dir_path / "aggregate_52mb.mat"
        p.write_bytes(header + var1_elem + bytes(var2_elem))

        rep = inspect_source(p, "a")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_RESOURCE_LIMIT")
        self.assertTrue(any("Aggregate declared array payload" in note for note in findings["diagnostic_notes"]))

    def test_variable_name_tag_not_int8_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Variable name tag is at offset 168 (type 1 for miINT8). Mutate type to 9 (miDOUBLE).
        raw[168] = 9
        p = self.dir_path / "name_not_int8.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "UNSUPPORTED_STRUCTURE")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_UNSUPPORTED_FORMAT")
        self.assertTrue(any("miINT8" in note for note in findings["diagnostic_notes"]))

    def test_variable_name_embedded_null_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Mutate 'i' in "sig" to null byte \x00
        raw[173] = 0
        p = self.dir_path / "embedded_null_name.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "MALFORMED_CONTAINER")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_CORRUPTED_FILE")
        self.assertTrue(any("embedded null" in note for note in findings["diagnostic_notes"]))

    def test_array_flags_length_not_8_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Flags tag length is at offset 140 (unpack "<II" at 136). Mutate length to 12.
        raw[140:144] = struct.pack("<I", 12)
        p = self.dir_path / "flags_len_not_8.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "MALFORMED_CONTAINER")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_CORRUPTED_FILE")
        self.assertTrue(any("exactly 8 bytes" in note for note in findings["diagnostic_notes"]))

    def test_array_flags_logical_or_global_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # Flags word is at offset 144..148. Set logical flag bit (0x0200).
        flags = struct.unpack("<I", raw[144:148])[0]
        raw[144:148] = struct.pack("<I", flags | 0x0200)
        p = self.dir_path / "flags_logical.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "UNSUPPORTED_STRUCTURE")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_UNSUPPORTED_FORMAT")
        self.assertTrue(any("Logical arrays" in note for note in findings["diagnostic_notes"]))

    def test_array_flags_nonzero_nzmax_rejected(self) -> None:
        raw = bytearray(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        # nzmax word is at offset 148..152. Set nzmax = 1.
        raw[148:152] = struct.pack("<I", 1)
        p = self.dir_path / "flags_nzmax.mat"
        p.write_bytes(raw)

        rep = inspect_source(p, "sig")
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "UNSUPPORTED_STRUCTURE")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_UNSUPPORTED_FORMAT")
        self.assertTrue(any("nzmax" in note for note in findings["diagnostic_notes"]))


class TestOperationalAndSafetyGuards(unittest.TestCase):
    """Tests for file existence, regular file checks, snapshot limits, and timeout isolation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_file_not_found_raises_source_inspection_error(self) -> None:
        missing = self.dir_path / "nonexistent.mat"
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(missing, "vib")
        self.assertIn("not found", str(ctx.exception).lower())

    def test_directory_raises_source_inspection_error(self) -> None:
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(self.dir_path, "vib")
        self.assertIn("not a regular file", str(ctx.exception).lower())

    def test_invalid_path_argument_null_byte_raises_source_inspection_error(self) -> None:
        with self.assertRaises(SourceInspectionError) as ctx_str:
            inspect_source("invalid\x00.mat", "vib")
        self.assertIn("null byte", str(ctx_str.exception).lower())

        with self.assertRaises(SourceInspectionError) as ctx_path:
            inspect_source(Path("invalid\x00.mat"), "vib")
        self.assertIn("null byte", str(ctx_path.exception).lower())

    def test_invalid_path_argument_surrogate_raises_source_inspection_error(self) -> None:
        with self.assertRaises(SourceInspectionError) as ctx_str:
            inspect_source("invalid\ud800.mat", "vib")
        self.assertIn("surrogate", str(ctx_str.exception).lower())

        with self.assertRaises(SourceInspectionError) as ctx_path:
            inspect_source(Path("invalid\ud800.mat"), "vib")
        self.assertIn("surrogate", str(ctx_path.exception).lower())

    def test_invalid_path_argument_type_and_whitespace_raises_source_inspection_error(self) -> None:
        with self.assertRaises(SourceInspectionError):
            inspect_source(None, "vib")  # type: ignore
        with self.assertRaises(SourceInspectionError):
            inspect_source(12345, "vib")  # type: ignore
        with self.assertRaises(SourceInspectionError):
            inspect_source("", "vib")
        with self.assertRaises(SourceInspectionError):
            inspect_source("   ", "vib")

    def test_legitimate_unicode_path_succeeds(self) -> None:
        unicode_dir = self.dir_path / "données_mesures_α"
        unicode_dir.mkdir()
        unicode_mat = unicode_dir / "vibration_échantillon_β.mat"
        unicode_mat.write_bytes(_make_mat_level5_bytes({"vib": np.array([[1.0, 2.0]], dtype=np.float64)}))

        rep = inspect_source(unicode_mat, "vib")
        self.assertEqual(rep["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")
        self.assertEqual(rep["source_identity"]["source_filename"], "vibration_échantillon_β.mat")

        rep_str = inspect_source(str(unicode_mat), "vib")
        self.assertEqual(rep_str["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")

    def test_invalid_variable_argument_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "sample.mat"
        p.write_bytes(_make_mat_level5_bytes({"x": np.array([[1.0]])}))

        with self.assertRaises(SourceInspectionError):
            inspect_source(p, "")
        with self.assertRaises(SourceInspectionError):
            inspect_source(p, "   ")
        with self.assertRaises(SourceInspectionError):
            inspect_source(p, None)  # type: ignore
        with self.assertRaises(SourceInspectionError):
            inspect_source(p, 123)  # type: ignore
        with self.assertRaises(SourceInspectionError) as ctx_null:
            inspect_source(p, "DE_time" + chr(0))
        self.assertIn("null byte", str(ctx_null.exception).lower())
        with self.assertRaises(SourceInspectionError) as ctx_surr:
            inspect_source(p, "A" + chr(0xD800))
        self.assertIn("surrogate", str(ctx_surr.exception).lower())
        with self.assertRaises(SourceInspectionError) as ctx_len:
            inspect_source(p, "x" * 257)
        self.assertIn("exceeds 256-byte limit", str(ctx_len.exception).lower())

    def test_snapshot_cap_exceeded_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "large.mat"
        # Create sparse file > 50,000,000 bytes
        with p.open("wb") as f:
            f.seek(MAX_SNAPSHOT_BYTES)
            f.write(b"\x00")
        self.assertGreater(p.stat().st_size, MAX_SNAPSHOT_BYTES)

        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(p, "vib")
        self.assertIn("exceeds snapshot cap", str(ctx.exception).lower())

    def test_timeout_deadline_kills_worker_and_returns_resource_limit(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"v": np.array([[1.0]])}))

        # Mock worker that sleeps for 1.0 second, with 0.1 second deadline
        mock_cmd = [sys.executable, "-c", "import time; time.sleep(1.0)"]
        t0 = time.monotonic()
        rep = inspect_source(p, "v", timeout=0.1, _worker_cmd=mock_cmd)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 1.0)
        validate_exploratory_source_inspection(rep)

        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_RESOURCE_LIMIT")
        self.assertIsNone(rep["container_facts"]["numeric_dtype"])
        self.assertIsNone(rep["container_facts"]["numeric_shape"])
        self.assertIsNone(rep["container_facts"]["sample_count"])
        self.assertIsNone(rep["container_facts"]["observed_raw_extrema"])

    def test_worker_nonzero_exit_code_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))

        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", "import sys; sys.exit(13)"],
            )
        self.assertIn("exit code 13", str(ctx.exception))

    def test_worker_invalid_json_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))

        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", "import sys; sys.stdout.write('corrupted non-json output'); sys.exit(0)"],
            )
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_worker_stdout_overflow_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))

        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", "import sys; sys.stdout.write('x' * 2_500_000); sys.exit(0)"],
            )
        self.assertIn("IPC payload limit", str(ctx.exception))

    def test_worker_stderr_overflow_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))

        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", "import sys; sys.stderr.write('e' * 1_500_000); sys.exit(0)"],
            )
        self.assertIn("IPC payload limit", str(ctx.exception))

    def test_directory_input_rejected_with_source_inspection_error(self) -> None:
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(self.dir_path, "sig")
        self.assertIn("not a regular file", str(ctx.exception).lower())

    def test_worker_emits_null_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", "import sys; sys.stdout.write('null'); sys.exit(0)"],
            )
        self.assertIn("invalid payload type NoneType", str(ctx.exception))

    def test_worker_emits_array_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", "import sys; sys.stdout.write('[]'); sys.exit(0)"],
            )
        self.assertIn("invalid payload type list", str(ctx.exception))

    def test_worker_emits_unknown_status_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "UNKNOWN_STATUS",
            "container_format": "MATLAB_V5",
            "available_variables": [],
            "notes": ["Unknown status note."],
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("unknown structural status 'UNKNOWN_STATUS'", str(ctx.exception))

    def test_worker_emits_inconsistent_metadata_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        leaky_payload = json.dumps({
            "status": "UNSUPPORTED_STRUCTURE",
            "container_format": "MATLAB_V5",
            "available_variables": [],
            "numeric_dtype": "float64",
            "notes": ["Leaked dtype note."],
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(leaky_payload)}); sys.exit(0)"],
            )
        self.assertIn("unexpected 'numeric_dtype' for non-success status", str(ctx.exception))

    def test_worker_closes_pipes_and_sleeps_classified_as_resource_limit(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        mock_cmd = [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.close(); sys.stderr.close(); time.sleep(5.0)",
        ]
        t0 = time.monotonic()
        rep = inspect_source(p, "sig", timeout=0.2, _worker_cmd=mock_cmd)
        elapsed = time.monotonic() - t0

        self.assertLess(elapsed, 4.0)
        validate_exploratory_source_inspection(rep)
        findings = rep["inspection_findings"]
        self.assertEqual(findings["structural_status"], "RESOURCE_LIMIT_EXCEEDED")
        self.assertEqual(findings["measurement_readiness"], "BLOCKED_RESOURCE_LIMIT")
        self.assertIsNone(rep["container_facts"]["numeric_dtype"])
        self.assertIsNone(rep["container_facts"]["numeric_shape"])

    def test_symlink_input_path_rejected_with_source_inspection_error(self) -> None:
        real_file = self.dir_path / "real.mat"
        real_file.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        symlink_file = self.dir_path / "symlink.mat"
        symlink_file.symlink_to(real_file)

        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(symlink_file, "sig")
        self.assertIn("symbolic link input paths are rejected by security policy", str(ctx.exception).lower())

    def test_worker_emits_status_list_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": [],
            "container_format": "MATLAB_V5",
            "available_variables": [],
            "notes": [],
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("status must be str", str(ctx.exception).lower())

    def test_worker_emits_status_dict_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": {},
            "container_format": "MATLAB_V5",
            "available_variables": [],
            "notes": [],
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("status must be str", str(ctx.exception).lower())

    def test_worker_emits_invalid_container_format_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "OTHER",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("invalid container_format", str(ctx.exception).lower())

    def test_worker_emits_incompatible_container_format_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "UNRECOGNIZED",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("requires container_format 'matlab_v5'", str(ctx.exception).lower())

    def test_worker_emits_missing_required_fields_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({"status": "STRUCTURAL_SUCCESS"})
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("missing required key", str(ctx.exception).lower())

    def test_worker_emits_boolean_dimension_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [True, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("invalid numeric_shape", str(ctx.exception).lower())

    def test_worker_emits_boolean_sample_count_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": True,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("sample_count true invalid", str(ctx.exception).lower())

    def test_worker_emits_zero_or_negative_dimension_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [0, 2],
            "sample_count": 0,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("invalid numeric_shape", str(ctx.exception).lower())

    def test_worker_emits_inconsistent_shape_and_sample_count_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 5,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("does not match shape", str(ctx.exception).lower())

    def test_worker_emits_nan_extrema_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        # Raw JSON with NaN
        bad_json = (
            '{"status": "STRUCTURAL_SUCCESS", "container_format": "MATLAB_V5", '
            '"available_variables": ["sig"], "notes": [], "numeric_dtype": "float64", '
            '"numeric_shape": [1, 2], "sample_count": 2, "nonfinite_sample_count": 0, '
            '"observed_raw_extrema": {"min": NaN, "max": 2.0}}'
        )
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_json)}); sys.exit(0)"],
            )
        self.assertTrue("nonstandard json constant" in str(ctx.exception).lower() or "finite numbers" in str(ctx.exception).lower())

    def test_worker_emits_reversed_extrema_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 5.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("extrema min (5.0) > max (2.0)", str(ctx.exception).lower())

    def test_worker_emits_target_not_in_available_variables_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["other_var"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("not in available_variables", str(ctx.exception).lower())

    def test_worker_emits_inconsistent_nonfinite_counts_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 1,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("structural_success reported non-zero nonfinite_sample_count", str(ctx.exception).lower())

    def test_worker_emits_all_nonfinite_with_non_null_extrema_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "NONFINITE_VALUES_DETECTED",
            "container_format": "MATLAB_V5",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 2,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("non-null extrema when all samples are non-finite", str(ctx.exception).lower())

    def test_worker_emits_oversized_variable_list_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        vars_1025 = [f"var_{i}" for i in range(1025)]
        bad_payload = json.dumps({
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": vars_1025,
            "notes": [],
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("exceeds limit of 1024", str(ctx.exception).lower())

    def test_worker_emits_oversized_notes_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": [],
            "notes": ["x" * 3000],
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("exceeds 2048 characters", str(ctx.exception).lower())

    def test_worker_emits_primitive_payload_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        for prim in ("123", '"plain string"', "true", "false"):
            with self.assertRaises(SourceInspectionError) as ctx:
                inspect_source(
                    p,
                    "sig",
                    _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(prim)}); sys.exit(0)"],
                )
            self.assertIn("expected dict", str(ctx.exception).lower())

    def test_path_replaced_with_symlink_during_worker_execution_preserves_acquisition_identity(self) -> None:
        real_p = self.dir_path / "original.mat"
        real_p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        original_sha256 = hashlib.sha256(real_p.read_bytes()).hexdigest()
        original_uri = real_p.resolve().as_uri()

        other_p = self.dir_path / "other.mat"
        other_p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[99.0, 100.0]], dtype=np.float64)}))
        other_uri = other_p.resolve().as_uri()

        worker_script = (
            f"import sys, os, json\n"
            f"from pathlib import Path\n"
            f"from adi_app.source_inspection import _worker_preflight_and_extract, MAX_SNAPSHOT_BYTES\n"
            f"p = Path({repr(str(real_p))})\n"
            f"bak = Path({repr(str(real_p))} + '.bak')\n"
            f"os.rename(p, bak)\n"
            f"os.symlink({repr(str(other_p))}, p)\n"
            f"raw_bytes = sys.stdin.buffer.read(MAX_SNAPSHOT_BYTES + 1)\n"
            f"res = _worker_preflight_and_extract(raw_bytes, 'sig')\n"
            f"sys.stdout.write(json.dumps(res))\n"
            f"sys.exit(0)\n"
        )
        rep = inspect_source(real_p, "sig", _worker_cmd=[sys.executable, "-c", worker_script])
        self.assertEqual(rep["source_identity"]["raw_byte_sha256"], original_sha256)
        self.assertEqual(rep["source_identity"]["source_locator"], original_uri)
        self.assertNotEqual(rep["source_identity"]["source_locator"], other_uri)
        self.assertEqual(rep["container_facts"]["numeric_dtype"], "float64")
        self.assertAlmostEqual(rep["container_facts"]["observed_raw_extrema"]["min"], 1.0)
        self.assertAlmostEqual(rep["container_facts"]["observed_raw_extrema"]["max"], 2.0)

    def test_direct_validator_unhashable_available_variables_raises_source_inspection_error(self) -> None:
        for bad_var in ([], {}, None, 123, True, False, 1.5, [1, 2], {"a": 1}):
            payload = {
                "status": "TARGET_VARIABLE_NOT_FOUND",
                "container_format": "MATLAB_V5",
                "available_variables": [bad_var],
                "notes": [],
            }
            with self.assertRaises(SourceInspectionError) as ctx:
                _validate_worker_protocol_payload(payload, "DE_time")
            self.assertIn("must be str", str(ctx.exception))

    def test_direct_validator_mixed_unhashable_available_variables(self) -> None:
        payload = {
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": ["valid_var", []],
            "notes": [],
        }
        with self.assertRaises(SourceInspectionError) as ctx:
            _validate_worker_protocol_payload(payload, "DE_time")
        self.assertIn("must be str", str(ctx.exception))

    def test_worker_emits_unhashable_available_variables_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        for bad_vars in ([[]], [{}], [None], [True]):
            bad_payload = json.dumps({
                "status": "TARGET_VARIABLE_NOT_FOUND",
                "container_format": "MATLAB_V5",
                "available_variables": bad_vars,
                "notes": [],
            })
            with self.assertRaises(SourceInspectionError) as ctx:
                inspect_source(
                    p,
                    "sig",
                    _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
                )
            self.assertIn("must be str", str(ctx.exception))

    def test_direct_validator_surrogate_variable_name_raises_source_inspection_error(self) -> None:
        payload = {
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": [chr(0xD800)],
            "notes": [],
        }
        with self.assertRaises(SourceInspectionError) as ctx:
            _validate_worker_protocol_payload(payload, "DE_time")
        self.assertIn("surrogate", str(ctx.exception).lower())

    def test_worker_emits_surrogate_variable_name_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": [chr(0xD800)],
            "notes": [],
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("surrogate", str(ctx.exception).lower())

    def test_direct_validator_success_keys_present_on_failure_raises_source_inspection_error(self) -> None:
        success_keys = (
            "numeric_dtype",
            "numeric_shape",
            "sample_count",
            "nonfinite_sample_count",
            "observed_raw_extrema",
        )
        for key in success_keys:
            for val in (None, "float64" if key == "numeric_dtype" else 1):
                payload = {
                    "status": "TARGET_VARIABLE_NOT_FOUND",
                    "container_format": "MATLAB_V5",
                    "available_variables": [],
                    "notes": [],
                    key: val,
                }
                with self.assertRaises(SourceInspectionError) as ctx:
                    _validate_worker_protocol_payload(payload, "DE_time")
                self.assertIn(f"unexpected '{key}'", str(ctx.exception).lower())

    def test_worker_emits_null_success_keys_on_failure_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": [],
            "notes": [],
            "numeric_dtype": None,
            "sample_count": None,
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("unexpected 'numeric_dtype'", str(ctx.exception).lower())

    def test_valid_multibyte_utf8_variable_name_succeeds(self) -> None:
        self.assertEqual(_validate_variable_name_string("vibración_α"), "vibración_α")
        p = self.dir_path / "utf8.mat"
        p.write_bytes(_make_mat_level5_bytes({"x": np.array([[1.0, 2.0]], dtype=np.float64)}))
        rep = inspect_source(p, "vibración_α")
        self.assertEqual(rep["inspection_findings"]["structural_status"], "TARGET_VARIABLE_NOT_FOUND")
        self.assertEqual(rep["container_facts"]["target_variable"], "vibración_α")
        self.assertIn("x", rep["container_facts"]["available_variables"])

        # Also verify worker protocol accepts valid multibyte UTF-8
        payload = {
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["vibración_α"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        }
        res = _validate_worker_protocol_payload(payload, "vibración_α")
        self.assertEqual(res["available_variables"], ["vibración_α"])

    def test_direct_validator_oversized_and_invalid_extrema(self) -> None:
        base_payload = {
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["DE_time"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 1],
            "sample_count": 1,
            "nonfinite_sample_count": 0,
        }
        invalid_extrema = [
            {"min": 0, "max": 10**400},
            {"min": -10**400, "max": 0},
            {"min": 0, "max": 10**10000},
            {"min": float("inf"), "max": 1.0},
            {"min": 0.0, "max": float("inf")},
            {"min": float("-inf"), "max": 0.0},
            {"min": float("nan"), "max": 1.0},
            {"min": 0.0, "max": float("nan")},
            {"min": True, "max": 10.0},
            {"min": 0.0, "max": False},
            {"min": 10.0, "max": 5.0},
        ]
        for ext in invalid_extrema:
            pl = dict(base_payload)
            pl["observed_raw_extrema"] = ext
            with self.assertRaises(SourceInspectionError) as ctx:
                _validate_worker_protocol_payload(pl, "DE_time")
            self.assertTrue(
                "finite numbers" in str(ctx.exception)
                or "min" in str(ctx.exception)
            )

        valid_extrema = [
            {"min": -1e308, "max": 1e308},
            {"min": 0, "max": 0},
            {"min": -5.5, "max": 5.5},
        ]
        for ext in valid_extrema:
            pl = dict(base_payload)
            pl["observed_raw_extrema"] = ext
            validated = _validate_worker_protocol_payload(pl, "DE_time")
            self.assertEqual(validated["status"], "STRUCTURAL_SUCCESS")

    def test_worker_emits_oversized_extrema_raises_source_inspection_error(self) -> None:
        p = self.dir_path / "dummy.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        bad_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["sig"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 0, "max": 10**400},
        })
        with self.assertRaises(SourceInspectionError) as ctx:
            inspect_source(
                p,
                "sig",
                _worker_cmd=[sys.executable, "-c", f"import sys; sys.stdout.write({repr(bad_payload)}); sys.exit(0)"],
            )
        self.assertIn("finite numbers", str(ctx.exception))

    def test_parent_directory_symlink_swap_before_open_detected_and_rejected(self) -> None:
        dir_a = self.dir_path / "dir_a"
        dir_b = self.dir_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()

        file_a = dir_a / "source.mat"
        file_b = dir_b / "source.mat"
        file_a.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        file_b.write_bytes(_make_mat_level5_bytes({"sig": np.array([[99.0, 100.0]], dtype=np.float64)}))

        alias = self.dir_path / "alias"
        os.symlink(dir_a, alias)

        test_path = alias / "source.mat"
        orig_open = os.open

        def mock_open(file, flags, *args, **kwargs):
            if "alias" in str(file):
                os.unlink(alias)
                os.symlink(dir_b, alias)
            return orig_open(file, flags, *args, **kwargs)

        from unittest.mock import patch
        with patch("adi_app.source_inspection.os.open", side_effect=mock_open):
            with self.assertRaises(SourceInspectionError) as ctx:
                inspect_source(test_path, "sig")
        self.assertIn("identity changed before descriptor acquisition", str(ctx.exception).lower())

    def test_parent_directory_symlink_without_swap_succeeds(self) -> None:
        dir_a = self.dir_path / "dir_a"
        dir_a.mkdir()
        file_a = dir_a / "source.mat"
        file_a.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))
        file_a_sha256 = hashlib.sha256(file_a.read_bytes()).hexdigest()
        file_a_uri = file_a.resolve().as_uri()

        alias = self.dir_path / "alias_stable"
        os.symlink(dir_a, alias)
        test_path = alias / "source.mat"

        rep = inspect_source(test_path, "sig")
        self.assertEqual(rep["source_identity"]["raw_byte_sha256"], file_a_sha256)
        self.assertEqual(rep["source_identity"]["source_locator"], file_a_uri)
        self.assertEqual(rep["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")


class TestDeterminismAndContracts(unittest.TestCase):
    """Tests for deterministic identification and complete contract invariants."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_repeatable_execution_and_inspection_id(self) -> None:
        arr = np.array([[1.0, 2.0, 3.0]], dtype=np.float64)
        p = self.dir_path / "repeat.mat"
        p.write_bytes(_make_mat_level5_bytes({"var1": arr, "var2": arr}))

        rep1 = inspect_source(p, "var1")
        time.sleep(0.01)
        rep2 = inspect_source(p, "var1")

        self.assertEqual(rep1["inspection_id"], rep2["inspection_id"])
        self.assertEqual(rep1["source_identity"], rep2["source_identity"])
        self.assertEqual(rep1["container_facts"], rep2["container_facts"])
        self.assertEqual(rep1["metadata_status_ledger"], rep2["metadata_status_ledger"])
        self.assertEqual(rep1["inspection_findings"], rep2["inspection_findings"])

        # Different variable produces different deterministic inspection_id
        rep_diff_var = inspect_source(p, "var2")
        self.assertNotEqual(rep1["inspection_id"], rep_diff_var["inspection_id"])

    def test_strict_json_serialization_allow_nan_false(self) -> None:
        arr = np.array([[np.nan, 1.0]], dtype=np.float64)
        p = self.dir_path / "nan_case.mat"
        p.write_bytes(_make_mat_level5_bytes({"sig": arr}))

        rep = inspect_source(p, "sig")
        # Must serialize strictly without raising ValueError on NaN/Inf
        dumped = json.dumps(rep, allow_nan=False)

        def _reject_const(c: str) -> None:
            raise ValueError(f"Nonstandard constant found: {c}")

        # Strict JSON deserialization rejecting any nonstandard constant
        parsed = json.loads(dumped, parse_constant=_reject_const)
        self.assertIsInstance(parsed, dict)
        self.assertEqual(parsed["container_facts"]["nonfinite_sample_count"], 1)


class TestCLIInspectSource(unittest.TestCase):
    """End-to-end CLI integration tests for adi-portfolio inspect-source."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_mat(self, name: str, var: str, arr: np.ndarray) -> Path:
        p = self.dir_path / name
        p.write_bytes(_make_mat_level5_bytes({var: arr}))
        return p

    def test_cli_structural_success_exit_code_0(self) -> None:
        p = self._write_mat("success.mat", "x", np.array([[1.0, 2.0, 3.0]], dtype=np.float64))
        code, stdout, stderr = _run_cli(["inspect-source", "--mat", str(p), "--variable", "x"])

        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        doc = json.loads(stdout)
        self.assertEqual(doc["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")

    def test_cli_non_success_exit_code_1(self) -> None:
        p = self._write_mat("not_found.mat", "alpha", np.array([[1.0, 2.0]], dtype=np.float64))
        code, stdout, stderr = _run_cli(["inspect-source", "--mat", str(p), "--variable", "beta"])

        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        doc = json.loads(stdout)
        self.assertEqual(doc["inspection_findings"]["structural_status"], "TARGET_VARIABLE_NOT_FOUND")

    def test_cli_out_file_success_atomic(self) -> None:
        p = self._write_mat("success.mat", "x", np.array([[1.0, 2.0, 3.0]], dtype=np.float64))
        out_p = self.dir_path / "report.json"
        self.assertFalse(out_p.exists())

        code, stdout, stderr = _run_cli(
            ["inspect-source", "--mat", str(p), "--variable", "x", "--out", str(out_p)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertTrue(out_p.exists())

        file_doc = json.loads(out_p.read_text(encoding="utf-8"))
        stdout_doc = json.loads(stdout)
        self.assertEqual(file_doc, stdout_doc)
        self.assertEqual(file_doc["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")

    def test_cli_out_file_non_success_atomic(self) -> None:
        p = self._write_mat("bad.mat", "a", np.array([[1.0, 2.0]], dtype=np.float64))
        out_p = self.dir_path / "bad_report.json"

        code, stdout, stderr = _run_cli(
            ["inspect-source", "--mat", str(p), "--variable", "missing_var", "--out", str(out_p)]
        )
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertTrue(out_p.exists())

        file_doc = json.loads(out_p.read_text(encoding="utf-8"))
        self.assertEqual(file_doc["inspection_findings"]["structural_status"], "TARGET_VARIABLE_NOT_FOUND")

    def test_cli_out_file_already_exists_exit_code_2(self) -> None:
        p = self._write_mat("success.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        out_p = self.dir_path / "existing.json"
        out_p.write_text("existing content", encoding="utf-8")

        code, stdout, stderr = _run_cli(
            ["inspect-source", "--mat", str(p), "--variable", "x", "--out", str(out_p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("already exists", stderr.lower())
        self.assertEqual(out_p.read_text(encoding="utf-8"), "existing content")

    def test_cli_out_file_same_as_mat_exit_code_2(self) -> None:
        p = self._write_mat("source.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        orig_bytes = p.read_bytes()

        code, stdout, stderr = _run_cli(
            ["inspect-source", "--mat", str(p), "--variable", "x", "--out", str(p)]
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("cannot be identical to input", stderr.lower())
        self.assertEqual(p.read_bytes(), orig_bytes)

    def test_cli_missing_arguments_exit_code_2(self) -> None:
        # Missing --variable
        code, stdout, stderr = _run_cli(["inspect-source", "--mat", "dummy.mat"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("required", stderr.lower())

        # Missing --mat
        code2, stdout2, stderr2 = _run_cli(["inspect-source", "--variable", "sig"])
        self.assertEqual(code2, 2)
        self.assertEqual(stdout2, "")
        self.assertIn("required", stderr2.lower())

    def test_cli_missing_mat_file_exit_code_2(self) -> None:
        missing = self.dir_path / "no_such_file.mat"
        code, stdout, stderr = _run_cli(["inspect-source", "--mat", str(missing), "--variable", "sig"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("not found", stderr.lower())

    def test_cli_snapshot_cap_exceeded_exit_code_2(self) -> None:
        large_p = self.dir_path / "huge.mat"
        with large_p.open("wb") as f:
            f.seek(MAX_SNAPSHOT_BYTES)
            f.write(b"\x00")

        code, stdout, stderr = _run_cli(["inspect-source", "--mat", str(large_p), "--variable", "sig"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("snapshot cap", stderr.lower())

    def test_cli_existing_commands_preserved(self) -> None:
        # demo
        code_demo, stdout_demo, _ = _run_cli(["demo"])
        self.assertEqual(code_demo, 0)
        self.assertIn("scaffold only", stdout_demo.lower())

        # benchmark
        code_bm, stdout_bm, _ = _run_cli(["benchmark"])
        self.assertEqual(code_bm, 1)
        self.assertIn("NOT_IMPLEMENTED", stdout_bm)

        # evaluate without suite
        code_ev, stdout_ev, _ = _run_cli(["evaluate"])
        self.assertEqual(code_ev, 1)
        self.assertIn("NOT_IMPLEMENTED", stdout_ev)

    def test_cli_worker_crash_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 13\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=(b'', b'Worker crash 13', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("exit code 13", res.stderr.lower())

    def test_cli_malformed_container_returns_exit_code_1_with_valid_report(self) -> None:
        p = self.dir_path / "bad.mat"
        p.write_bytes(b"MATLAB 5.0 truncated header")
        code, stdout, stderr = _run_cli(["inspect-source", "--mat", str(p), "--variable", "sig"])
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        doc = json.loads(stdout)
        self.assertEqual(doc["inspection_findings"]["structural_status"], "MALFORMED_CONTAINER")

    def test_cli_out_file_link_collision_handles_gracefully(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        out_p = self.dir_path / "race_report.json"
        valid_payload = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["x"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"def fake_link(src, dst):\n"
            f"    raise FileExistsError('Race collision')\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=({repr(valid_payload.encode('utf-8'))}, b'', False, False)):\n"
            f"        with patch('os.link', side_effect=fake_link):\n"
            f"            sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x', '--out', {repr(str(out_p))}]\n"
            f"            sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 2)
        self.assertIn("already exists and will not be overwritten", res.stderr.lower())
        temp_files = list(self.dir_path.glob(".*tmp*"))
        self.assertEqual(temp_files, [])

    def test_cli_symlink_input_exit_code_2(self) -> None:
        real_file = self.dir_path / "real_cli.mat"
        real_file.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]])}))
        symlink_file = self.dir_path / "symlink_cli.mat"
        symlink_file.symlink_to(real_file)

        code, stdout, stderr = _run_cli(["inspect-source", "--mat", str(symlink_file), "--variable", "sig"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("symbolic link input paths are rejected by security policy", stderr.lower())

    def test_cli_worker_null_output_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=(b'null', b'', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("invalid payload type nonetype", res.stderr.lower())

    def test_cli_worker_closes_pipes_and_sleeps_exit_code_1(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        test_script = (
            f"from unittest.mock import patch\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"with patch('adi_app.source_inspection._communicate_bounded', return_value=(b'', b'', True, False)):\n"
            f"    sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"    sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 1)
        self.assertEqual(res.stderr, "")
        doc = json.loads(res.stdout)
        self.assertEqual(doc["inspection_findings"]["structural_status"], "RESOURCE_LIMIT_EXCEEDED")

    def test_cli_worker_status_list_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        bad_json = json.dumps({"status": [], "container_format": "MATLAB_V5", "available_variables": [], "notes": []})
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=({repr(bad_json.encode('utf-8'))}, b'', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("status must be str", res.stderr.lower())

    def test_cli_worker_invalid_container_format_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        bad_json = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "OTHER",
            "available_variables": ["x"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=({repr(bad_json.encode('utf-8'))}, b'', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("invalid container_format", res.stderr.lower())

    def test_cli_worker_boolean_dimension_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        bad_json = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["x"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [True, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 1.0, "max": 2.0},
        })
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=({repr(bad_json.encode('utf-8'))}, b'', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("invalid numeric_shape", res.stderr.lower())

    def test_cli_worker_unhashable_available_variables_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        bad_json = json.dumps({
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": [[]],
            "notes": [],
        })
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=({repr(bad_json.encode('utf-8'))}, b'', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("must be str", res.stderr.lower())

    def test_cli_worker_oversized_extrema_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        bad_json = json.dumps({
            "status": "STRUCTURAL_SUCCESS",
            "container_format": "MATLAB_V5",
            "available_variables": ["x"],
            "notes": [],
            "numeric_dtype": "float64",
            "numeric_shape": [1, 2],
            "sample_count": 2,
            "nonfinite_sample_count": 0,
            "observed_raw_extrema": {"min": 0, "max": 10**400},
        })
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=({repr(bad_json.encode('utf-8'))}, b'', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("finite numbers", res.stderr.lower())

    def test_cli_invalid_target_variable_null_byte_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        test_script = (
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x\\x00']\n"
            f"sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("null byte", res.stderr.lower())

    def test_cli_worker_surrogate_variable_name_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        bad_json = json.dumps({
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": [chr(0xD800)],
            "notes": [],
        })
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=({repr(bad_json.encode('utf-8'))}, b'', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("surrogate", res.stderr.lower())

    def test_cli_worker_null_success_key_on_failure_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        bad_json = json.dumps({
            "status": "TARGET_VARIABLE_NOT_FOUND",
            "container_format": "MATLAB_V5",
            "available_variables": [],
            "notes": [],
            "numeric_dtype": None,
        })
        test_script = (
            f"from unittest.mock import patch, MagicMock\n"
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"mock_proc = MagicMock()\n"
            f"mock_proc.returncode = 0\n"
            f"with patch('adi_app.source_inspection.subprocess.Popen', return_value=mock_proc):\n"
            f"    with patch('adi_app.source_inspection._communicate_bounded', return_value=({repr(bad_json.encode('utf-8'))}, b'', False, False)):\n"
            f"        sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x']\n"
            f"        sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("unexpected 'numeric_dtype'", res.stderr.lower())

    def test_cli_invalid_mat_path_null_byte_exit_code_2(self) -> None:
        test_script = (
            "import sys\n"
            "from adi_app.portfolio import main\n"
            "sys.argv = ['adi-portfolio', 'inspect-source', '--mat', 'invalid\\x00.mat', '--variable', 'x']\n"
            "sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("null byte", res.stderr.lower())

    def test_cli_invalid_mat_path_surrogate_exit_code_2(self) -> None:
        test_script = (
            "import sys\n"
            "from adi_app.portfolio import main\n"
            "sys.argv = ['adi-portfolio', 'inspect-source', '--mat', 'invalid\\ud800.mat', '--variable', 'x']\n"
            "sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("surrogate", res.stderr.lower())

    def test_cli_invalid_out_path_null_byte_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        test_script = (
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x', '--out', 'out\\x00.json']\n"
            f"sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("null byte", res.stderr.lower())

    def test_cli_invalid_out_path_surrogate_exit_code_2(self) -> None:
        p = self._write_mat("sample.mat", "x", np.array([[1.0, 2.0]], dtype=np.float64))
        test_script = (
            f"import sys\n"
            f"from adi_app.portfolio import main\n"
            f"sys.argv = ['adi-portfolio', 'inspect-source', '--mat', {repr(str(p))}, '--variable', 'x', '--out', 'out\\ud800.json']\n"
            f"sys.exit(main())\n"
        )
        res = subprocess.run([sys.executable, "-c", test_script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(res.stdout, "")
        self.assertIn("surrogate", res.stderr.lower())

    def test_cli_legitimate_unicode_path_exit_code_0(self) -> None:
        unicode_dir = self.dir_path / "données_mesures_α"
        unicode_dir.mkdir()
        unicode_mat = unicode_dir / "vibration_échantillon_β.mat"
        unicode_mat.write_bytes(_make_mat_level5_bytes({"sig": np.array([[1.0, 2.0]], dtype=np.float64)}))

        code, stdout, stderr = _run_cli(["inspect-source", "--mat", str(unicode_mat), "--variable", "sig"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        doc = json.loads(stdout)
        self.assertEqual(doc["inspection_findings"]["structural_status"], "STRUCTURAL_SUCCESS")
        self.assertEqual(doc["source_identity"]["source_filename"], "vibration_échantillon_β.mat")


if __name__ == "__main__":
    unittest.main()
