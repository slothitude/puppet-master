"""E2E test — Full delegation cycle (mock LLM)."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH, PuppetConfig
from graph import MandateGraph
from mandate import Budget, CodeMandate, ScopeViolationError
from sandbox import Sandbox
from scope_enforcer import ScopeEnforcer
from validator import Validator, detect_overlaps


class TestE2EDelegation(unittest.TestCase):
    """Full delegation cycle: create mandate → scope enforce → validate → merge."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.graph = MandateGraph(self.db_path)

        # Create test repo
        self.repo = os.path.join(self.tmpdir, "repo")
        os.makedirs(self.repo)
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True, capture_output=True)

        # Create src/auth/ with a bug
        os.makedirs(os.path.join(self.repo, "src", "auth"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "src", "utils"), exist_ok=True)

        with open(os.path.join(self.repo, "src", "auth", "login.py"), "w") as f:
            f.write("def login(user, pw):\n    return True  # BUG: no auth check\n")

        with open(os.path.join(self.repo, "src", "utils", "helper.py"), "w") as f:
            f.write("def helper():\n    return 42\n")

        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self):
        self.graph.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_cycle(self):
        """Mandate creation → executor changes file → scope ok → validate → merge."""
        # 1. Create mandate
        sb = Sandbox(self.repo)
        branch = "fix-auth"
        checkpoint = sb.get_checkpoint()
        worktree_path = sb.create_worktree(branch, checkpoint)

        mandate = CodeMandate(
            goal="Fix auth bug in login.py",
            branch=branch,
            checkpoint=checkpoint,
            allowed_paths=["src/auth/**"],
            forbidden_paths=["src/utils/**"],
            budget=Budget(max_turns=20, max_tokens=50000),
            depth=0,
        )
        mandate.transition("dispatched")

        # 2. Write to graph
        self.graph.add_node("root", "mandate", {"goal": "root", "status": "active"})
        self.graph.add_node(mandate.mandate_id, "mandate", mandate.to_dict())
        self.graph.add_edge("root", mandate.mandate_id, "delegates_to")

        # 3. Simulate executor: edit a file within scope
        enforcer = ScopeEnforcer(mandate, graph=self.graph, executor_id="test-exec")
        enforcer.enforce("write_file", {"path": "src/auth/login.py"})
        # Write to worktree
        with open(os.path.join(worktree_path, "src", "auth", "login.py"), "w") as f:
            f.write("def login(user, pw):\n    if user == 'admin' and pw == 'secret':\n        return True\n    return False\n")
        subprocess.run(["git", "add", "-A"], cwd=worktree_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix auth"], cwd=worktree_path, capture_output=True)

        # 4. Submit result
        result = {
            "summary": "Fixed auth bug — added credential check",
            "files_changed": ["src/auth/login.py"],
            "tests_result": "passed",
        }
        mandate.result = result
        mandate.transition("submitted")
        self.graph.update_node(mandate.mandate_id, mandate.to_dict())

        # 5. Validate
        validator = Validator(mandate, self.graph)
        report = validator.run(result)
        self.assertTrue(report.scope_respected)
        self.assertTrue(report.accepted)

        # 6. Merge
        success = sb.merge_branch(branch, "master")
        self.assertTrue(success)

        # Verify the fix is in the main branch
        with open(os.path.join(self.repo, "src", "auth", "login.py")) as f:
            content = f.read()
        self.assertIn("admin", content)

        # Cleanup
        subprocess.run(["git", "branch", "-D", "fix-auth"], cwd=self.repo, capture_output=True)

    def test_violation_e2e(self):
        """Executor touches forbidden path → scope_enforcer blocks → violation in graph."""
        mandate = CodeMandate(
            allowed_paths=["src/auth/**"],
            forbidden_paths=["src/utils/**"],
        )

        enforcer = ScopeEnforcer(mandate, graph=self.graph, executor_id="bad-exec")

        try:
            enforcer.enforce("write_file", {"path": "src/utils/evil.py"})
            self.fail("Should have raised ScopeViolationError")
        except ScopeViolationError:
            pass

        # Violation should be in graph
        stats = self.graph.graph_stats()
        self.assertEqual(stats["by_type"].get("violation", 0), 1)


class TestOverlapDetection(unittest.TestCase):
    def test_no_overlap(self):
        m1 = CodeMandate(allowed_paths=["src/auth/**"])
        m2 = CodeMandate(allowed_paths=["src/utils/**"])
        conflicts = detect_overlaps([m1, m2])
        self.assertEqual(len(conflicts), 0)

    def test_overlap_detected(self):
        m1 = CodeMandate(allowed_paths=["src/auth/**"])
        m2 = CodeMandate(allowed_paths=["src/auth/**"])
        conflicts = detect_overlaps([m1, m2])
        self.assertEqual(len(conflicts), 1)
        self.assertIn("src/auth/**", conflicts[0]["overlapping_paths"])


if __name__ == "__main__":
    unittest.main()
