"""Executor — ReAct loop for puppet agents via Ollama API.

Scoped tools via ScopeEnforcer, Ollama API with "think": false.
Executors receive a mandate, run a bounded loop, return typed results.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from config import PuppetConfig
from graph import MandateGraph
from mandate import Budget, CodeMandate, ScopeViolationError
from scope_enforcer import ScopeEnforcer

logger = logging.getLogger(__name__)

# Tool definitions exposed to executor LLM
EXECUTOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the worktree",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to worktree root"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the worktree",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to worktree root"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: root)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a pattern in files",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Search pattern"},
                    "path": {"type": "string", "description": "Directory to search in"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run the test suite in the worktree",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Test command to run (e.g. 'pytest')"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Think out loud before taking action. Use this to plan your approach or analyze observations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Your reasoning or plan"},
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_result",
            "description": "Submit your work as the final result. Call this when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "What was done"},
                    "files_changed": {"type": "array", "items": {"type": "string"}, "description": "List of changed files"},
                    "tests_result": {"type": "string", "description": "Test output summary"},
                },
                "required": ["summary", "files_changed"],
            },
        },
    },
]


def _build_system_prompt(mandate: CodeMandate) -> str:
    """Build the system prompt for the executor LLM."""
    return f"""You are a code executor. You have a specific mandate to complete.

## Your Mandate
- Goal: {mandate.goal}
- Allowed paths: {', '.join(mandate.allowed_paths) or 'all'}
- Forbidden paths: {', '.join(mandate.forbidden_paths) or 'none'}
- Allowed tools: {', '.join(mandate.allowed_tools)}
- Budget: {mandate.budget.max_turns} turns, {mandate.budget.max_tokens} tokens
- Depth: {mandate.depth} (cannot spawn sub-agents)

