```markdown
# Volatix-Agent -- Autonomous Self-Healing Multi-Agent Engine
## Complete Build Plan & Engineering Specification

---

## 1. Project Overview & Problem Statement

### The Problem
Traditional AI coding assistants and single-prompt LLM wrappers generate code based on statistical likelihood without validating whether the code actually compiles, runs, or passes tests. This forces developers into a manual cycle of copying generated code, executing tests, encountering errors, and manually feeding stack traces back into the prompt. Furthermore, allowing unvalidated LLM-generated scripts to execute directly on a developer's host machine introduces security risks and environment pollution.

### The Solution
**Volatix-Agent** is an autonomous, self-healing developer engine that converts bug reports, issue tickets, or failing error logs into verified code fixes and pull requests. It uses a stateful multi-agent architecture (Planner, Executor, Validator) governed by LangGraph and tool-calling via the Model Context Protocol (MCP). Untrusted code fixes are executed safely inside ephemeral Docker containers, and test failures trigger an automated, closed-loop self-correction cycle before producing a final pull request.

---

## 2. System Architecture


```

```
                           ┌──────────────────────────────┐
                           │  Bug Report / Incident Log   │
                           └──────────────┬───────────────┘
                                          │
                                          ▼
                           ┌──────────────────────────────┐
                           │        Planner Agent         │
                           │  - Analyzes problem space    │
                           │  - Inspects AST / codebase   │
                           │  - Generates Execution Plan  │
                           └──────────────┬───────────────┘
                                          │
                                          ▼
                           ┌──────────────────────────────┐
                           │       Executor Agent         │  <═══>  [ Model Context Protocol (MCP) Server ]
                           │  - Applies code changes      │         ├─ Tool: File Reader / Writer
                           │  - Requests tool execution   │         ├─ Tool: AST Symbol Extractor
                           └──────────────┬───────────────┘         └─ Tool: Docker Test Runner
                                          │
                                          ▼
                           ┌──────────────────────────────┐
                           │       Validator Agent        │
                           │  - Triggers PyTest in Docker │
                           │  - Parses stdout / stack     │
                           └──────────────┬───────────────┘
                                          │
                                  ┌───────┴───────┐
                                  │ Pass or Fail? │
                                  └───────┬───────┘
                             Fail │       │ Pass
                                  │       └─────────────────────────────┐
                                  ▼                                     ▼
                  [ Self-Correction Loop ]              [ Output Generation ]
                  - Parses & trims stack trace          - Prepares Git Diff / Patch
                  - Routes error back to Executor       - Generates Root Cause Analysis

```

