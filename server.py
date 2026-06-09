"""Puppet Master — MCP mandate server (the constitution).

FastMCP server exposing all delegation, execution, validation,
and merge tools. Same architecture as GAT server: SQLite graph
as the authority layer, queryable at runtime.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from config import DB_PATH, get_puppet, load_puppets
from graph import MandateGraph
from mandate import (
    Budget,
    CodeMandate,
    MandateStatus,
    ScopeViolation,
    ScopeViolationError,
    ValidationReport,
    _now,
)
from sandbox import Sandbox
from scope_enforcer import ScopeEnforcer
from validator import Validator

logger = logging.getLogger(__name__)

mcp = FastMCP("puppet-master")

# Global graph instance
_graph: MandateGraph | None = None


def get_graph() -> MandateGraph:
    global _graph
    if _graph is None:
        _graph = MandateGraph(DB_PATH)
    return _graph


# ── Delegation Tools ──

@mcp.tool()
def delegate(
    goal: str,
    repo_path: str,
    executor: str = "lappy-4b",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    depth: int = 1,
) -> dict:
    """High-level delegation: analyze goal, create mandate, dispatch executor.

    Creates a mandate, spawns a git worktree, and starts the executor.
    """
    graph = get_graph()
    puppet = get_puppet(executor)
    if not puppet:
        return {"error": f"Unknown executor: {executor}. Available: {list(load_puppets().keys())}"}

    # Create sandbox
    sb = Sandbox(repo_path)
    branch = f"mandate-{_now().replace(':', '-').split('.')[0]}"
    checkpoint = sb.get_checkpoint()
    worktree_path = sb.create_worktree(branch, checkpoint)

    # Create mandate
    mandate = CodeMandate(
        goal=goal,
        branch=branch,
        checkpoint=checkpoint,
        allowed_paths=allowed_paths or [],
        forbidden_paths=forbidden_paths or [],
        budget=Budget(max_turns=puppet.max_turns, max_tokens=puppet.max_tokens),
        depth=depth,
        executor_id=executor,
    )
    mandate.transition("dispatched")

    # Write to graph
    graph.add_node(mandate.mandate_id, "mandate", mandate.to_dict())
    graph.add_node(f"br-{branch}", "branch", {
        "path": worktree_path,
        "branch_name": branch,
        "checkpoint": checkpoint,
    })
    graph.add_edge("root", mandate.mandate_id, "delegates_to")
    graph.add_edge(mandate.mandate_id, f"br-{branch}", "delegates_to")

    return {
        "mandate_id": mandate.mandate_id,
        "branch": branch,
        "worktree_path": worktree_path,
        "executor": executor,
        "checkpoint": checkpoint,
        "status": mandate.status,
    }


@mcp.tool()
def mandate_create(
    goal: str,
    repo_path: str,
    parent_id: str = "root",
    allowed_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    executor: str = "lappy-4b",
    depth: int = 1,
) -> dict:
    """Create a mandate node and spawn a git worktree for it."""
    graph = get_graph()
    puppet = get_puppet(executor)
    if not puppet:
        return {"error": f"Unknown executor: {executor}"}

    sb = Sandbox(repo_path)
    branch = f"mandate-{_now().replace(':', '-').split('.')[0]}"
    checkpoint = sb.get_checkpoint()
    worktree_path = sb.create_worktree(branch, checkpoint)

    mandate = CodeMandate(
        parent_id=parent_id,
        goal=goal,
        branch=branch,
        checkpoint=checkpoint,
        allowed_paths=allowed_paths or [],
        forbidden_paths=forbidden_paths or [],
        budget=Budget(max_turns=puppet.max_turns, max_tokens=puppet.max_tokens),
        depth=depth,
        executor_id=executor,
    )

    graph.add_node(mandate.mandate_id, "mandate", mandate.to_dict())
    graph.add_node(f"br-{branch}", "branch", {"path": worktree_path, "branch_name": branch})
    graph.add_edge(parent_id, mandate.mandate_id, "delegates_to")
    graph.add_edge(mandate.mandate_id, f"br-{branch}", "delegates_to")

    return mandate.to_dict()


@mcp.tool()
def mandate_query(mandate_id: str) -> dict:
    """Read a mandate's full details. Any agent can read its own mandate."""
    graph = get_graph()
    node = graph.get_node(mandate_id)
    if not node:
        return {"error": f"Mandate {mandate_id} not found"}
    return node["data"]


@mcp.tool()
def mandate_list() -> dict:
    """List all mandates with status and tree structure."""
    graph = get_graph()
    stats = graph.graph_stats()
    nodes = graph.find_by_type("mandate")
    tree = graph.find_subtree("root") if graph.get_node("root") else {}

    mandates = []
    for n in nodes:
        data = n["data"]
        children = graph.find_children(n["id"])
        mandates.append({
            "mandate_id": n["id"],
            "goal": data.get("goal", ""),
            "status": data.get("status", ""),
            "executor": data.get("executor_id"),
            "branch": data.get("branch", ""),
            "children": children,
        })

    return {
        "total": len(mandates),
        "mandates": mandates,
        "stats": stats,
        "tree_root": "root" in tree,
    }


@mcp.tool()
def mandate_ancestry(mandate_id: str) -> dict:
    """Get the full delegation chain from mandate to root."""
    graph = get_graph()
    chain = graph.find_ancestry(mandate_id)
    nodes = [graph.get_node(nid) for nid in chain]
    return {
        "chain": chain,
        "nodes": [{**n["data"], "id": n["id"]} for n in nodes if n],
    }


