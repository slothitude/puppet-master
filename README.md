# Puppet Master

Code delegation with scope integrity. A large model (Claude Code, GLM-5.1) decomposes code tasks into bounded mandates, dispatches them to small local models (qwen3.5:4b/8b on Lappy and Pi), validates the results structurally, then merges or rejects.

The mandate server is the authority — not the prompt. Agents don't guess what they can do. They ask the graph.

## Quick Start

```bash
# 1. Install dependencies
pip install fastmcp httpx pyyaml

# 2. Make sure Ollama is running with a model pulled
ollama pull qwen3.5:4b

# 3. Run tests to verify
python -m pytest tests/ -v   # 102 tests should pass

# 4. Start the MCP server (for use with Claude Code)
python server.py
```

Add to your `.mcp.json`:
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

## How It Works

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

## Usage

### With Claude Code (MCP Server)

This is the intended use case. Claude Code acts as the root agent and calls Puppet Master's MCP tools to delegate work.

**1. Delegate a task to an executor:**
```
delegate(
  goal="Fix auth bug in login.py — add credential check",
  repo_path="./my-project",
  executor="lappy-4b",
  allowed_paths=["src/auth/**"],
  forbidden_paths=["src/utils/**"]
)
```
Returns a `mandate_id`, branch name, worktree path, and checkpoint SHA. The executor runs on an isolated git branch.

**2. Check on all mandates:**
```
mandate_list()
```
Returns every mandate with its status, goal, executor, branch, and children. The `stats` field includes graph-wide node/edge counts.

**3. Read a specific mandate's contract:**
```
mandate_query(mandate_id="mnd-a1b2c3d4")
```
Returns the full contract: allowed paths, forbidden paths, budget remaining, status, executor assignment.

**4. Trace delegation ancestry:**
```
mandate_ancestry(mandate_id="mnd-a1b2c3d4")
```
Returns the full chain from this mandate back to root. Useful for understanding why a mandate exists.

**5. Check if an action is allowed before executing:**
```
scope_check(mandate_id="mnd-a1b2c3d4", tool_name="write_file", path="src/auth/login.py")
```
Returns `{allowed: true}` or `{allowed: false, reason: "path_outside_scope"}`. Use this to preview whether the enforcer would block something.

**6. Validate a completed mandate:**
```
mandate_validate(mandate_id="mnd-a1b2c3d4")
```
Runs the full validation pipeline: scope check, test results, schema compliance, dependency check, side-effect detection. Returns a `ValidationReport` with `accepted: true/false` and per-check details.

**7. Merge an accepted mandate:**
```
mandate_merge(mandate_id="mnd-a1b2c3d4", repo_path="./my-project", target_branch="main")
```
Fast-forwards the mandate's branch into the target. Cleans up the worktree on success.

**8. Reject and re-delegate (if validation fails):**
```
mandate_reject(mandate_id="mnd-a1b2c3d4", reason="Scope violation — touched forbidden path", re_delegate=True)
```
Records the rejection reason, optionally creates a new tighter mandate with `depth - 1`.

**9. Search past mandates:**
```
mandate_search(query="auth bug")
```
Full-text search across all mandate goals, results, and rejection reasons.

**10. Check overall health:**
```
status()
```
Returns graph stats and puppet health (whether Ollama is reachable on each machine).

### Standalone (No Claude Code)

```bash
python standalone.py "fix auth bugs" --repo ./my-project --executor lappy-4b
python standalone.py "refactor API layer" --repo ./my-project --executor lappy-8b --plan-only
```

Standalone mode does heuristic partitioning (one mandate per code directory) instead of LLM-driven analysis. Use `--plan-only` to see what mandates would be created without executing them.

### From Python

```python
from config import load_puppets, get_puppet
from graph import MandateGraph
from mandate import Budget, CodeMandate
from sandbox import Sandbox
from executor import Executor

# Load a puppet
puppet = get_puppet("lappy-4b")

# Create a mandate
mandate = CodeMandate(
    goal="Fix auth bug",
    allowed_paths=["src/auth/**"],
    forbidden_paths=["src/utils/**"],
    budget=Budget(max_turns=20, max_tokens=50000),
    depth=1,
)

# Create isolated worktree
sb = Sandbox("./my-project")
branch = f"fix-{mandate.mandate_id[:8]}"
checkpoint = sb.get_checkpoint()
worktree_path = sb.create_worktree(branch, checkpoint)
mandate.branch = branch
mandate.checkpoint = checkpoint

# Run the executor
executor = Executor(mandate, puppet, worktree_path)
result = executor.run()

# Inspect result
print(result["summary"])
print(result["files_changed"])

# Merge if happy
sb.merge_branch(branch, "main")
sb.cleanup(branch)
```

## MCP Tools Reference (14)

### Delegation
| Tool | Description |
|------|-------------|
| `delegate` | Create mandate + worktree + dispatch in one call |
| `mandate_create` | Create a mandate manually (for multi-mandate setups) |
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