```

---

## 3. Tech Stack & Prerequisites

* **Language & Runtime:** Python 3.11+
* **Agent Orchestration:** LangGraph (`StateGraph`, `MemorySaver` for state persistence)
* **LLM Tooling Protocol:** Model Context Protocol (MCP) Python SDK
* **Code Parsing & AST Analysis:** Python `ast` module / `tree-sitter` (semantic AST chunking)
* **Sandboxed Execution Runtime:** Docker Engine, `docker` Python SDK (`docker-py`)
* **Testing Framework:** `pytest`, `unittest`
* **Observability & Tracing:** OpenTelemetry, Arize Phoenix / LangSmith
* **CLI / Visualization:** `rich` / `Textual` (Console TUI) or Next.js (Optional Web Dashboard)
* **DevOps & CI/CD:** GitHub Actions, Docker, PyTest, Black/Flake8

---

## 4. Phase-by-Phase Implementation Blueprint

### Phase 1: Environment Setup & Repository Rules
* **Goal:** Establish repository architecture, branch protection, and automated CI.
* **Tasks:**
  1. Initialize repository directory layout:
     ```text
     volatix-agent/
     ├── .github/workflows/     # CI pipelines (linting, tests, build)
     ├── mcp_server/            # MCP tools (AST, File I/O, Docker Execution)
     ├── agent/                 # LangGraph state machine & nodes
     │   ├── state.py           # TypedDict AgentState definition
     │   ├── nodes/             # Planner, Executor, Validator nodes
     │   └── router.py          # Conditional edge branching logic
     ├── sandbox/               # Docker container orchestration & dockerfiles
     ├── evals/                 # Benchmark dataset & evaluation harness
     ├── cli/                   # Rich console/TUI interface
     ├── tests/                 # Unit & integration tests
     ├── pyproject.toml
     └── README.md
     ```
  2. **Git Workflow Configuration (NO Direct Pushes to Main):**
     * Configure GitHub repository branch protection rules on `main`.
     * Require Pull Requests (PRs) with at least 1 approval before merging.
     * Require CI status checks to pass before merging.

---

### Phase 2: Ephemeral Docker Sandbox Infrastructure
* **Goal:** Build an isolated container runner to safely execute untrusted code edits and run test suites without affecting the host environment.
* **Tasks:**
  1. Create a base Dockerfile (`sandbox/Dockerfile.sandbox`) pre-configured with Python, `pytest`, `git`, and core development dependencies.
  2. Build the container manager (`sandbox/runner.py`) using `docker-py` to:
     * Mount target repository workspaces into isolated, ephemeral containers.
     * Execute arbitrary commands (e.g., `pytest tests/`) with configured execution timeouts (e.g., 30s cap to prevent infinite loops).
     * Cleanly capture `stdout`, `stderr`, and exit codes, and destroy container instances after execution.

---

### Phase 3: Model Context Protocol (MCP) Server & Tool Suite
* **Goal:** Implement standardized MCP tools that agents can invoke safely.
* **Tasks:**
  1. Build an MCP server (`mcp_server/server.py`) exposing the following tools:
     * `read_file_content`: Reads code files from workspace.
     * `write_file_patch`: Applies unified diffs or modified content to specified files.
     * `get_ast_symbols`: Parses source files using Python's `ast` module to return class/function definitions and signatures while stripping function bodies (reducing context window overhead).
     * `run_test_suite`: Triggers the Docker sandbox runner from Phase 2 and returns test execution logs.

---

### Phase 4: LangGraph Agent Nodes & State Machine
* **Goal:** Construct the stateful multi-agent execution graph governing agent reasoning, tool invocation, and state management.
* **Tasks:**
  1. Define the central state schema (`agent/state.py`):
     ```python
     from typing import TypedDict, List, Dict, Optional

     class AgentState(TypedDict):
         issue_description: str
         codebase_path: str
         plan: List[str]
         modified_files: Dict[str, str]
         test_output: str
         test_passed: bool
         retry_count: int
         max_retries: int
         error_summary: Optional[str]
     ```
  2. Implement Core Nodes (`agent/nodes/`):
     * **Planner Node:** Consumes issue description + AST symbol list $\rightarrow$ outputs an actionable, step-by-step resolution plan.
     * **Executor Node:** Inspects code files via MCP tools and applies code modifications.
     * **Validator Node:** Calls `run_test_suite` tool via Docker sandbox and evaluates test pass/fail status.

---

### Phase 5: Stack-Trace Trimming & Self-Correction Routing
* **Goal:** Enable closed-loop self-healing when unit tests fail.
* **Tasks:**
  1. Build a stack-trace parser (`agent/nodes/stack_parser.py`) that strips noisy framework stack traces and isolates key error messages and failing assertions.
  2. Implement conditional router logic (`agent/router.py`):
     * If `test_passed == True` $\rightarrow$ Route to **Finalize Patch Node**.
     * If `test_passed == False` AND `retry_count < max_retries` $\rightarrow$ Increment `retry_count`, inject trimmed stack trace into `error_summary`, and route back to **Executor Node**.
     * If `retry_count >= max_retries` $\rightarrow$ Route to **Failure Handler Node**.

---

### Phase 6: Output Artifacts & Terminal Interface
* **Goal:** Render agent state progress in real-time and export production-ready patches.
* **Tasks:**
  1. **Artifact Generation:** Output a unified `.diff` file and a structured markdown Root Cause Analysis report explaining the bug and verification proof.
  2. **Console CLI (`cli/main.py`):** Use `rich` or `Textual` to render live agent transitions (*Planning $\rightarrow$ Executing $\rightarrow$ Validating $\rightarrow$ Retrying*) alongside real-time execution logs.

---

### Phase 7: Evaluation Suite & Metric Gathering Framework
* **Goal:** Systematically run benchmark experiments to record baseline vs. optimized performance metrics.
* **Tasks:**
  1. **Benchmark Dataset Construction (`evals/dataset/`):**
     * Assemble 50--100 bug scenarios across 5 open-source Python repositories (or synthetic projects), spanning syntax errors, logic bugs, type errors, and missing edge-case handling.
     * Ensure each scenario contains an automated test that fails on the bug and passes when resolved.
  2. **Evaluation Harness (`evals/run_evals.py`):**
     * Build an automated runner that executes the benchmark suite under two conditions:
       * **Condition A (Baseline):** Single-pass LLM prompt (no agent loop, no AST trimming, no retry loop).
       * **Condition B (Optimized Volatix-Agent):** Full multi-agent self-healing loop with AST trimming and Docker sandboxing.
     * Track and export execution logs via OpenTelemetry / Phoenix:
       * **Completion Rate (Pass@1):** Percentage of scenarios passing unit tests.
       * **Convergence Speed:** Average retry iterations required before achieving passing tests.
       * **Token Efficiency:** Total prompt + completion token usage per task.
       * **Sandbox Latency:** Container spin-up and execution overhead (ms).

---

## 5. CI/CD & Quality Assurance Pipeline

Every contribution **MUST** follow PR-based submission. Direct commits to `main` are blocked.

### GitHub Actions Workflow (`.github/workflows/ci.yml`)
1. **Linting & Code Style:** Run `black --check .` and `flake8 .`
2. **Unit Tests:** Execute `pytest tests/unit/` verifying:
   * LangGraph state transitions and router logic mocks.
   * Stack-trace parser correctness.
   * MCP tool payload schemas.
3. **Integration Tests:** Spin up Docker service in CI and verify that `SandboxRunner` executes container commands cleanly.
4. **Automated PR Enforcement:** PRs can only merge when all workflow steps pass.

---

## 6. How to Run & Reproduce the Project Locally

```bash
# 1. Clone repository
git clone [https://github.com/JCHETAN26/volatix-agent.git](https://github.com/JCHETAN26/volatix-agent.git)
cd volatix-agent

# 2. Setup virtual environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Build Docker Sandbox image
docker build -t volatix-sandbox:latest -f sandbox/Dockerfile.sandbox .

# 4. Run Agent via CLI
python -m cli.main --issue "Fix TypeError in user payment payload parser" --repo-path ./sample_app

# 5. Run Evaluation Suite & Metrics Collector
python -m evals.run_evals --output-file evals/results.json

```

```

```