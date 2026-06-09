"""Core data structures for Puppet Master — CodeMandate, Budget, ValidationReport, ScopeViolation."""

from __future__ import annotations

import fnmatch
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class MandateStatus(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


VALID_TRANSITIONS: dict[MandateStatus, list[MandateStatus]] = {
    MandateStatus.PENDING: [MandateStatus.DISPATCHED],
    MandateStatus.DISPATCHED: [MandateStatus.SUBMITTED],
    MandateStatus.SUBMITTED: [MandateStatus.ACCEPTED, MandateStatus.REJECTED],
    MandateStatus.REJECTED: [MandateStatus.PENDING],  # re-delegation
    MandateStatus.ACCEPTED: [],  # terminal
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return f"mnd-{uuid.uuid4().hex[:8]}"


@dataclass
class Budget:
    max_turns: int = 20
    max_tokens: int = 50000
    turns_used: int = 0
    tokens_used: int = 0

    def can_proceed(self) -> bool:
        return self.turns_used < self.max_turns and self.tokens_used < self.max_tokens

    def record_turn(self, tokens: int = 0) -> None:
        self.turns_used += 1
        self.tokens_used += tokens

    def to_dict(self) -> dict:
        return {
            "max_turns": self.max_turns,
            "max_tokens": self.max_tokens,
            "turns_used": self.turns_used,
            "tokens_used": self.tokens_used,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Budget:
        return cls(
            max_turns=d.get("max_turns", 20),
            max_tokens=d.get("max_tokens", 50000),
            turns_used=d.get("turns_used", 0),
            tokens_used=d.get("tokens_used", 0),
        )


@dataclass
class CodeMandate:
    mandate_id: str = field(default_factory=_id)
    parent_id: str = "root"
    goal: str = ""
    branch: str = ""
    checkpoint: str = ""  # git SHA at delegation time
    allowed_paths: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=lambda: [
        "read_file", "write_file", "run_tests", "list_files", "search_files"
    ])
    forbidden_actions: list[str] = field(default_factory=lambda: [
        "git_commit", "git_push", "spawn_agent", "install_deps"
    ])
    budget: Budget = field(default_factory=Budget)
    result_schema: dict = field(default_factory=dict)
    depth: int = 1  # max sub-delegation depth (0 = leaf, no spawning)
    status: str = "pending"
    created_at: str = field(default_factory=_now)
    submitted_at: str | None = None
    result: dict | None = None
    ancestry: list[str] = field(default_factory=list)
    executor_id: str | None = None

    def transition(self, new_status: str) -> None:
        """Finite state machine for mandate status transitions."""
        current = MandateStatus(self.status)
        target = MandateStatus(new_status)
        if target not in VALID_TRANSITIONS.get(current, []):
            raise ValueError(
                f"Invalid transition: {self.status} -> {new_status}. "
                f"Allowed: {[s.value for s in VALID_TRANSITIONS.get(current, [])]}"
            )
        self.status = new_status
        if new_status == MandateStatus.SUBMITTED.value:
            self.submitted_at = _now()

    def path_allowed(self, path: str) -> bool:
        """Check if a path is within the mandate's scope.

        Forbidden paths take priority over allowed patterns.
        """
        # Check forbidden first (overrides allowed)
        for pattern in self.forbidden_paths:
            if fnmatch.fnmatch(path, pattern):
                return False
        # Check allowed
        if not self.allowed_paths:
            return True  # no restrictions
        return any(fnmatch.fnmatch(path, p) for p in self.allowed_paths)

    def tool_allowed(self, tool_name: str) -> bool:
        """Check if a tool is permitted by this mandate."""
        if tool_name in self.forbidden_actions:
            return False
        # Map tool names to action names
        action_map = {
            "write_file": "write", "read_file": "read",
            "run_tests": "run_tests", "list_files": "list",
            "search_files": "search", "git_commit": "git_commit",
            "git_push": "git_push", "spawn_agent": "spawn_agent",
            "install_deps": "install_deps", "bash": "bash",
        }
        action = action_map.get(tool_name, tool_name)
        return action not in self.forbidden_actions and (
            not self.allowed_tools or action in self.allowed_tools
        )

    def to_dict(self) -> dict:
        return {
            "mandate_id": self.mandate_id,
            "parent_id": self.parent_id,
            "goal": self.goal,
            "branch": self.branch,
            "checkpoint": self.checkpoint,
            "allowed_paths": self.allowed_paths,
            "forbidden_paths": self.forbidden_paths,
            "allowed_tools": self.allowed_tools,
            "forbidden_actions": self.forbidden_actions,
            "budget": self.budget.to_dict(),
            "result_schema": self.result_schema,
            "depth": self.depth,
            "status": self.status,
            "created_at": self.created_at,
            "submitted_at": self.submitted_at,
            "result": self.result,
            "ancestry": self.ancestry,
            "executor_id": self.executor_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CodeMandate:
        return cls(
            mandate_id=d["mandate_id"],
            parent_id=d.get("parent_id", "root"),
            goal=d.get("goal", ""),
            branch=d.get("branch", ""),
            checkpoint=d.get("checkpoint", ""),
            allowed_paths=d.get("allowed_paths", []),
            forbidden_paths=d.get("forbidden_paths", []),
            allowed_tools=d.get("allowed_tools", []),
            forbidden_actions=d.get("forbidden_actions", []),
            budget=Budget.from_dict(d.get("budget", {})),
            result_schema=d.get("result_schema", {}),
            depth=d.get("depth", 1),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", _now()),
            submitted_at=d.get("submitted_at"),
            result=d.get("result"),
            ancestry=d.get("ancestry", []),
            executor_id=d.get("executor_id"),
        )


@dataclass
class ScopeViolation:
    mandate_id: str
    executor_id: str
    attempted_tool: str
    attempted_path: str | None
    reason: str  # "path_outside_scope" | "tool_not_allowed" | "budget_exhausted" | "depth_exceeded"
    timestamp: str = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "mandate_id": self.mandate_id,
            "executor_id": self.executor_id,
            "attempted_tool": self.attempted_tool,
            "attempted_path": self.attempted_path,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ScopeViolation:
        return cls(
            mandate_id=d["mandate_id"],
            executor_id=d["executor_id"],
            attempted_tool=d["attempted_tool"],
            attempted_path=d.get("attempted_path"),
            reason=d["reason"],
            timestamp=d.get("timestamp", _now()),
        )


class ScopeViolationError(Exception):
    """Raised when a scope violation is detected."""

    def __init__(self, violation: ScopeViolation):
        self.violation = violation
        super().__init__(
            f"Scope violation: {violation.reason} — "
            f"tool={violation.attempted_tool} path={violation.attempted_path}"
        )


@dataclass
class ValidationReport:
    scope_respected: bool = True
    tests_pass: bool = True
    schema_met: bool = True
    no_new_deps: bool = True
    no_side_effects: bool = True
    quality_accepted: bool | None = None
    quality_notes: str | None = None
    errors: list[str] = field(default_factory=list)
    accepted: bool = False

    def compute(self) -> None:
        """Compute final acceptance based on all checks."""
        self.accepted = (
            self.scope_respected
            and self.tests_pass
            and self.schema_met
            and self.no_new_deps
            and self.no_side_effects
        )

    def to_dict(self) -> dict:
        return {
            "scope_respected": self.scope_respected,
            "tests_pass": self.tests_pass,
            "schema_met": self.schema_met,
            "no_new_deps": self.no_new_deps,
            "no_side_effects": self.no_side_effects,
            "quality_accepted": self.quality_accepted,
            "quality_notes": self.quality_notes,
            "errors": self.errors,
            "accepted": self.accepted,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ValidationReport:
        return cls(
            scope_respected=d.get("scope_respected", True),
            tests_pass=d.get("tests_pass", True),
            schema_met=d.get("schema_met", True),
            no_new_deps=d.get("no_new_deps", True),
            no_side_effects=d.get("no_side_effects", True),
            quality_accepted=d.get("quality_accepted"),
            quality_notes=d.get("quality_notes"),
            errors=d.get("errors", []),
            accepted=d.get("accepted", False),
        )