### Executor Tools (available to the small model inside the ReAct loop)
| Tool | Description |
|------|-------------|
| `read_file` | Read a file from the worktree |
| `write_file` | Write content to a file in the worktree |
| `list_files` | List files in a directory |
| `search_files` | Search for a pattern in files |
| `run_tests` | Run the test suite |
| `think` | Think out loud — structured reasoning scratchpad |
| `submit_result` | Submit work as the final result |

## Mandate Lifecycle

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
5a. ACCEPT -> mandate_merge() -> fast-forward merge to main
5b. REJECT -> mandate_reject(reason, re_delegate=True) -> tighter mandate
```

### Status Machine

```
pending -> dispatched -> submitted -> accepted (terminal)
                                -> rejected -> pending (re-delegation)
```

Invalid transitions are rejected at runtime. You can't go from `pending` to `accepted` without dispatch and submit.

## Configuration

### puppets.yaml

```yaml
puppets:
  lappy-4b:
    name: "Lappy qwen3.5:4b"
    ollama_url: "http://192.168.0.33:11434"
    model: "qwen3.5:4b"
    max_concurrent: 2
    think: false
    default_budget:
      max_turns: 20
      max_tokens: 50000

  lappy-8b:
    name: "Lappy qwen3.5:8b"
    ollama_url: "http://192.168.0.33:11434"
    model: "qwen3.5:8b"
    max_concurrent: 1
    think: false
    default_budget:
      max_turns: 30
      max_tokens: 80000

  pi-4b:
    name: "Pi qwen3.5:4b"
    ollama_url: "http://192.168.0.237:11434"
    model: "qwen3.5:4b"
    max_concurrent: 1
    think: false
    default_budget:
      max_turns: 15
      max_tokens: 30000
```

| Puppet | Model | Max Turns | Max Tokens | Machine |
|--------|-------|-----------|------------|---------|
| `lappy-4b` | qwen3.5:4b | 20 | 50,000 | Lappy (192.168.0.33) |
| `lappy-8b` | qwen3.5:8b | 30 | 80,000 | Lappy (192.168.0.33) |
| `pi-4b` | qwen3.5:4b | 15 | 30,000 | Pi (192.168.0.237) |

### Mandate Budget Tracking

Every mandate tracks token consumption via its `Budget`:
- `turns_used` — incremented each ReAct turn
- `tokens_used` — accumulated from Ollama `eval_count` + `prompt_eval_count`

Budget is stored in the mandate graph and queryable via `mandate_list()`. When budget is exhausted, the enforcer blocks further tool calls with a `budget_exhausted` violation.

### Adding a New Executor

Add an entry to `puppets.yaml`:
```yaml
puppets:
  my-machine:
    ollama_url: "http://my-ip:11434"
    model: "qwen3.5:4b"
    max_concurrent: 1
    default_budget:
      max_turns: 25
      max_tokens: 60000
```

Then use it: `delegate(goal="...", repo_path="./", executor="my-machine")`

## Overlap Partitioning

When creating multiple mandates that touch overlapping paths, the partitioner classifies and resolves conflicts:

| Type | Meaning | Resolution |
|------|---------|------------|
| `ABSORB` | One path is a subset of another | Merge into the larger mandate |
| `EXTRACT` | Shared utility touched by both | Create a third mandate for the shared code |
| `SEQUENCE` | Incidental collision | Serialize with `depends_on` edges |

This runs automatically when you create overlapping mandates. No manual intervention needed.

## Project Structure

```
puppet-master/
  server.py            # FastMCP server — 14 tools for delegation, validation, merge
  mandate.py           # CodeMandate, Budget, ScopeViolation, ValidationReport
  graph.py             # MandateGraph — SQLite WAL nodes+edges, ancestry, FTS, subtree
  scope_enforcer.py    # Runtime scope enforcement — wraps tool calls, records violations
  sandbox.py           # Git worktree isolation — create, diff, merge, cleanup
  validator.py         # Mechanical validation — scope, tests, schema, deps, side effects
  executor.py          # ReAct loop — Ollama API, scoped tools, budget enforcement
  partitioner.py       # Overlap classification (ABSORB/EXTRACT/SEQUENCE) + resolution
  config.py            # Puppet loading from YAML, DB path defaults
  standalone.py         # Autonomous entry point — own agent loop, no Claude Code needed
  puppets.yaml         # Executor registry (Lappy 4b/8b, Pi 4b)
  data/
    mandate_graph.db   # Auto-created, persists delegation tree across sessions
  tests/               # 102 tests
```

## Prerequisites

- Python 3.13+
- FastMCP (`pip install fastmcp`)
- httpx (`pip install httpx`)
- PyYAML (`pip install pyyaml`)
- Ollama running with `qwen3.5:4b` or `qwen3.5:8b` pulled
- Git (for worktree isolation)
