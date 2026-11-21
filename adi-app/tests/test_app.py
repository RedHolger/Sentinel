"""Basic and regression tests for ADI App."""
import contextlib
import io
import json
import unittest

import adi_app
from adi_app.portfolio import main


class TestApp(unittest.TestCase):
    def test_version_present(self):
        self.assertEqual(adi_app.__version__, "0.1.0")

    def test_demo_scaffold_output(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["demo"])
        output = stdout.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("scaffold", output.lower())
        # Regression checks: must not claim completed verification or inference
        self.assertNotIn("offline deterministic verification path", output.lower())
        self.assertIn("not implemented", output.lower())

    def test_benchmark_returns_not_implemented_and_nonzero(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["benchmark"])
        self.assertNotEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload.get("status"), "NOT_IMPLEMENTED")
        self.assertEqual(payload.get("hardware_status"), "UNVERIFIED")
        self.assertIn("reason", payload)
        self.assertTrue(len(payload["reason"]) > 0)
        # Regression checks: must not include fabricated measurement or pass fields
        self.assertNotEqual(payload.get("status"), "PASS")
        self.assertNotIn("trials", payload)
        self.assertNotIn("target", payload)

    def test_evaluate_returns_not_implemented_and_nonzero(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["evaluate"])
        self.assertNotEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload.get("status"), "NOT_IMPLEMENTED")
        self.assertEqual(payload.get("hardware_status"), "UNVERIFIED")
        self.assertIn("reason", payload)
        self.assertTrue(len(payload["reason"]) > 0)
        # Regression checks: must not include fabricated measurement or pass fields
        self.assertNotEqual(payload.get("status"), "PASS")
        self.assertNotIn("evaluated_cases", payload)


if __name__ == "__main__":
    unittest.main()
