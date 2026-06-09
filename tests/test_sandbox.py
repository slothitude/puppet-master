"""Tests for sandbox.py — branch creation, isolation, diff, merge."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sandbox import Sandbox


class TestSandbox(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmpdir, "repo")
        os.makedirs(self.repo)
        # Init a git repo
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True, capture_output=True)
        # Create initial commit
        with open(os.path.join(self.repo, "main.py"), "w") as f:
            f.write("print('hello')\n")
        os.makedirs(os.path.join(self.repo, "src"), exist_ok=True)
        with open(os.path.join(self.repo, "src", "auth.py"), "w") as f:
            f.write("# auth module\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_worktree(self):
        sb = Sandbox(self.repo)
        path = sb.create_worktree("fix-auth")
        self.assertTrue(os.path.isdir(path))
        self.assertIn("fix-auth", path)
        # Cleanup
        sb.cleanup("fix-auth")

    def test_worktree_isolation(self):
        sb = Sandbox(self.repo)
        path = sb.create_worktree("fix-auth")
        # Write to worktree
        auth_path = os.path.join(path, "src", "auth.py")
        with open(auth_path, "w") as f:
            f.write("# modified auth\n")
        # Original should be unchanged
        with open(os.path.join(self.repo, "src", "auth.py")) as f:
            content = f.read()
        self.assertEqual(content, "# auth module\n")
        sb.cleanup("fix-auth")

    def test_diff_branch(self):
        sb = Sandbox(self.repo)
        wt_path = sb.create_worktree("fix-auth")
        # Make changes in worktree
        with open(os.path.join(wt_path, "src", "auth.py"), "w") as f:
            f.write("# fixed auth\n")
        subprocess.run(["git", "add", "-A"], cwd=wt_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix auth"], cwd=wt_path, capture_output=True)
        diff = sb.diff_branch("fix-auth")
        self.assertIn("auth.py", diff)
        sb.cleanup("fix-auth")

    def test_list_changed_files(self):
        sb = Sandbox(self.repo)
        wt_path = sb.create_worktree("fix-auth")
        with open(os.path.join(wt_path, "src", "auth.py"), "w") as f:
            f.write("# fixed auth\n")
        subprocess.run(["git", "add", "-A"], cwd=wt_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix auth"], cwd=wt_path, capture_output=True)
        files = sb.list_changed_files("fix-auth")
        self.assertIn("src/auth.py", files)
        sb.cleanup("fix-auth")

    def test_merge_branch(self):
        sb = Sandbox(self.repo)
        wt_path = sb.create_worktree("fix-auth")
        with open(os.path.join(wt_path, "src", "auth.py"), "w") as f:
            f.write("# fixed auth\n")
        subprocess.run(["git", "add", "-A"], cwd=wt_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix auth"], cwd=wt_path, capture_output=True)
        result = sb.merge_branch("fix-auth", target_branch="master")
        self.assertTrue(result)
        # Verify merge
        with open(os.path.join(self.repo, "src", "auth.py")) as f:
            content = f.read()
        self.assertEqual(content, "# fixed auth\n")
        # Cleanup branch
        subprocess.run(["git", "branch", "-D", "fix-auth"], cwd=self.repo, capture_output=True)

    def test_checkpoint(self):
        sb = Sandbox(self.repo)
        sha = sb.get_checkpoint()
        self.assertEqual(len(sha), 40)  # full SHA

    def test_not_a_repo(self):
        with self.assertRaises(ValueError):
            Sandbox("/tmp/not-a-repo-xyz")


if __name__ == "__main__":
    unittest.main()
