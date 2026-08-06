# Volatix-Agent

Autonomous self-healing multi-agent engine that converts bug reports, issue tickets, and
failing error logs into verified code fixes.

A stateful LangGraph loop drives three agents — **Planner**, **Executor**, **Validator** —
with tools exposed over the Model Context Protocol. Untrusted edits execute inside
ephemeral Docker containers; failing tests feed a trimmed stack trace back into the
Executor until the suite passes or the retry budget is exhausted.

```
Issue ─▶ Planner ─▶ Executor ─▶ Validator ─┬─ pass ─▶ Patch + Root Cause Analysis
                        ▲                  │
                        └──── retry ◀──────┘  (trimmed stack trace)
```

## Status

| Phase | Scope | State |
|-------|-------|-------|
| 1 | Repo layout, CI, branch protection | ✅ Done |
| 2 | Ephemeral Docker sandbox | ✅ Done |
| 3 | MCP server & tool suite | ✅ Done |
| 4 | LangGraph nodes & state machine | ✅ Done |
| 5 | Stack-trace trimming & self-correction routing | ✅ Done |
| 6 | Output artifacts & terminal UI | ⬜ Not started |
| 7 | Evaluation suite & metrics | ⬜ Not started |

Phases 1–2 ship the package skeleton, the `AgentState` schema, the router decision
function, the Docker sandbox runner, and a green CI pipeline. Every other module is a
documented placeholder that raises `NotImplementedError` naming its phase.

### Sandbox isolation

`SandboxRunner` treats every command as hostile — it is running code an LLM wrote in
response to a bug report. Each run gets a fresh container with:

| Control | Default | Why |
|---------|---------|-----|
| Network | disabled | Untrusted code cannot phone home or exfiltrate |
| Capabilities | `cap_drop: ALL` | No raw sockets, no mount, no ptrace |
| Privilege escalation | `no-new-privileges:true` | setuid binaries cannot elevate |
| Memory | 512 MB | A runaway allocation cannot exhaust the host |
| PIDs | 256 | Blocks fork bombs |
| Wall clock | 30 s | Infinite loops are killed and reported as exit 124 |
| Lifetime | `remove(force=True)` in `finally` | No container leaks across retry cycles |

The container runs as the host UID rather than the image's baked-in user, so pytest can
write `__pycache__` into the mounted workspace without running as root.

### MCP tool suite

Tools are exposed over the Model Context Protocol (`mcp_server/server.py`), served over
stdio:

| Tool | Purpose |
|------|---------|
| `list_workspace_files` | Discover what exists before reaching for anything else |
| `get_ast_symbols` | Class/function signatures with **bodies stripped** |
| `read_file_content` | Read a file, or just a line range |
| `write_file_patch` | Exact-substring edit, or full rewrite |
| `run_test_suite` | Execute pytest in the Docker sandbox |

**Path confinement.** Every path an agent supplies is untrusted — it comes from an LLM
reasoning about a bug report. `Workspace.resolve()` canonicalizes each path and rejects
anything escaping the root: `../` traversal, absolute paths, symlinks pointing outside,
and NUL bytes.

**AST body-stripping** is the main context-window saving in the pipeline: a long module
collapses to a signature outline annotated with line ranges, so the agent reads only the
regions that matter. A file that doesn't parse returns its `SyntaxError` and location as
data rather than failing — a bug report is frequently *about* a syntax error.

```bash
VOLATIX_WORKSPACE=/path/to/repo python -m mcp_server.server
```

### The graph

```
planner ─▶ executor ─▶ validator ─┬─ pass ────────▶ finalize ─▶ END
              ▲                   │
              └─────── retry ◀────┤   (retry_count++, failure injected)
                                  │
                                  └─ budget spent ─▶ give_up ─▶ END
```

Nodes are built with their dependencies bound, so the graph holds no global state and
tests drive it with a fake client and a fake sandbox — no API key, no daemon.

| Node | Model config | Notes |
|------|--------------|-------|
| Planner | `claude-opus-5`, effort `high`, structured output | Sees the AST outline of the whole repo, never file bodies |
| Executor | `claude-opus-5`, effort `xhigh`, tool-use loop | Capped at 25 tool iterations |
| Validator | no model call | Sole owner of the pass/fail verdict |

Retries re-enter the **Executor**, not the Planner — re-planning an unchanged repository
would just spend tokens to reach the same plan.

The Executor is deliberately not given `run_test_suite`: validation belongs to the
Validator so the Executor cannot mark its own homework.

`temperature`, `top_p`, and `top_k` are never sent — Claude Opus 5 rejects them. Depth is
controlled with `effort` instead.

### Stack-trace trimming

Raw pytest output is mostly noise to a model trying to fix a bug. `trim_stack_trace`
keeps the assertion text and the innermost frame in *your* code, and drops session
banners, progress dots, passing tests, and framework frames from `_pytest`, `importlib`,
and `site-packages`. Measured on real pytest output:

| Failure kind | Before | After | Reduction |
|--------------|-------:|------:|----------:|
| Assertion + exception | 1012 ch | 206 ch | 80% |
| Collection (SyntaxError) | 1084 ch | 139 ch | 88% |

```
2 failed, 1 passed in 0.02s

tests/test_calc.py::test_add  (tests/test_calc.py:5)
  assert -1 == 5
  +  where -1 = add(2, 3)

tests/test_calc.py::test_boom  (calc.py:6)
  ZeroDivisionError: division by zero
```

Note `test_boom` resolves to `calc.py:6` — the innermost frame in the source, not the
`tests/test_calc.py:9` line where the assertion sits.

Unrecognised output (sandbox error, timeout kill, segfault) falls back to the raw tail
rather than returning nothing, which would silently break the retry loop.

## Layout

```text
volatix-agent/
├── .github/workflows/   CI pipelines (lint, unit, integration)
├── agent/               LangGraph state machine
│   ├── state.py         AgentState schema
│   ├── router.py        Conditional edge branching
│   └── nodes/           Planner, Executor, Validator, stack parser
├── mcp_server/          MCP tools (AST, file I/O, Docker execution)
├── sandbox/             Container orchestration + Dockerfile
├── evals/               Benchmark dataset & harness
├── cli/                 Rich console interface
└── tests/               Unit & integration tests
```

## Setup

```bash
git clone https://github.com/JCHETAN26/Volatix-Agent.git
cd Volatix-Agent

python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"

docker build -t volatix-sandbox:latest -f sandbox/Dockerfile.sandbox .
```

## Usage

Neither entry point runs end to end yet — both parse arguments and raise
`NotImplementedError` until their phase lands.

```bash
# Run the agent against a repository
python -m cli.main --issue "Fix TypeError in payment payload parser" --repo-path ./sample_app

# Run the benchmark suite
python -m evals.run_evals --output-file evals/results.json
```

## Development

```bash
pytest tests/unit/           # fast, no Docker required
pytest tests/integration/    # requires a running Docker daemon
black . && flake8 .
```

**All changes ship through pull requests.** Direct pushes to `main` are blocked by branch
protection — including for admins. A PR merges once all three CI jobs pass and the branch
is up to date with `main`. Reviewer approval is not required while the project is
single-maintainer; raise `required_approving_review_count` once there is a second
collaborator.
