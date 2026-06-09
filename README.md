# Puppet Master

Code delegation with scope integrity. A large model (Claude Code, GLM-5.1) decomposes code tasks into bounded mandates, dispatches them to small local models (qwen3.5:4b/8b on Lappy and Pi), validates the results structurally, then merges or rejects.

The mandate server is the authority — not the prompt. Agents don't guess what they can do. They ask the graph.

## The Problem

When you delegate code work to a small model, three things go wrong:

1. **Scope creep** — the model drifts outside its assigned files
2. **Goal drift** — the model loses track of what it was supposed to do
3. **State bleed** — changes leak between parallel workers, or sub-agents spawn unbounded trees

Puppet Master fixes this with structural enforcement, not prompting. A mandate is a contract. A scope enforcer is a gatekeeper. A git worktree is a sandbox. These exist in a queryable graph database — the same pattern that solved Godot API hallucination in GAT, applied to agent authority.

## Architecture

```
ROOT AGENT (Claude Code / GLM-5.1 / standalone)
  |
  |  Analyzes codebase, partitions work into mandates
  |  Validates diffs, merges branches, rejects bad work
  |
  v
MANDATE SERVER (FastMCP — the constitution)
  |
  |  mandate_graph.db — SQLite WAL, nodes + edges
  |  Every agent queries this to know its authority
  |  Server IS the authority, not the prompt
  |
  v
EXECUTORS (qwen3.5:4b/8b — Lappy/Pi via Ollama)
  |  Receive mandate, run scoped ReAct loop
  |  Work on sandboxed git branch only
  |  Cannot see other branches, cannot spawn sub-agents
  |  Budget enforced: max turns, max tokens
  v
VALIDATOR (mechanical checks before merge)
     Scope respected? Tests pass? Schema met?
     No new deps? No side effects?
```

## GAT Lineage

GAT solved agents hallucinating Godot APIs by putting 22,095 nodes and 54,966 edges in a queryable SQLite graph. Agents stopped guessing and started asking.

Puppet Master uses the same architecture for agent authority:

| GAT | Puppet Master |
|-----|---------------|
| `gat.db` — engine schema graph | `mandate_graph.db` — delegation tree |
| `get_class()` — agent asks what a node IS | `mandate/query` — agent asks what its mandate IS |
| `find_inheritance()` — trace class hierarchy | `mandate/ancestry` — trace delegation chain to root |
| `validate_node_path()` — validate connections | `scope/check` — validate file access before action |
| Domain-scoped tools | Mandate-scoped tool surfaces |
| `engine_schema.json` | `mandate_schema` — queryable authority graph |

The principle is identical: if agents can query a database for their authority, they don't hallucinate it.

## Files

```
puppet-master/
  mandate.py           # Core dataclasses: CodeMandate, Budget, ScopeViolation, ValidationReport
  graph.py             # MandateGraph — SQLite WAL nodes+edges, ancestry, FTS, subtree
  scope_enforcer.py    # Runtime scope enforcement — wraps tool calls, records violations
  sandbox.py           # Git worktree isolation — create, diff, merge, cleanup
  validator.py         # Mechanical validation — scope, tests, schema, deps, side effects
  executor.py          # ReAct loop — Ollama API, scoped tools, budget enforcement
  server.py            # FastMCP server — 12 tools (delegation, execution, validation, merge)
  config.py            # Puppet loading from YAML, DB path defaults
  standalone.py         # Autonomous entry point — own agent loop, no Claude Code needed
  puppets.yaml         # Executor registry (Lappy 4b/8b, Pi 4b)
  data/
    mandate_graph.db   # Auto-created, persists delegation tree across sessions
  tests/
    test_mandate.py    # Path globs, budget FSM, status transitions, schema validation
    test_scope.py      # Allowed/blocked tool calls, violation recording to graph
    test_graph.py       # Node/edge ops, ancestry traversal, subtree BFS, FTS search
    test_sandbox.py     # Worktree creation, file isolation, diff, merge, cleanup
    test_validator.py   # Scope checks, schema validation, side effects, overlap detection
    test_e2e.py         # Full delegation cycle, violation E2E, overlap detection
```

## Token and Request Tracking

Every executor tracks token consumption via its `Budget`:

```python
@dataclass
class Budget:
    max_turns: int = 20       # max LLM calls per mandate
    max_tokens: int = 50000    # max tokens per mandate
    turns_used: int = 0        # incremented each ReAct turn
    tokens_used: int = 0        # accumulated from Ollama responses
```

Budget is stored in the mandate graph and queried at runtime. When an executor calls the Ollama chat API, the response's `eval_count` and `prompt_eval_count` are parsed and recorded. If the budget is exhausted, the enforcer blocks the next tool call with a `budget_exhausted` violation.

### Per-Puppet Budgets (puppets.yaml)

| Puppet | Model | Max Turns | Max Tokens | Machine |
|--------|-------|-----------|------------|---------|
| `lappy-4b` | qwen3.5:4b | 20 | 50,000 | Lappy (192.168.0.33) |
| `lappy-8b` | qwen3.5:8b | 30 | 80,000 | Lappy (192.168.0.33) |
| `pi-4b` | qwen3.5:4b | 15 | 30,000 | Pi (192.168.0.237) |

### Request Counting

Each mandate tracks its full lifecycle in the graph:

```
mandate_nodes:   mandate (contract), executor (puppet), branch (worktree), result (output), violation (audit trail)
mandate_edges:  delegates_to, depends_on, validates, merges_into, rejected_by
```

