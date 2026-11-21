"""Comprehensive test suite for ADI App recording ingestion.

Tests local CSV loading, strict metadata validation, source-byte SHA-256 integrity,
whitespace and blank-line policies, UTF-8 BOM/CRLF decoding, channel permutations,
rejection of all malformed-input categories, caller/source immutability,
and pipeline feeding into adi-runtime window feature extraction.

All generated fixtures are strictly designated as synthetic test data. Fixtures
using provenance='recorded' test metadata handling only and are not evidence of
real sensor acquisition.
"""

from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
import tempfile
import unittest

from adi_app.ingestion import (
    IngestionDigestMismatchError,
    IngestionFileNotFoundError,
    IngestionMetadataError,
    IngestionParseError,
    RecordingIngestionError,
    load_vibration_csv,
)
from adi_runtime import extract_window_features


class TestRecordingIngestion(unittest.TestCase):
    """Test suite for CSV ingestion, byte hashing, and contract assembly."""

    def setUp(self):
        self.valid_metadata_1ch = {
            "schema_version": "1.0.0",
            "recording_id": "rec-synth-001",
            "machine_id": "pump-west-01",
            "session_id": "sess-bench-01",
            "source_id": "synth-gen-sine-sweep",
            "provenance": "synthetic",
            "sample_rate_hz": 2000.0,
            "acceleration_unit": "g",
        }

        self.valid_metadata_3ch = {
            "schema_version": "1.0.0",
            "recording_id": "rec-test-002",
            "machine_id": "cnc-spindle-01",
            "session_id": "sess-diag-02",
            "source_id": "synthetic-sim-adxl355",
            "provenance": "synthetic",
            "sample_rate_hz": 4000.0,
            "acceleration_unit": "m/s^2",
        }

    # -------------------------------------------------------------------------
    # Valid CSV Formats & Channel Permutations
    # -------------------------------------------------------------------------

    def test_valid_single_channel_csv(self):
        """TC-INGEST-01: Valid single-channel CSV parses into verified recording."""
        content = "x\n0.012\n0.015\n-0.008\n0.021\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "recording_1ch.csv"
            csv_file.write_text(content, encoding="utf-8")

            recording = load_vibration_csv(csv_file, self.valid_metadata_1ch)
            self.assertEqual(recording["schema_version"], "1.0.0")
            self.assertEqual(recording["channels"], ["x"])
            self.assertEqual(
                recording["samples"],
                [[0.012], [0.015], [-0.008], [0.021]],
            )
            self.assertEqual(recording["acceleration_unit"], "g")
            self.assertEqual(recording["sample_rate_hz"], 2000.0)
            self.assertEqual(
                recording["source_sha256"],
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
            )

    def test_valid_three_channel_csv(self):
        """TC-INGEST-02: Valid 3-channel CSV parses with correct rectangular matrix."""
        content = "x,y,z\n0.12,-0.05,9.81\n0.15,-0.04,9.80\n0.11,-0.06,9.82\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "recording_3ch.csv"
            csv_file.write_text(content, encoding="utf-8")

            recording = load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertEqual(recording["channels"], ["x", "y", "z"])
            self.assertEqual(len(recording["samples"]), 3)
            self.assertEqual(recording["samples"][0], [0.12, -0.05, 9.81])
            self.assertEqual(recording["samples"][1], [0.15, -0.04, 9.80])
            self.assertEqual(recording["samples"][2], [0.11, -0.06, 9.82])

    def test_reordered_channels_preserve_values(self):
        """TC-INGEST-03: Reordered channels preserve values in header order without transposition."""
        content_zyx = "z,y,x\n9.81,-0.05,0.12\n9.80,-0.04,0.15\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "reordered_zyx.csv"
            csv_file.write_text(content_zyx, encoding="utf-8")

            recording = load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertEqual(recording["channels"], ["z", "y", "x"])
            self.assertEqual(recording["samples"][0], [9.81, -0.05, 0.12])
            self.assertEqual(recording["samples"][1], [9.80, -0.04, 0.15])

        content_yx = "y,x\n10.5,-1.2\n20.5,-2.2\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "reordered_yx.csv"
            csv_file.write_text(content_yx, encoding="utf-8")

            meta = copy.deepcopy(self.valid_metadata_3ch)
            recording = load_vibration_csv(csv_file, meta)
            self.assertEqual(recording["channels"], ["y", "x"])
            self.assertEqual(recording["samples"][0], [10.5, -1.2])

    def test_scientific_notation_numeric_values(self):
        """TC-INGEST-04: Standard decimal scientific notation is accurately converted."""
        content = "x,y\n1.23e-4,-4.56E+02\n+1.0e+05,0.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "scientific.csv"
            csv_file.write_text(content, encoding="utf-8")

            recording = load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertAlmostEqual(recording["samples"][0][0], 0.000123)
            self.assertAlmostEqual(recording["samples"][0][1], -456.0)
            self.assertAlmostEqual(recording["samples"][1][0], 100000.0)
            self.assertEqual(recording["samples"][1][1], 0.0)

    # -------------------------------------------------------------------------
    # Documented Whitespace & Blank-Line Policies
    # -------------------------------------------------------------------------

    def test_whitespace_policy_trimmed_numeric_cells(self):
        """TC-INGEST-05: Leading and trailing whitespace around numeric values in cells is trimmed."""
        content = "x,y\n 1.0 ,  2.0 \n  3.5\t, -4.0  \n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "cell_spaces.csv"
            csv_file.write_text(content, encoding="utf-8")

            recording = load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertEqual(recording["samples"][0], [1.0, 2.0])
            self.assertEqual(recording["samples"][1], [3.5, -4.0])

    def test_whitespace_policy_reject_blank_lines(self):
        """TC-INGEST-06: Blank or whitespace-only lines are strictly rejected (never dropped)."""
        scenarios = [
            ("intermediate_empty_line", "x,y\n1.0,2.0\n\n3.0,4.0\n", 3),
            ("intermediate_spaces_line", "x,y\n1.0,2.0\n   \n3.0,4.0\n", 3),
            ("trailing_empty_line", "x,y\n1.0,2.0\n\n", 3),
            ("trailing_spaces_line", "x,y\n1.0,2.0\n   \n", 3),
            ("leading_empty_line", "\nx,y\n1.0,2.0\n", 1),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for name, text, expected_row in scenarios:
                csv_file = Path(tmp_dir) / f"{name}.csv"
                csv_file.write_text(text, encoding="utf-8")
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(csv_file, self.valid_metadata_3ch)
                self.assertIn("blank lines are forbidden", str(ctx.exception).lower())
                self.assertEqual(ctx.exception.row, expected_row)

    def test_whitespace_policy_reject_whitespace_in_header_tokens(self):
        """TC-INGEST-07: Header channel tokens must not contain leading or trailing whitespace."""
        scenarios = [
            " x,y\n1.0,2.0\n",
            "x ,y\n1.0,2.0\n",
            "x, y\n1.0,2.0\n",
            "x,\ty\n1.0,2.0\n",
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for idx, text in enumerate(scenarios):
                csv_file = Path(tmp_dir) / f"header_space_{idx}.csv"
                csv_file.write_text(text, encoding="utf-8")
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(csv_file, self.valid_metadata_3ch)
                self.assertIn("whitespace", str(ctx.exception).lower())
                self.assertEqual(ctx.exception.row, 1)

    # -------------------------------------------------------------------------
    # BOM & CRLF Decoding and Hashing Semantics
    # -------------------------------------------------------------------------

    def test_bom_handling_and_hashing(self):
        """TC-INGEST-08: UTF-8 BOM is stripped during decoding while hashed into raw byte digest."""
        bom_bytes = b"\xef\xbb\xbfx,y\n1.0,2.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "bom.csv"
            csv_file.write_bytes(bom_bytes)

            recording = load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertEqual(recording["channels"], ["x", "y"])
            self.assertEqual(recording["samples"], [[1.0, 2.0]])
            self.assertEqual(
                recording["source_sha256"],
                hashlib.sha256(bom_bytes).hexdigest(),
            )

    def test_crlf_handling_and_hashing(self):
        """TC-INGEST-09: Windows CRLF newlines are parsed cleanly with exact byte digest."""
        crlf_bytes = b"x,y\r\n1.0,2.0\r\n3.0,4.0\r\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "crlf.csv"
            csv_file.write_bytes(crlf_bytes)

            recording = load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertEqual(recording["channels"], ["x", "y"])
            self.assertEqual(recording["samples"], [[1.0, 2.0], [3.0, 4.0]])
            self.assertEqual(
                recording["source_sha256"],
                hashlib.sha256(crlf_bytes).hexdigest(),
            )

    def test_exact_byte_hash_checked_with_independent_hashlib(self):
        """TC-INGEST-10: source_sha256 strictly equals independent hashlib.sha256 of raw file bytes."""
        content = "x,y,z\n0.1,0.2,0.3\n-0.1,-0.2,-0.3\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "hash_check.csv"
            csv_file.write_text(content, encoding="utf-8")

            expected_hash = hashlib.sha256(csv_file.read_bytes()).hexdigest()
            recording = load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertEqual(recording["source_sha256"], expected_hash)

    def test_equivalent_numerical_csv_with_different_bytes_produces_different_hashes(self):
        """TC-INGEST-11: Equivalent numbers with distinct byte representations yield distinct SHA-256 digests."""
        cases = [
            ("lf_standard", b"x,y\n1.0,2.0\n"),
            ("crlf_newline", b"x,y\r\n1.0,2.0\r\n"),
            ("utf8_bom", b"\xef\xbb\xbfx,y\n1.0,2.0\n"),
            ("formatted_decimals", b"x,y\n1.00,2.000\n"),
            ("scientific_notation", b"x,y\n1e0,2e0\n"),
        ]
        recordings = []
        with tempfile.TemporaryDirectory() as tmp_dir:
            for name, raw_bytes in cases:
                path = Path(tmp_dir) / f"{name}.csv"
                path.write_bytes(raw_bytes)
                rec = load_vibration_csv(path, self.valid_metadata_3ch)
                # Numerical equality
                self.assertEqual(rec["samples"], [[1.0, 2.0]])
                recordings.append((name, rec["source_sha256"]))

        # Assert all 5 SHA-256 digests are strictly distinct
        hashes = [h for _, h in recordings]
        self.assertEqual(len(hashes), len(set(hashes)), "All hashes must be strictly pairwise unique")

    # -------------------------------------------------------------------------
    # Expected Digest Matching & Mismatch Verification
    # -------------------------------------------------------------------------

    def test_expected_digest_match(self):
        """TC-INGEST-12: Correct expected SHA-256 digest is accepted."""
        content = "x\n1.0\n2.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "digest_match.csv"
            csv_file.write_text(content, encoding="utf-8")
            actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

            recording = load_vibration_csv(
                csv_file, self.valid_metadata_1ch, expected_sha256=actual_hash
            )
            self.assertEqual(recording["source_sha256"], actual_hash)

    def test_expected_digest_mismatch_raises(self):
        """TC-INGEST-13: Mismatched expected SHA-256 raises IngestionDigestMismatchError."""
        content = "x\n1.0\n2.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "digest_mismatch.csv"
            csv_file.write_text(content, encoding="utf-8")
            wrong_hash = "0" * 64

            with self.assertRaises(IngestionDigestMismatchError) as ctx:
                load_vibration_csv(
                    csv_file, self.valid_metadata_1ch, expected_sha256=wrong_hash
                )
            self.assertIn("does not match expected", str(ctx.exception))

    def test_expected_digest_invalid_syntax_raises(self):
        """TC-INGEST-14: Malformed expected_sha256 syntax raises IngestionDigestMismatchError."""
        invalid_hashes = [
            "a" * 63,                   # 63 characters (too short)
            "a" * 65,                   # 65 characters (too long)
            "g" * 64,                   # Non-hex character
            "a" * 64 + "\n",            # Trailing newline
            " " + "a" * 63,             # Leading space
            "",                         # Empty string
            123456,                     # Non-string
        ]
        content = "x\n1.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "syntax.csv"
            csv_file.write_text(content, encoding="utf-8")

            for bad_hash in invalid_hashes:
                with self.assertRaises(IngestionDigestMismatchError):
                    load_vibration_csv(
                        csv_file, self.valid_metadata_1ch, expected_sha256=bad_hash
                    )

    # -------------------------------------------------------------------------
    # Rejection of All Malformed CSV Categories
    # -------------------------------------------------------------------------

    def test_reject_empty_cells(self):
        """TC-INGEST-15: Empty cells raise IngestionParseError with exact row and column."""
        scenarios = [
            ("trailing_comma", "x,y\n1.0,\n", 2, 2),
            ("leading_comma", "x,y\n,2.0\n", 2, 1),
            ("middle_comma", "x,y,z\n1.0,,3.0\n", 2, 2),
            ("whitespace_cell", "x,y\n1.0,   \n", 2, 2),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for name, text, r, c in scenarios:
                csv_file = Path(tmp_dir) / f"{name}.csv"
                csv_file.write_text(text, encoding="utf-8")
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(csv_file, self.valid_metadata_3ch)
                self.assertIn("empty cell", str(ctx.exception).lower())
                self.assertEqual(ctx.exception.row, r)
                self.assertEqual(ctx.exception.column, c)

    def test_reject_extra_and_missing_columns(self):
        """TC-INGEST-16: Column count mismatch against header raises IngestionParseError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Extra column
            p_extra = Path(tmp_dir) / "extra.csv"
            p_extra.write_text("x,y\n1.0,2.0,3.0\n", encoding="utf-8")
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(p_extra, self.valid_metadata_3ch)
            self.assertIn("expected 2", str(ctx.exception))
            self.assertEqual(ctx.exception.row, 2)

            # Missing column
            p_miss = Path(tmp_dir) / "missing.csv"
            p_miss.write_text("x,y,z\n1.0,2.0\n", encoding="utf-8")
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(p_miss, self.valid_metadata_3ch)
            self.assertIn("expected 3", str(ctx.exception))
            self.assertEqual(ctx.exception.row, 2)

    def test_reject_nan_and_infinity(self):
        """TC-INGEST-17: Non-finite numeric tokens raise IngestionParseError."""
        bad_tokens = ["NaN", "nan", "+NaN", "-nan", "Inf", "inf", "-inf", "+infinity", "Infinity"]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for token in bad_tokens:
                p = Path(tmp_dir) / f"nonfinite_{token}.csv"
                p.write_text(f"x\n{token}\n", encoding="utf-8")
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(p, self.valid_metadata_1ch)
                self.assertIn("non-finite", str(ctx.exception).lower())
                self.assertEqual(ctx.exception.row, 2)
                self.assertEqual(ctx.exception.column, 1)

    def test_reject_invalid_utf8_encoding(self):
        """TC-INGEST-18: Non-UTF8 byte sequences raise IngestionParseError."""
        bad_bytes = b"x,y\n\xff\xfe,1.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "invalid_utf8.csv"
            p.write_bytes(bad_bytes)
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(p, self.valid_metadata_3ch)
            self.assertIn("invalid utf-8 encoding", str(ctx.exception).lower())

    def test_reject_duplicate_and_unknown_channel_names(self):
        """TC-INGEST-19: Duplicate or unauthorized channel names raise IngestionParseError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Duplicate
            p_dup = Path(tmp_dir) / "dup.csv"
            p_dup.write_text("x,x\n1.0,2.0\n", encoding="utf-8")
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(p_dup, self.valid_metadata_3ch)
            self.assertIn("duplicate channel name 'x'", str(ctx.exception).lower())
            self.assertEqual(ctx.exception.row, 1)

            # Unknown channel name (uppercase, time, accel)
            for bad_chan in ("time", "a", "X", "accel_x"):
                p_bad = Path(tmp_dir) / f"chan_{bad_chan}.csv"
                p_bad.write_text(f"x,{bad_chan}\n1.0,2.0\n", encoding="utf-8")
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(p_bad, self.valid_metadata_3ch)
                self.assertIn(f"Invalid channel name '{bad_chan}'", str(ctx.exception))

    def test_reject_unsupported_numeric_syntax(self):
        """TC-INGEST-20: Unparseable numeric syntax raises IngestionParseError."""
        bad_syntax = ["abc", "0x12", '"1,000"', "None", "null", "1.2.3", "True", "False", "1 2"]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for idx, s in enumerate(bad_syntax):
                p = Path(tmp_dir) / f"syntax_{idx}.csv"
                p.write_text(f"x\n{s}\n", encoding="utf-8")
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(p, self.valid_metadata_1ch)
                self.assertIn("invalid decimal numeric syntax", str(ctx.exception).lower())
                self.assertEqual(ctx.exception.row, 2)
                self.assertEqual(ctx.exception.column, 1)

    def test_reject_empty_file_and_header_only_file(self):
        """TC-INGEST-21: Empty files and files without data rows raise IngestionParseError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            p_empty = Path(tmp_dir) / "empty.csv"
            p_empty.write_bytes(b"")
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(p_empty, self.valid_metadata_1ch)
            self.assertIn("empty", str(ctx.exception).lower())

            p_hdr_only = Path(tmp_dir) / "header_only.csv"
            p_hdr_only.write_text("x,y\n", encoding="utf-8")
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(p_hdr_only, self.valid_metadata_3ch)
            self.assertIn("no data rows", str(ctx.exception).lower())

    # -------------------------------------------------------------------------
    # Missing Files & Metadata Validation
    # -------------------------------------------------------------------------

    def test_missing_file_raises_filenotfound(self):
        """TC-INGEST-22: Nonexistent file or directory path raises IngestionFileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            p_nonexistent = Path(tmp_dir) / "does_not_exist.csv"
            with self.assertRaises(IngestionFileNotFoundError):
                load_vibration_csv(p_nonexistent, self.valid_metadata_1ch)

            with self.assertRaises(IngestionFileNotFoundError):
                load_vibration_csv(tmp_dir, self.valid_metadata_1ch)

    def test_invalid_metadata_scenarios_raise(self):
        """TC-INGEST-23: Missing/extra metadata keys or invalid types raise IngestionMetadataError."""
        content = "x\n1.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "valid.csv"
            csv_file.write_text(content, encoding="utf-8")

            # Missing required field
            for field in self.valid_metadata_1ch:
                bad_meta = copy.deepcopy(self.valid_metadata_1ch)
                del bad_meta[field]
                with self.assertRaises(IngestionMetadataError) as ctx:
                    load_vibration_csv(csv_file, bad_meta)
                self.assertIn("missing required metadata field", str(ctx.exception).lower())

            # Extra field
            extra_meta = copy.deepcopy(self.valid_metadata_1ch)
            extra_meta["unrecognized_field"] = "unexpected"
            with self.assertRaises(IngestionMetadataError) as ctx:
                load_vibration_csv(csv_file, extra_meta)
            self.assertIn("unknown metadata field", str(ctx.exception).lower())

            # Bad schema_version
            bad_ver = copy.deepcopy(self.valid_metadata_1ch)
            bad_ver["schema_version"] = "2.0.0"
            with self.assertRaises(IngestionMetadataError):
                load_vibration_csv(csv_file, bad_ver)

            # Bad provenance
            bad_prov = copy.deepcopy(self.valid_metadata_1ch)
            bad_prov["provenance"] = "measured"
            with self.assertRaises(IngestionMetadataError):
                load_vibration_csv(csv_file, bad_prov)

            # Bad acceleration_unit
            bad_unit = copy.deepcopy(self.valid_metadata_1ch)
            bad_unit["acceleration_unit"] = "m/s"
            with self.assertRaises(IngestionMetadataError):
                load_vibration_csv(csv_file, bad_unit)

            # Non-positive sample_rate_hz
            for bad_hz in (0, -100.0, float("nan"), float("inf"), True, False, "1000"):
                bad_rate = copy.deepcopy(self.valid_metadata_1ch)
                bad_rate["sample_rate_hz"] = bad_hz
                with self.assertRaises(IngestionMetadataError):
                    load_vibration_csv(csv_file, bad_rate)

            # Empty string identifier
            for id_field in ("recording_id", "machine_id", "session_id", "source_id"):
                bad_id = copy.deepcopy(self.valid_metadata_1ch)
                bad_id[id_field] = "   "
                with self.assertRaises(IngestionMetadataError):
                    load_vibration_csv(csv_file, bad_id)

    # -------------------------------------------------------------------------
    # Immutability Guarantees
    # -------------------------------------------------------------------------

    def test_caller_metadata_and_source_file_immutability(self):
        """TC-INGEST-24: Caller metadata dict and on-disk file bytes are not mutated."""
        content = "x,y\n1.0,2.0\n3.0,4.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "immutable.csv"
            csv_file.write_text(content, encoding="utf-8")

            orig_meta = copy.deepcopy(self.valid_metadata_3ch)
            caller_meta = copy.deepcopy(self.valid_metadata_3ch)
            orig_bytes = csv_file.read_bytes()

            recording = load_vibration_csv(csv_file, caller_meta)

            self.assertEqual(caller_meta, orig_meta, "Caller metadata dictionary must not be mutated")
            self.assertEqual(csv_file.read_bytes(), orig_bytes, "Source file on disk must not be modified")

    # -------------------------------------------------------------------------
    # Downstream Feature Extraction Pipeline Integration
    # -------------------------------------------------------------------------

    def test_loaded_recording_feeds_extract_window_features(self):
        """TC-INGEST-25: Ingested recording feeds extract_window_features and reproduces analytical metrics."""
        content = "x\n3.0\n4.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "features_feed.csv"
            csv_file.write_text(content, encoding="utf-8")

            recording = load_vibration_csv(csv_file, self.valid_metadata_1ch)
            features_result = extract_window_features(recording, window_size=2, hop_size=2)

            self.assertEqual(features_result["status"], "SUCCESS")
            self.assertEqual(len(features_result["windows"]), 1)
            win = features_result["windows"][0]
            self.assertAlmostEqual(win["features"]["x"]["mean"], 3.5)
            self.assertAlmostEqual(win["features"]["x"]["rms"], math.sqrt(12.5))
            self.assertAlmostEqual(win["features"]["x"]["peak_abs"], 4.0)

    def test_provenance_recorded_fixture_label(self):
        """TC-INGEST-26: Test fixture with provenance='recorded' tests metadata handling only."""
        content = "x,y\n0.5,1.5\n"
        meta_recorded = copy.deepcopy(self.valid_metadata_3ch)
        meta_recorded["provenance"] = "recorded"
        meta_recorded["source_id"] = "synthetic-fixture-provenance-test-only"

        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "recorded_provenance.csv"
            csv_file.write_text(content, encoding="utf-8")

            # This fixture tests adapter handling of provenance="recorded".
            # It is synthetic test data, not evidence of physical sensor acquisition.
            recording = load_vibration_csv(csv_file, meta_recorded)
            self.assertEqual(recording["provenance"], "recorded")
            self.assertEqual(recording["source_id"], "synthetic-fixture-provenance-test-only")

    # -------------------------------------------------------------------------
    # Repair 01 Focused Regression Tests (Strict Structure, Grammar, Errors)
    # -------------------------------------------------------------------------

    def test_unterminated_quoted_numeric_field_rejected(self):
        """TC-INGEST-27: Unterminated quote raises IngestionParseError with filename, physical line, and csv.Error cause."""
        content = b'x\n"1'
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "unterminated.csv"
            csv_file.write_bytes(content)
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(csv_file, self.valid_metadata_1ch)
            self.assertIn(str(csv_file), str(ctx.exception))
            self.assertIsNotNone(ctx.exception.row)
            self.assertIsInstance(ctx.exception.__cause__, Exception)

    def test_malformed_closing_quote_rejected(self):
        """TC-INGEST-28: Malformed closing quote raises IngestionParseError with parser cause."""
        content = b'x,y\n"1"2,3\n'
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "malformed_close.csv"
            csv_file.write_bytes(content)
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertIsInstance(ctx.exception.__cause__, Exception)

    def test_embedded_newline_in_quoted_field_rejected(self):
        """TC-INGEST-29: Embedded physical newlines inside quoted cells are strictly rejected."""
        scenarios = [
            b'x\n"1\n2"\n',
            b'x\n"1\r\n2"\n',
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for idx, raw_bytes in enumerate(scenarios):
                csv_file = Path(tmp_dir) / f"embedded_nl_{idx}.csv"
                csv_file.write_bytes(raw_bytes)
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(csv_file, self.valid_metadata_1ch)
                self.assertIn("embedded newline", str(ctx.exception).lower())

    def test_valid_quoted_numeric_fields(self):
        """TC-INGEST-30: Valid quoted numeric fields parse cleanly into numeric observations."""
        content = b'x,y\n"1.23","4.56"\n"+0.78","-9.01"\n'
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "valid_quoted.csv"
            csv_file.write_bytes(content)
            rec = load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertEqual(rec["samples"], [[1.23, 4.56], [0.78, -9.01]])

    def test_underscore_and_unicode_digit_rejection(self):
        """TC-INGEST-31: Underscores and non-ASCII Unicode digits are strictly rejected before float conversion."""
        bad_numbers = [
            "1_000",
            "1_234.56",
            "1e1_0",
            "١٢٣",       # Arabic-Indic
            "১২৩",       # Bengali
            "①",         # Enclosed digit
            "1.0\u00a0", # Non-breaking space
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for idx, s in enumerate(bad_numbers):
                csv_file = Path(tmp_dir) / f"bad_num_{idx}.csv"
                csv_file.write_text(f"x\n{s}\n", encoding="utf-8")
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(csv_file, self.valid_metadata_1ch)
                self.assertIn("invalid decimal numeric syntax", str(ctx.exception).lower())

    def test_valid_signed_decimals_and_scientific_notation_forms(self):
        """TC-INGEST-32: Explicit numeric grammar accepts signed decimals, dot-only mantissas, and exponents."""
        valid_forms = [
            ("12", 12.0),
            ("12.", 12.0),
            ("12.34", 12.34),
            (".34", 0.34),
            ("+12", 12.0),
            ("-12.34", -12.34),
            ("+.34", 0.34),
            ("-.5", -0.5),
            ("12e3", 12000.0),
            ("12.e3", 12000.0),
            ("12.34E-2", 0.1234),
            (".34e+2", 34.0),
            ("-1.5e-3", -0.0015),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for s, expected in valid_forms:
                csv_file = Path(tmp_dir) / "grammar_test.csv"
                csv_file.write_text(f"x\n{s}\n", encoding="utf-8")
                rec = load_vibration_csv(csv_file, self.valid_metadata_1ch)
                self.assertAlmostEqual(rec["samples"][0][0], expected)

    def test_oversized_positive_and_negative_sample_rate_integers(self):
        """TC-INGEST-33: Oversized integer sample_rate_hz raises IngestionMetadataError without leaking OverflowError."""
        content = "x\n1.0\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "rate_test.csv"
            csv_file.write_text(content, encoding="utf-8")

            for oversized in (10**400, -(10**400)):
                meta = copy.deepcopy(self.valid_metadata_1ch)
                meta["sample_rate_hz"] = oversized
                with self.assertRaises(IngestionMetadataError) as ctx:
                    load_vibration_csv(csv_file, meta)
                self.assertIn("binary64", str(ctx.exception).lower())

    def test_csv_field_size_limit_failure(self):
        """TC-INGEST-34: CSV field exceeding csv.field_size_limit() raises IngestionParseError without leaking csv.Error."""
        import csv
        old_limit = csv.field_size_limit()
        try:
            csv.field_size_limit(30)
            content = "x\n" + "1" * 50 + "\n"
            with tempfile.TemporaryDirectory() as tmp_dir:
                csv_file = Path(tmp_dir) / "field_limit.csv"
                csv_file.write_text(content, encoding="utf-8")
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(csv_file, self.valid_metadata_1ch)
                self.assertIn("field larger than field limit", str(ctx.exception).lower())
                self.assertIsInstance(ctx.exception.__cause__, csv.Error)
        finally:
            csv.field_size_limit(old_limit)

    def test_bare_cr_policy_rejected(self):
        """TC-INGEST-35: Bare CR line endings are rejected with IngestionParseError without leaking csv.Error."""
        scenarios = [
            b"x\r1.0\r",
            b"x\r1\r",
            b"x,y\r1.0,2.0\r3.0,4.0\r",
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            for idx, raw_bytes in enumerate(scenarios):
                csv_file = Path(tmp_dir) / f"bare_cr_{idx}.csv"
                csv_file.write_bytes(raw_bytes)
                with self.assertRaises(IngestionParseError) as ctx:
                    load_vibration_csv(
                        csv_file,
                        self.valid_metadata_1ch if b"," not in raw_bytes else self.valid_metadata_3ch,
                    )
                self.assertIn("bare cr", str(ctx.exception).lower())

    def test_error_context_filename_row_column_and_cause(self):
        """TC-INGEST-36: IngestionParseError preserves filename, row, column, and underlying cause."""
        content = "x,y\n1.0,abc\n"
        with tempfile.TemporaryDirectory() as tmp_dir:
            csv_file = Path(tmp_dir) / "context_test.csv"
            csv_file.write_text(content, encoding="utf-8")
            with self.assertRaises(IngestionParseError) as ctx:
                load_vibration_csv(csv_file, self.valid_metadata_3ch)
            self.assertEqual(ctx.exception.filename, str(csv_file))
            self.assertEqual(ctx.exception.row, 2)
            self.assertEqual(ctx.exception.column, 2)
            self.assertIn(str(csv_file), str(ctx.exception))
            self.assertIn("row 2", str(ctx.exception))
            self.assertIn("column 2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
