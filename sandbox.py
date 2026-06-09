"""Sandbox — Git worktree isolation for executor branches.

Creates, diffs, merges, and cleans up git worktrees.
Executors work on isolated branches; root merges via diff review.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class Sandbox:
    """Git worktree manager for mandate sandboxes."""

    def __init__(self, repo_path: str):
        self.repo_path = os.path.abspath(repo_path)
        if not os.path.isdir(os.path.join(self.repo_path, ".git")):
            raise ValueError(f"Not a git repository: {self.repo_path}")

    def create_worktree(self, branch_name: str, base_sha: str | None = None) -> str:
        """Create a git worktree for the mandate branch.

        Args:
            branch_name: Name for the new branch.
            base_sha: SHA to branch from. Defaults to current HEAD.

        Returns:
            Path to the new worktree directory.
        """
        worktree_path = os.path.join(
            os.path.dirname(self.repo_path),
            f".puppet-worktrees/{branch_name}",
        )

        # Clean up if worktree already exists
        if os.path.exists(worktree_path):
            self._remove_worktree_dir(worktree_path, branch_name)

        # Create branch at base SHA
        base = base_sha or self._head_sha()
        subprocess.run(
            ["git", "branch", branch_name, base],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
        )

        # Create worktree
        subprocess.run(
            ["git", "worktree", "add", worktree_path, branch_name],
            cwd=self.repo_path,
            check=True,
            capture_output=True,
        )

        logger.info(f"Created worktree: {worktree_path} (branch: {branch_name} from {base})")
        return worktree_path

    def diff_branch(self, branch_name: str) -> str:
        """Get unified diff of branch against its base.

        Returns the diff output as a string.
        """
        # Find merge base (the checkpoint SHA)
        result = subprocess.run(
            ["git", "merge-base", "HEAD", branch_name],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        base_sha = result.stdout.strip()

        result = subprocess.run(
            ["git", "diff", base_sha, branch_name, "--stat"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def list_changed_files(self, branch_name: str) -> list[str]:
        """List files changed on branch vs base."""
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", branch_name],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return [f for f in result.stdout.strip().split("\n") if f]

    def merge_branch(self, branch_name: str, target_branch: str = "main") -> bool:
        """Merge a mandate branch into the target branch.

        Returns True if merge succeeded.
        """
        # Check current branch
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        current_branch = result.stdout.strip()

        if current_branch != target_branch:
            subprocess.run(
                ["git", "checkout", target_branch],
                cwd=self.repo_path,
                check=True,
                capture_output=True,
            )

        # Attempt merge
        result = subprocess.run(
            ["git", "merge", "--no-ff", branch_name, "-m", f"Merge mandate branch: {branch_name}"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            # Merge conflict — abort
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=self.repo_path,
                capture_output=True,
            )
            logger.error(f"Merge conflict merging {branch_name} into {target_branch}")
            return False

        logger.info(f"Merged {branch_name} into {target_branch}")
        return True

    def cleanup(self, branch_name: str) -> None:
        """Remove worktree and branch."""
        # Find worktree path
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        worktree_path = None
        lines = result.stdout.strip().split("\n")
        for i, line in enumerate(lines):
            if line.startswith("worktree ") and branch_name in line:
                worktree_path = line.split(" ", 1)[1]
                break

        if worktree_path and os.path.exists(worktree_path):
            self._remove_worktree_dir(worktree_path, branch_name)

        # Delete branch
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=self.repo_path,
            capture_output=True,
        )
        logger.info(f"Cleaned up branch: {branch_name}")

    def _remove_worktree_dir(self, worktree_path: str, branch_name: str) -> None:
        subprocess.run(
            ["git", "worktree", "remove", worktree_path, "--force"],
            cwd=self.repo_path,
            capture_output=True,
        )
        # Also try prune
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=self.repo_path,
            capture_output=True,
        )

    def _head_sha(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def get_checkpoint(self) -> str:
        """Get current HEAD SHA to use as checkpoint."""
        return self._head_sha()
