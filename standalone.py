"""Standalone mode — autonomous entry point for Puppet Master.

Runs its own agent loop with GLM-5.1 (or any OpenAI-compatible API)
as the root agent. Reads task from CLI or stdin, analyzes codebase,
partitions work, dispatches to executors, validates, merges.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from config import DB_PATH, load_puppets
from graph import MandateGraph
from mandate import CodeMandate, _now, _id
from partitioner import OverlapType, partition_work

logger = logging.getLogger(__name__)


def analyze_goal(goal: str, repo_path: str) -> list[dict]:
    """Analyze a goal and partition into non-overlapping mandates.

    In standalone mode, this does simple heuristic partitioning.
    When Claude Code is driving, it does the partitioning via MCP.
    """
    import os

    # Walk the repo to understand structure
    dirs = set()
    for root, d, f in os.walk(repo_path):
        if ".git" in root or "node_modules" in root or "__pycache__" in root:
            continue
        rel = os.path.relpath(root, repo_path)
        if rel != ".":
            dirs.add(rel)

    # Simple heuristic: create one mandate per significant directory
    # In production, a large model would do this analysis
    mandates = []
    for d in sorted(dirs):
        # Only create mandates for code directories
        py_files = list(Path(repo_path).glob(os.path.join(d, "*.py")))
        js_files = list(Path(repo_path).glob(os.path.join(d, "*.js")))
        ts_files = list(Path(repo_path).glob(os.path.join(d, "*.ts")))
        if py_files or js_files or ts_files:
            mandates.append({
                "goal": f"{goal} — work on {d}/",
                "allowed_paths": [f"{d}/**"],
                "branch": f"mandate-{d.replace('/', '-')}",
            })

    return mandates


def run_standalone(goal: str, repo_path: str, executor: str = "lappy-4b") -> dict:
    """Run the full delegation cycle autonomously."""
    graph = MandateGraph(DB_PATH)
    puppets = load_puppets()

    if executor not in puppets:
        return {"error": f"Unknown executor: {executor}"}

    # 1. Analyze and partition
    partition = analyze_goal(goal, repo_path)
    if not partition:
        return {"error": "Could not partition goal — no code directories found"}

    # Convert to mandates
    mandates = []
    for p in partition:
        m = CodeMandate(
            goal=p["goal"],
            allowed_paths=p["allowed_paths"],
            branch=p["branch"],
            executor_id=executor,
        )
        mandates.append(m)

    # 2. Run partition_work — classify overlaps, resolve
    plan = partition_work(mandates, repo_path)

    # 3. Write to graph
    root_id = "standalone-root"
    graph.add_node(root_id, "mandate", {"goal": goal, "status": "active"})

    for m in plan["active_mandates"]:
        graph.add_node(m.mandate_id, "mandate", m.to_dict())
        graph.add_edge(root_id, m.mandate_id, "delegates_to")

    # Wire depends_on edges from SEQUENCE/EXTRACT resolutions
    for edge in plan.get("depends_on_edges", []):
        graph.add_edge(edge["from"], edge["to"], "depends_on")

    return {
        "goal": goal,
        "repo": repo_path,
        "mandates_created": len(plan["active_mandates"]),
        "overlaps_detected": len(plan["resolutions"]),
        "absorbed": plan["absorbed"],
        "resolutions": plan["resolutions"],
        "mandate_ids": [m.mandate_id for m in plan["active_mandates"]],
    }


def main():
    parser = argparse.ArgumentParser(description="Puppet Master — standalone mode")
    parser.add_argument("goal", help="The goal to delegate")
    parser.add_argument("--repo", default=".", help="Path to the target repository")
    parser.add_argument("--executor", default="lappy-4b", help="Executor puppet to use")
    parser.add_argument("--plan-only", action="store_true", help="Only plan, don't execute")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    result = run_standalone(args.goal, args.repo, args.executor)
    print(json.dumps(result, indent=2))

    if not args.plan_only:
        logger.info("Plan-only mode. Set --execute to run executors.")
        # In a full implementation, this would dispatch executors
        # and run the validation loop


if __name__ == "__main__":
    from pathlib import Path  # noqa: E402
    main()