@mcp.tool()
def mandate_children(mandate_id: str) -> dict:
    """Get direct sub-mandates of a mandate."""
    graph = get_graph()
    children = graph.find_children(mandate_id)
    nodes = [graph.get_node(cid) for cid in children]
    return {
        "parent": mandate_id,
        "children": [{**n["data"], "id": n["id"]} for n in nodes if n],
    }


# ── Execution Tools ──

@mcp.tool()
def mandate_submit(mandate_id: str, result: dict) -> dict:
    """Executor submits its result for validation."""
    graph = get_graph()
    node = graph.get_node(mandate_id)
    if not node:
        return {"error": f"Mandate {mandate_id} not found"}

    mandate = CodeMandate.from_dict(node["data"])
    mandate.result = result
    mandate.transition("submitted")

    graph.update_node(mandate_id, mandate.to_dict())

    # Add result node
    graph.add_node(f"res-{mandate_id}", "result", {**result, "mandate_id": mandate_id})
    graph.add_edge(mandate_id, f"res-{mandate_id}", "validates")

    return {"status": "submitted", "mandate_id": mandate_id}


@mcp.tool()
def scope_check(mandate_id: str, tool_name: str, path: str | None = None) -> dict:
    """Check if a tool/path action is allowed by a mandate."""
    graph = get_graph()
    node = graph.get_node(mandate_id)
    if not node:
        return {"error": f"Mandate {mandate_id} not found", "allowed": False}

    mandate = CodeMandate.from_dict(node["data"])
    enforcer = ScopeEnforcer(mandate, graph=graph, executor_id="check")

    try:
        enforcer.check_tool(tool_name)
    except ScopeViolationError as e:
        return {"allowed": False, "reason": e.violation.reason}

    if path:
        try:
            enforcer.check_path(tool_name, path)
        except ScopeViolationError as e:
            return {"allowed": False, "reason": e.violation.reason}

    return {"allowed": True}


@mcp.tool()
def status() -> dict:
    """Overall status: all mandates, branches, puppet health."""
    graph = get_graph()
    stats = graph.graph_stats()
    puppets = load_puppets()

    # Check puppet health
    puppet_health = {}
    for name, cfg in puppets.items():
        try:
            import httpx
            resp = httpx.get(f"{cfg.ollama_url}/api/tags", timeout=5)
            puppet_health[name] = "up" if resp.status_code == 200 else "down"
        except Exception:
            puppet_health[name] = "down"

    return {
        "graph": stats,
        "puppets": puppet_health,
    }


# ── Validation + Merge Tools ──

@mcp.tool()
def mandate_validate(mandate_id: str) -> dict:
    """Validate a submitted mandate result — mechanical checks then quality gate."""
    graph = get_graph()
    node = graph.get_node(mandate_id)
    if not node:
        return {"error": f"Mandate {mandate_id} not found"}

    mandate = CodeMandate.from_dict(node["data"])
    if mandate.status != "submitted":
        return {"error": f"Mandate is {mandate.status}, not submitted"}

    result = mandate.result or {}
    worktree_path = result.get("worktree_path", "")

    # Mechanical checks
    validator = Validator(mandate, graph)
    report = validator.run(result)

    # Store report in graph
    graph.add_node(f"val-{mandate_id}", "result", {**report.to_dict(), "mandate_id": mandate_id})
    graph.update_node(mandate_id, {"validation": report.to_dict()})

    return report.to_dict()


@mcp.tool()
def mandate_merge(mandate_id: str, repo_path: str, target_branch: str = "master") -> dict:
    """Merge an accepted mandate branch into the target branch."""
    graph = get_graph()
    node = graph.get_node(mandate_id)
    if not node:
        return {"error": f"Mandate {mandate_id} not found"}

    mandate = CodeMandate.from_dict(node["data"])
    branch = mandate.branch

    if not branch:
        return {"error": "No branch associated with this mandate"}

    sb = Sandbox(repo_path)
    success = sb.merge_branch(branch, target_branch)

    if success:
        mandate.transition("accepted")
        graph.update_node(mandate_id, mandate.to_dict())
        graph.add_edge(f"br-{branch}", target_branch, "merges_into")
        sb.cleanup(branch)
        return {"merged": True, "branch": branch, "into": target_branch}
    else:
        return {"merged": False, "error": "Merge conflict"}


@mcp.tool()
def mandate_reject(mandate_id: str, reason: str, re_delegate: bool = False) -> dict:
    """Reject a mandate, optionally re-delegate."""
    graph = get_graph()
    node = graph.get_node(mandate_id)
    if not node:
        return {"error": f"Mandate {mandate_id} not found"}

    mandate = CodeMandate.from_dict(node["data"])
    mandate.transition("rejected")
    graph.update_node(mandate_id, {**mandate.to_dict(), "reject_reason": reason})

    if re_delegate:
        # Create re-delegation edge
        new_id = f"mnd-redel-{_now().replace(':', '-').split('.')[0]}"
        new_mandate = CodeMandate(
            parent_id=mandate_id,
            goal=mandate.goal,
            allowed_paths=mandate.allowed_paths,
            forbidden_paths=mandate.forbidden_paths,
            depth=max(0, mandate.depth - 1),
            executor_id=mandate.executor_id,
        )
        graph.add_node(new_mandate.mandate_id, "mandate", new_mandate.to_dict())
        graph.add_edge(mandate_id, new_mandate.mandate_id, "rejected_by")
        return {"rejected": True, "reason": reason, "re_delegated": True, "new_mandate_id": new_mandate.mandate_id}

    return {"rejected": True, "reason": reason}


@mcp.tool()
def mandate_search(query: str) -> dict:
    """Full-text search across past mandates."""
    graph = get_graph()
    results = graph.fts_search(query)
    return {
        "query": query,
        "count": len(results),
        "results": [{**n["data"], "id": n["id"]} for n in results],
    }


# ── Entry point ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")
