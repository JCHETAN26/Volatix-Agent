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
| 2 | Ephemeral Docker sandbox | ⬜ Not started |
| 3 | MCP server & tool suite | ⬜ Not started |
| 4 | LangGraph nodes & state machine | ⬜ Not started |
| 5 | Stack-trace trimming & self-correction routing | ⬜ Not started |
| 6 | Output artifacts & terminal UI | ⬜ Not started |
| 7 | Evaluation suite & metrics | ⬜ Not started |

Phase 1 ships the package skeleton, the `AgentState` schema, the router decision
function, and a green CI pipeline. Every other module is a documented placeholder that
raises `NotImplementedError` naming its phase.

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
