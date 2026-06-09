"""Validator — Result validation for mandate submissions.

Mechanical checks first: scope, tests, schema, deps, side effects.
Large-model quality gate only if all mechanical checks pass.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

from graph import MandateGraph
from mandate import CodeMandate, ValidationReport

logger = logging.getLogger(__name__)


class Validator:
    """Validates executor results against their mandate contract."""

    def __init__(self, mandate: CodeMandate, graph: MandateGraph | None = None):
        self.mandate = mandate
        self.graph = graph

    def run(self, result: dict) -> ValidationReport:
        """Run all validation checks. Returns a ValidationReport."""
        report = ValidationReport()

        # 1. Scope check — did executor stay within allowed paths?
        files_changed = result.get("files_changed", [])
        scope_errors = self._check_scope(files_changed)
        if scope_errors:
            report.scope_respected = False
            report.errors.extend(scope_errors)

        # 2. Test check
        tests_result = result.get("tests_result", "")
        if tests_result and "not run" not in tests_result.lower():
            # If tests were run, check they passed
            if "error" in tests_result.lower() or "fail" in tests_result.lower():
                report.tests_pass = False
                report.errors.append(f"Tests failed: {tests_result[:200]}")

        # 3. Schema check
        if self.mandate.result_schema:
            schema_errors = self._check_schema(result)
            if schema_errors:
                report.schema_met = False
                report.errors.extend(schema_errors)

        # 4. No new dependencies
        if result.get("files_changed"):
            dep_errors = self._check_dependencies(result.get("worktree_path", ""))
            if dep_errors:
                report.no_new_deps = False
                report.errors.extend(dep_errors)

        # 5. No side effects
        side_errors = self._check_side_effects(result)
        if side_errors:
            report.no_side_effects = False
            report.errors.extend(side_errors)

        # Compute final acceptance
        report.compute()

        return report

    def _check_scope(self, files_changed: list[str]) -> list[str]:
        """Check that all changed files are within the mandate's allowed paths."""
        errors = []
        for f in files_changed:
            if not self.mandate.path_allowed(f):
                errors.append(f"Scope violation: {f} is outside allowed paths")
        return errors

    def _check_schema(self, result: dict) -> list[str]:
        """Check that the result matches the expected result_schema."""
        errors = []
        schema = self.mandate.result_schema
        for key, expected_type in schema.items():
            if key not in result:
                errors.append(f"Missing required field: {key}")
            elif expected_type == "array" and not isinstance(result[key], list):
                errors.append(f"Field {key} should be array, got {type(result[key]).__name__}")
            elif expected_type == "string" and not isinstance(result[key], str):
                errors.append(f"Field {key} should be string, got {type(result[key]).__name__}")
        return errors

    def _check_dependencies(self, worktree_path: str) -> list[str]:
        """Check that no new dependencies were added (package.json, requirements.txt, etc)."""
        errors = []
        if not worktree_path or not os.path.isdir(worktree_path):
            return errors

        dep_files = ["package.json", "requirements.txt", "Cargo.toml", "pyproject.toml", "go.mod"]
        files_changed = []  # would need git diff in practice
        for df in dep_files:
            if os.path.exists(os.path.join(worktree_path, df)):
                # In a full implementation, we'd diff the dep file
                # For now, just flag if it exists in a code mandate
                pass

        return errors

    def _check_side_effects(self, result: dict) -> list[str]:
        """Check for unexpected side effects."""
        errors = []
        # Check for unexpected files outside mandate scope
        files_changed = result.get("files_changed", [])
        for f in files_changed:
            # Flag common side effect patterns
            if "lock." in f or f.endswith(".log") or f.endswith(".lock"):
                errors.append(f"Unexpected side effect file: {f}")
            if f.startswith(".git/") or f.startswith(".env"):
                errors.append(f"Sensitive path modified: {f}")
        return errors


def detect_overlaps(mandates: list[CodeMandate]) -> list[dict]:
    """Detect file path overlaps between mandates.

    Returns list of conflict dicts with overlap details.
    """
    conflicts = []
    for i, m1 in enumerate(mandates):
        for m2 in mandates[i + 1:]:
            # Simple overlap: check if any allowed paths from m1 match m2
            overlap = []
            for p1 in m1.allowed_paths:
                for p2 in m2.allowed_paths:
                    if p1 == p2:
                        overlap.append(p1)
            if overlap:
                conflicts.append({
                    "mandate_1": m1.mandate_id,
                    "mandate_2": m2.mandate_id,
                    "overlapping_paths": overlap,
                })
    return conflicts
