"""Tests for validator.py — scope checks, schema, overlap detection."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph import MandateGraph
from mandate import CodeMandate, ValidationReport
from validator import Validator, detect_overlaps


class TestValidatorScope(unittest.TestCase):
    def test_scope_pass(self):
        mandate = CodeMandate(allowed_paths=["src/auth/**"])
        result = {"files_changed": ["src/auth/login.py", "src/auth/token.py"]}
        v = Validator(mandate)
        report = v.run(result)
        self.assertTrue(report.scope_respected)

    def test_scope_fail(self):
        mandate = CodeMandate(allowed_paths=["src/auth/**"])
        result = {"files_changed": ["src/auth/login.py", "src/utils/evil.py"]}
        v = Validator(mandate)
        report = v.run(result)
        self.assertFalse(report.scope_respected)
        self.assertFalse(report.accepted)
        self.assertIn("src/utils/evil.py", report.errors[0])

    def test_forbidden_overrides(self):
        mandate = CodeMandate(
            allowed_paths=["src/**"],
            forbidden_paths=["src/config/**"],
        )
        result = {"files_changed": ["src/config/secrets.py"]}
        v = Validator(mandate)
        report = v.run(result)
        self.assertFalse(report.scope_respected)


class TestValidatorSchema(unittest.TestCase):
    def test_schema_pass(self):
        mandate = CodeMandate(result_schema={"summary": "string", "files_changed": "array"})
        result = {"summary": "done", "files_changed": ["a.py"]}
        v = Validator(mandate)
        report = v.run(result)
        self.assertTrue(report.schema_met)

    def test_schema_fail_missing(self):
        mandate = CodeMandate(result_schema={"summary": "string"})
        result = {"files_changed": ["a.py"]}
        v = Validator(mandate)
        report = v.run(result)
        self.assertFalse(report.schema_met)
        self.assertIn("Missing", report.errors[0])


class TestValidatorSideEffects(unittest.TestCase):
    def test_lock_file_flagged(self):
        mandate = CodeMandate()
        result = {"files_changed": ["package-lock.json"]}
        v = Validator(mandate)
        report = v.run(result)
        self.assertFalse(report.no_side_effects)


class TestOverlapDetection(unittest.TestCase):
    def test_no_overlap(self):
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/auth/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/utils/**"])
        conflicts = detect_overlaps([m1, m2])
        self.assertEqual(len(conflicts), 0)

    def test_exact_overlap(self):
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/auth/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/auth/**"])
        conflicts = detect_overlaps([m1, m2])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["mandate_1"], "m1")
        self.assertEqual(conflicts[0]["mandate_2"], "m2")

    def test_three_way(self):
        m1 = CodeMandate(mandate_id="m1", allowed_paths=["src/a/**"])
        m2 = CodeMandate(mandate_id="m2", allowed_paths=["src/a/**"])
        m3 = CodeMandate(mandate_id="m3", allowed_paths=["src/b/**"])
        conflicts = detect_overlaps([m1, m2, m3])
        self.assertEqual(len(conflicts), 1)  # m1 vs m2 only


class TestValidationReportCompute(unittest.TestCase):
    def test_all_pass(self):
        r = ValidationReport()
        r.compute()
        self.assertTrue(r.accepted)

    def test_one_fail(self):
        r = ValidationReport(scope_respected=False)
        r.compute()
        self.assertFalse(r.accepted)

    def test_dict_round_trip(self):
        r = ValidationReport(accepted=True, quality_accepted=True, quality_notes="looks good", errors=[])
        d = r.to_dict()
        r2 = ValidationReport.from_dict(d)
        self.assertTrue(r2.accepted)
        self.assertTrue(r2.quality_accepted)


if __name__ == "__main__":
    unittest.main()
