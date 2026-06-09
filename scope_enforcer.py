"""ScopeEnforcer — runtime scope enforcement for executor tool calls.

Wraps every tool call structurally. Raises ScopeViolationError
when a violation is detected and records it to the graph.
"""

from __future__ import annotations

import logging
from typing import Any

from mandate import Budget, CodeMandate, ScopeViolation, ScopeViolationError

logger = logging.getLogger(__name__)

# Tool → action mapping
TOOL_ACTION_MAP: dict[str, str] = {
    "read_file": "read",
    "write_file": "write",
    "edit_file": "write",
    "run_tests": "run_tests",
    "run_command": "bash",
    "bash": "bash",
    "list_files": "list",
    "search_files": "search",
    "grep": "search",
    "git_commit": "git_commit",
    "git_push": "git_push",
    "git_diff": "read",
    "spawn_agent": "spawn_agent",
    "install_deps": "install_deps",
}

# Tools that require path checking
PATH_TOOLS = {"read_file", "write_file", "edit_file"}


class ScopeEnforcer:
    """Wraps tool calls with mandate scope enforcement."""

    def __init__(self, mandate: CodeMandate, graph=None, executor_id: str = ""):
        self.mandate = mandate
        self.graph = graph
        self.executor_id = executor_id

    def check_tool(self, tool_name: str) -> None:
        """Check if a tool is allowed. Raises ScopeViolationError if not."""
        # Check budget first
        if not self.mandate.budget.can_proceed():
            violation = self._make_violation(tool_name, None, "budget_exhausted")
            raise ScopeViolationError(violation)

        # Check forbidden actions
        action = TOOL_ACTION_MAP.get(tool_name, tool_name)
        if action in self.mandate.forbidden_actions:
            violation = self._make_violation(tool_name, None, "tool_not_allowed")
            raise ScopeViolationError(violation)

    def check_path(self, tool_name: str, path: str) -> None:
        """Check if a path is within mandate scope. Raises ScopeViolationError if not."""
        if tool_name not in PATH_TOOLS:
            return  # tool doesn't use file paths
        if not self.mandate.path_allowed(path):
            violation = self._make_violation(tool_name, path, "path_outside_scope")
            raise ScopeViolationError(violation)

    def enforce(self, tool_name: str, tool_args: dict) -> None:
        """Full enforcement check: tool + path. Raises ScopeViolationError on violation."""
        self.check_tool(tool_name)
        # Extract path from common arg positions
        for key in ("path", "file_path", "filepath", "source", "target"):
            if key in tool_args and isinstance(tool_args[key], str):
                self.check_path(tool_name, tool_args[key])
                break

    def enforce_call(self, tool_name: str, *args: Any, **kwargs: Any) -> None:
        """Enforce before an actual function call. Works with both positional and keyword args."""
        self.check_tool(tool_name)
        # Check path from kwargs or first positional arg if it looks like a path
        for key in ("path", "file_path", "filepath"):
            if key in kwargs:
                self.check_path(tool_name, kwargs[key])
                return
        if args and isinstance(args[0], str) and ("/" in args[0] or "\\" in args[0]):
            self.check_path(tool_name, args[0])

    def wrap_tool(self, tool_name: str, tool_fn, **default_kwargs):
        """Return a wrapped function that enforces scope before execution.

        Usage:
            safe_read = enforcer.wrap_tool("read_file", read_file_fn, path="some/path")
            safe_read()  # will raise if path outside scope
        """
        def wrapped(**kwargs):
            merged = {**default_kwargs, **kwargs}
            self.enforce(tool_name, merged)
            return tool_fn(**merged)
        return wrapped

    def _make_violation(self, tool: str, path: str | None, reason: str) -> ScopeViolation:
        violation = ScopeViolation(
            mandate_id=self.mandate.mandate_id,
            executor_id=self.executor_id,
            attempted_tool=tool,
            attempted_path=path,
            reason=reason,
        )
        # Record to graph
        if self.graph:
            try:
                self.graph.add_node(
                    f"vio-{violation.timestamp}", "violation", violation.to_dict()
                )
                self.graph.add_edge(
                    self.mandate.mandate_id,
                    f"vio-{violation.timestamp}",
                    "validates",  # violation validates as evidence against mandate
                )
            except Exception as e:
                logger.warning(f"Failed to record violation to graph: {e}")
        else:
            logger.warning(f"Scope violation (no graph): {reason} tool={tool} path={path}")
        return violation
