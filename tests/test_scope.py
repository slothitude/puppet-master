"""Tests for scope_enforcer.py — allowed/blocked tool calls, ScopeViolation recording."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from graph import MandateGraph
from mandate import Budget, CodeMandate, ScopeViolationError
from scope_enforcer import ScopeEnforcer


class TestScopeToolCheck(unittest.TestCase):
    def setUp(self):
        self.mandate = CodeMandate(
            allowed_paths=["src/auth/**"],
            allowed_tools=["read_file", "write_file", "run_tests"],
            forbidden_actions=["git_commit", "spawn_agent"],
        )
        self.enforcer = ScopeEnforcer(self.mandate, executor_id="test-exec")

    def test_allowed_tool(self):
        self.enforcer.check_tool("read_file")  # should not raise

    def test_forbidden_tool(self):
        with self.assertRaises(ScopeViolationError) as ctx:
            self.enforcer.check_tool("git_commit")
        self.assertEqual(ctx.exception.violation.reason, "tool_not_allowed")

    def test_forbidden_spawn(self):
        with self.assertRaises(ScopeViolationError) as ctx:
            self.enforcer.check_tool("spawn_agent")
        self.assertEqual(ctx.exception.violation.reason, "tool_not_allowed")

    def test_budget_exhausted(self):
        self.mandate.budget = Budget(max_turns=1, max_tokens=100000)
        self.mandate.budget.record_turn()
        with self.assertRaises(ScopeViolationError) as ctx:
            self.enforcer.check_tool("read_file")
        self.assertEqual(ctx.exception.violation.reason, "budget_exhausted")


class TestScopePathCheck(unittest.TestCase):
    def setUp(self):
        self.mandate = CodeMandate(allowed_paths=["src/auth/**"])
        self.enforcer = ScopeEnforcer(self.mandate, executor_id="test-exec")

    def test_path_allowed(self):
        self.enforcer.check_path("write_file", "src/auth/login.py")  # no raise

    def test_path_blocked(self):
        with self.assertRaises(ScopeViolationError) as ctx:
            self.enforcer.check_path("write_file", "src/utils/x.py")
        self.assertEqual(ctx.exception.violation.reason, "path_outside_scope")

    def test_non_path_tool(self):
        self.enforcer.check_path("run_tests", "anything")  # no raise

    def test_forbidden_overrides_allowed(self):
        self.mandate.forbidden_paths = ["src/auth/**"]
        with self.assertRaises(ScopeViolationError) as ctx:
            self.enforcer.check_path("write_file", "src/auth/login.py")
        self.assertEqual(ctx.exception.violation.reason, "path_outside_scope")


class TestScopeEnforce(unittest.TestCase):
    def setUp(self):
        self.mandate = CodeMandate(allowed_paths=["src/auth/**"])
        self.enforcer = ScopeEnforcer(self.mandate, executor_id="test-exec")

    def test_enforce_args_path(self):
        self.enforcer.enforce("write_file", {"path": "src/auth/login.py"})  # no raise

    def test_enforce_blocked(self):
        with self.assertRaises(ScopeViolationError):
            self.enforcer.enforce("write_file", {"path": "src/evil/x.py"})


class TestScopeViolationRecording(unittest.TestCase):
    def test_violation_recorded_to_graph(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_graph.db")
            graph = MandateGraph(db_path)
            mandate = CodeMandate(allowed_paths=["src/auth/**"])
            enforcer = ScopeEnforcer(mandate, graph=graph, executor_id="lappy-4b")

            try:
                enforcer.enforce("write_file", {"path": "src/utils/x.py"})
            except ScopeViolationError:
                pass

            # Check violation recorded
            stats = graph.graph_stats()
            self.assertEqual(stats["by_type"].get("violation", 0), 1)

            graph.close()


class TestWrapTool(unittest.TestCase):
    def setUp(self):
        self.mandate = CodeMandate(allowed_paths=["src/auth/**"])
        self.enforcer = ScopeEnforcer(self.mandate, executor_id="test-exec")

    def test_wrapped_allowed(self):
        called = []
        def fake_read(path):
            called.append(path)
            return "content"

        safe_read = self.enforcer.wrap_tool("read_file", fake_read, path="src/auth/login.py")
        result = safe_read()
        self.assertEqual(result, "content")
        self.assertEqual(called, ["src/auth/login.py"])

    def test_wrapped_blocked(self):
        def fake_write(path):
            return "written"

        safe_write = self.enforcer.wrap_tool("write_file", fake_write, path="src/evil/x.py")
        with self.assertRaises(ScopeViolationError):
            safe_write()


if __name__ == "__main__":
    unittest.main()