The graph persists everything: who delegated what, to which executor, how many turns it took, what violations occurred, whether it was accepted or rejected. Run `mandate_list()` to see the full state.

## Delegation Flow

```
1. ROOT receives goal: "fix auth bugs in src/auth/"
2. ROOT calls delegate(goal, repo_path, executor="lappy-4b")
   -> Sandbox creates git worktree at checkpoint SHA
   -> Mandate created with allowed_paths=["src/auth/**"]
   -> Written to graph as mandate + branch nodes
3. EXECUTOR runs ReAct loop on isolated branch
   -> ScopeEnforcer blocks any tool call outside allowed paths
   -> Violations recorded to graph as violation nodes
   -> Budget enforced: 20 turns max, 50k tokens max
   -> Executor calls submit_result when done
4. ROOT calls mandate_validate(mandate_id)
   -> Mechanical checks: scope, tests, schema, deps, side effects
   -> If all pass: large-model quality gate (future)
5a. ACCEPT -> mandate_merge() -> fast-forward merge to main
5b. REJECT -> mandate_reject(reason, re_delegate=True) -> tighter mandate
```

## Mandate Status Machine

```
pending -> dispatched -> submitted -> accepted (terminal)
                                -> rejected -> pending (re-delegation)
```

Invalid transitions are rejected at runtime — you can't go from `pending` to `accepted` without going through dispatch and submit first.

## MCP Tools (12)

### Delegation
| Tool | Description |
|------|-------------|
| `delegate` | High-level: create mandate + worktree + dispatch in one call |
| `mandate_create` | Create a mandate node manually (for multi-mandate setups) |
| `mandate_query` | Read a mandate's full contract |
| `mandate_list` | All mandates with status + tree structure |
| `mandate_ancestry` | Full delegation chain from mandate to root |
| `mandate_children` | Direct sub-mandates |

### Execution
| Tool | Description |
|------|-------------|
| `mandate_submit` | Executor submits result for validation |
| `scope_check` | Self-check: is this tool/path allowed? |
| `status` | All mandates, puppet health, graph stats |

### Validation + Merge
| Tool | Description |
|------|-------------|
| `mandate_validate` | Run validation pipeline (mechanical + quality) |
| `mandate_merge` | Merge accepted branch into target |
| `mandate_reject` | Reject, record reason, optionally re-delegate |
| `mandate_search` | FTS search across past mandates |

## Setup

### Prerequisites
- Python 3.13+
- FastMCP (`pip install fastmcp`)
- httpx (`pip install httpx`)
- PyYAML (`pip install pyyaml`)
- Ollama running on Lappy (192.168.0.33:11434) and/or Pi (192.168.0.237:11434)
- Models installed: `qwen3.5:4b`, `qwen3.5:8b`

### Install

```bash
pip install fastmcp httpx pyyaml
git clone <this repo>
cd puppet-master
python -m pytest tests/ -v   # 74 tests should pass
```

### Run as MCP Server (with Claude Code)

```bash
python server.py
```

Add to `.mcp.json`:
```json
{
  "mcpServers": {
    "puppet-master": {
      "command": "python",
      "args": ["C:/path/to/puppet-master/server.py"]
    }
  }
}
```

### Run Standalone

```bash
python standalone.py "fix auth bugs" --repo ./my-project --executor lappy-4b
python standalone.py "refactor API layer" --repo ./my-project --executor lappy-8b --plan-only
```

## Token/Request Accounting

Token and request tracking is per-mandate, stored in the graph, and queryable:

```
mandate_list() -> each mandate includes budget.turns_used and budget.tokens_used
graph_stats() -> total nodes, edges, by_type, by_status breakdown
```

When the executor loop runs, each Ollama call's `eval_count` is added to `tokens_used`. The budget check runs before every tool call — when exceeded, the executor is forced to submit a partial result with the `budget_exhausted` flag set.

This is real token tracking, not estimates. Every mandate knows exactly how many tokens it burned and how many turns it took.

## Tests (74 passing)

```
tests/test_mandate.py     17 tests — Budget FSM, path globs, status transitions, schema
tests/test_scope.py       10 tests — Tool enforcement, path blocking, graph recording, wrap_tool
tests/test_graph.py       14 tests — Node/edge CRUD, ancestry, subtree BFS, FTS, stats
tests/test_sandbox.py      7 tests — Worktree creation, isolation, diff, merge, cleanup
tests/test_validator.py    10 tests — Scope checks, schema, side effects, overlap detection
tests/test_e2e.py          3 tests — Full cycle, violation E2E, overlap detection
```

## 2026 Prior Art

| System | Has | Missing |
|--------|-----|---------|
| OpenAI Codex sandbox | Path sandboxing (WritableRoot) | Mandate contracts, graph persistence |
| ccswarm | Git worktree isolation | Scope enforcement, validation |
| ALIGN | Game-theoretic mandates | Structural enforcement, git integration |
| AgentNet | DAG messaging | Code scope, git sandbox |
| EvoGit | Branch proposals + review | Mandate authority, depth limits |
| Code as Agent Harness | Role-based hierarchy concept | No implementation |
| **Puppet Master** | **All of the above** | Nothing — combines all five + GAT graph layer |

The novel addition: **queryable graph as the authority layer**. Same as GAT eliminated API hallucination — Puppet Master eliminates authority hallucination.
