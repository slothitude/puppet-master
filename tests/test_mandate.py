"""Tests for mandate.py — CodeMandate, Budget, status FSM, path globs."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mandate import Budget, CodeMandate, MandateStatus, ScopeViolation, ValidationReport


class TestBudget(unittest.TestCase):
    def test_can_proceed_initial(self):
        b = Budget(max_turns=5, max_tokens=1000)
        self.assertTrue(b.can_proceed())

    def test_exhausted_turns(self):
        b = Budget(max_turns=2, max_tokens=1000)
        b.record_turn()
        b.record_turn()
        self.assertFalse(b.can_proceed())

    def test_exhausted_tokens(self):
        b = Budget(max_turns=10, max_tokens=100)
        b.record_turn()
        b.record_tokens(101)
        self.assertFalse(b.can_proceed())

    def test_round_trip(self):
        b = Budget(max_turns=15, max_tokens=30000)
        b.record_turn()
        b.record_tokens(100)
        d = b.to_dict()
        b2 = Budget.from_dict(d)
        self.assertEqual(b2.turns_used, 1)
        self.assertEqual(b2.tokens_used, 100)
        self.assertEqual(b2.max_turns, 15)


class TestMandateStatus(unittest.TestCase):
    def test_valid_transitions(self):
        m = CodeMandate()
        m.transition("dispatched")
        self.assertEqual(m.status, "dispatched")
        m.transition("submitted")
        self.assertEqual(m.status, "submitted")

    def test_accept(self):
        m = CodeMandate()
        m.transition("dispatched")
        m.transition("submitted")
        m.transition("accepted")
        self.assertEqual(m.status, "accepted")

    def test_reject_and_redelegate(self):
        m = CodeMandate()
        m.transition("dispatched")
        m.transition("submitted")
        m.transition("rejected")
        self.assertEqual(m.status, "rejected")
        m.transition("pending")
        self.assertEqual(m.status, "pending")

    def test_invalid_transition(self):
        m = CodeMandate()
        with self.assertRaises(ValueError):
            m.transition("accepted")  # pending -> accepted not allowed

    def test_terminal_accepted(self):
        m = CodeMandate(status="accepted")
        with self.assertRaises(ValueError):
            m.transition("submitted")

    def test_submitted_at_set(self):
        m = CodeMandate()
        m.transition("dispatched")
        self.assertIsNone(m.submitted_at)
        m.transition("submitted")
        self.assertIsNotNone(m.submitted_at)


class TestPathGlobs(unittest.TestCase):
    def test_allowed_pattern(self):
        m = CodeMandate(allowed_paths=["src/auth/**"])
        self.assertTrue(m.path_allowed("src/auth/login.py"))
        self.assertTrue(m.path_allowed("src/auth/sub/module.py"))

    def test_blocked_outside_allowed(self):
        m = CodeMandate(allowed_paths=["src/auth/**"])
        self.assertFalse(m.path_allowed("src/utils/auth.py"))

    def test_forbidden_overrides_allowed(self):
        m = CodeMandate(
            allowed_paths=["src/**"],
            forbidden_paths=["src/auth/**"],
        )
        self.assertTrue(m.path_allowed("src/utils/helper.py"))
        self.assertFalse(m.path_allowed("src/auth/login.py"))

    def test_no_restrictions(self):
        m = CodeMandate()
        self.assertTrue(m.path_allowed("any/path/file.py"))

    def test_multiple_patterns(self):
        m = CodeMandate(allowed_paths=["src/auth/**", "src/models/**"])
        self.assertTrue(m.path_allowed("src/auth/login.py"))
        self.assertTrue(m.path_allowed("src/models/user.py"))
        self.assertFalse(m.path_allowed("src/views/home.py"))


class TestMandateRoundTrip(unittest.TestCase):
    def test_dict_round_trip(self):
        m = CodeMandate(
            goal="fix auth bug",
            branch="fix-auth",
            allowed_paths=["src/auth/**"],
            depth=2,
        )
        d = m.to_dict()
        m2 = CodeMandate.from_dict(d)
        self.assertEqual(m.mandate_id, m2.mandate_id)
        self.assertEqual(m.goal, m2.goal)
        self.assertEqual(m.allowed_paths, m2.allowed_paths)
        self.assertEqual(m.depth, m2.depth)

    def test_with_result(self):
        m = CodeMandate()
        m.result = {"files_changed": ["src/auth/login.py"], "tests_pass": True}
        d = m.to_dict()
        m2 = CodeMandate.from_dict(d)
        self.assertEqual(m2.result["files_changed"], ["src/auth/login.py"])


class TestScopeViolation(unittest.TestCase):
    def test_dict_round_trip(self):
        v = ScopeViolation(
            mandate_id="mnd-abc",
            executor_id="lappy-4b",
            attempted_tool="write_file",
            attempted_path="src/forbidden/x.py",
            reason="path_outside_scope",
        )
        d = v.to_dict()
        v2 = ScopeViolation.from_dict(d)
        self.assertEqual(v.mandate_id, v2.mandate_id)
        self.assertEqual(v.reason, v2.reason)


class TestValidationReport(unittest.TestCase):
    def test_all_pass(self):
        r = ValidationReport()
        r.compute()
        self.assertTrue(r.accepted)

    def test_scope_fail(self):
        r = ValidationReport(scope_respected=False)
        r.compute()
        self.assertFalse(r.accepted)

    def test_with_errors(self):
        r = ValidationReport(tests_pass=False, errors=["test_login failed"])
        r.compute()
        self.assertFalse(r.accepted)
        self.assertIn("test_login failed", r.errors)

    def test_dict_round_trip(self):
        r = ValidationReport(accepted=True, quality_accepted=True, quality_notes="good")
        d = r.to_dict()
        r2 = ValidationReport.from_dict(d)
        self.assertTrue(r2.accepted)
        self.assertEqual(r2.quality_notes, "good")


if __name__ == "__main__":
    unittest.main()