## Rules
1. ONLY modify files within allowed paths. Violations will be blocked.
2. Do NOT commit, push, or install dependencies.
3. Call submit_result when your work is complete.
4. If you cannot complete the goal, submit a partial result explaining why.
5. Focus on the goal. Do not scope creep."""


class Executor:
    """Runs a mandate via Ollama with scoped tools."""

    def __init__(
        self,
        mandate: CodeMandate,
        puppet: PuppetConfig,
        worktree_path: str,
        graph: MandateGraph | None = None,
    ):
        self.mandate = mandate
        self.puppet = puppet
        self.worktree_path = worktree_path
        self.graph = graph
        self.enforcer = ScopeEnforcer(mandate, graph=graph, executor_id=puppet.name)
        self.result: dict | None = None
        self.violations: list[dict] = []
        self._no_tool_count: int = 0

    def run(self) -> dict:
        """Execute the mandate. Returns the result dict."""
        messages = [
            {"role": "system", "content": _build_system_prompt(self.mandate)},
            {"role": "user", "content": f"Complete this mandate: {self.mandate.goal}"},
        ]

        for turn in range(self.mandate.budget.max_turns):
            self.mandate.budget.record_turn()

            try:
                response = self._ollama_call(messages)
            except Exception as e:
                logger.error(f"Ollama call failed: {e}")
                return self._partial_result(f"Ollama error: {e}")

            # Track token usage from Ollama response
            eval_count = response.get("eval_count", 0)
            prompt_count = response.get("prompt_eval_count", 0)
            self.mandate.budget.record_tokens(prompt_count + eval_count)

            msg = response.get("message", {})
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            if not tool_calls:
                self._no_tool_count += 1
                messages.append({"role": "assistant", "content": content})
                if self._no_tool_count >= 2:
                    return self._partial_result(f"No tool calls. Last content: {content[:500]}")
                messages.append({
                    "role": "user",
                    "content": "You must call submit_result with your final answer. If you're done, call submit_result now.",
                })
                continue

            self._no_tool_count = 0
            messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError as e:
                    tool_name_safe = fn.get("name", "unknown")
                    messages.append({"role": "tool", "content": f"Error: malformed arguments for {tool_name_safe}. {e}"})
                    continue

                if tool_name == "submit_result":
                    self.result = args
                    return args

                # Enforce scope
                try:
                    self.enforcer.enforce(tool_name, args)
                except ScopeViolationError as e:
                    self.violations.append(e.violation.to_dict())
                    tool_result = f"BLOCKED: {e.violation.reason}. {e}"
                else:
                    tool_result = self._execute_tool(tool_name, args)

                messages.append({"role": "tool", "content": tool_result})

            # Check if result was set by submit_result
            if self.result is not None:
                return self.result

            if not self.mandate.budget.can_proceed():
                return self._partial_result("Budget exhausted")

        return self._partial_result("Max turns reached")

    def _ollama_call(self, messages: list[dict]) -> dict:
        """Call Ollama chat API."""
        payload = {
            "model": self.puppet.model,
            "messages": messages,
            "tools": EXECUTOR_TOOLS,
            "stream": False,
            "options": {"num_predict": 2048},
        }
        # qwen3.5 thinking must be disabled
        if "qwen" in self.puppet.model:
            payload["think"] = False

        resp = httpx.post(
            f"{self.puppet.ollama_url}/api/chat",
            json=payload,
            timeout=120.0,
        )
        resp.raise_for_status()
        return resp.json()

    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """Execute a scoped tool call."""
        path = args.get("path", "")

        if tool_name == "think":
            return f"Thought noted: {args.get('thought', '')}"

        if tool_name == "read_file":
            full_path = os.path.join(self.worktree_path, path)
            try:
                with open(full_path) as f:
                    return f.read()[:8000]
            except FileNotFoundError:
                return f"Error: File not found: {path}"
            except Exception as e:
                return f"Error reading {path}: {e}"

        elif tool_name == "write_file":
            full_path = os.path.join(self.worktree_path, path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            try:
                with open(full_path, "w") as f:
                    f.write(args.get("content", ""))
                return f"Wrote {path} ({len(args.get('content', ''))} chars)"
            except Exception as e:
                return f"Error writing {path}: {e}"

        elif tool_name == "list_files":
            target = os.path.join(self.worktree_path, path) if path else self.worktree_path
            if not os.path.isdir(target):
                return f"Error: Not a directory: {path}"
            try:
                entries = []
                for name in sorted(os.listdir(target)):
                    full = os.path.join(target, name)
                    entries.append(f"{'[DIR]' if os.path.isdir(full) else '[FILE]'} {name}")
                return "\n".join(entries[:100])
            except Exception as e:
                return f"Error listing {path}: {e}"

        elif tool_name == "search_files":
            pattern = args.get("pattern", "")
            target = os.path.join(self.worktree_path, args.get("path", ""))
            try:
                results = []
                for root, dirs, files in os.walk(target):
                    for name in files:
                        if pattern.lower() in name.lower():
                            results.append(os.path.relpath(os.path.join(root, name), self.worktree_path))
                return "\n".join(results[:50]) if results else "No matches found"
            except Exception as e:
                return f"Error searching: {e}"

        elif tool_name == "run_tests":
            import subprocess
            cmd = args.get("command", "python -m pytest")
            try:
                result = subprocess.run(
                    cmd, shell=True, cwd=self.worktree_path,
                    capture_output=True, text=True, timeout=60,
                )
                output = result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout
                if result.stderr:
                    output += "\nSTDERR: " + (result.stderr[-1000:])
                return f"Exit code: {result.returncode}\n{output}"
            except Exception as e:
                return f"Error running tests: {e}"

        return f"Unknown tool: {tool_name}"

    def _partial_result(self, reason: str) -> dict:
        return {
            "summary": f"Partial result — {reason}",
            "files_changed": [],
            "tests_result": "not run",
            "violations": self.violations,
            "partial": True,
        }
