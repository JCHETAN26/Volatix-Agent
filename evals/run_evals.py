"""Benchmark harness -- Phase 7.

Runs every scenario under two conditions and exports the metrics:

    baseline  single-pass prompt, full file contents, no tools, no retry
    agent     the full self-healing graph with AST trimming and the sandbox

Every scenario runs on a fresh copy of its repository, and a failure in one scenario is
recorded and stepped over rather than aborting the sweep -- a benchmark that dies on
scenario 3 of 10 tells you nothing.
"""

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import List, Optional

from rich.console import Console
from rich.table import Table

from agent.graph import build_graph
from agent.llm import ClaudeClient, LLMError
from agent.state import build_initial_state
from agent.tools import ToolBackend
from evals.baseline import run_baseline
from evals.loader import Scenario, load_all
from evals.metrics import (
    CONDITION_AGENT,
    CONDITION_BASELINE,
    ScenarioResult,
    build_report,
)
from mcp_server.workspace import Workspace
from sandbox.runner import SandboxError, SandboxRunner


class MeasuringRunner:
    """Wraps SandboxRunner to record container latency, which the plan asks for."""

    def __init__(self, inner: SandboxRunner):
        self.inner = inner
        self.total_ms = 0.0
        self.runs = 0

    @property
    def timeout(self) -> int:
        return self.inner.timeout

    def run_tests(self, workspace_path: str, test_path: str = "tests/"):
        result = self.inner.run_tests(workspace_path, test_path)
        self.total_ms += result.duration_ms
        self.runs += 1
        return result

    def run(self, workspace_path: str, command):
        return self.inner.run(workspace_path, command)


def _new_runner(timeout: int) -> MeasuringRunner:
    return MeasuringRunner(SandboxRunner(timeout=timeout))


def run_baseline_scenario(scenario: Scenario, timeout: int) -> ScenarioResult:
    """Condition A: one prompt, one write, one test run."""
    repo = scenario.materialise()
    runner = _new_runner(timeout)
    client = ClaudeClient()
    started = time.perf_counter()
    error = None
    passed = False

    try:
        run_baseline(scenario.issue, repo, client)
        passed = runner.run_tests(str(repo), scenario.test_path).passed
    except (LLMError, SandboxError) as exc:
        error = str(exc)
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    return ScenarioResult(
        scenario_id=scenario.id,
        category=scenario.category,
        condition=CONDITION_BASELINE,
        passed=passed,
        wall_ms=(time.perf_counter() - started) * 1000,
        sandbox_ms=runner.total_ms,
        sandbox_runs=runner.runs,
        tokens=client.usage.as_dict(),
        error=error,
    )


def run_agent_scenario(scenario: Scenario, timeout: int, max_retries: int) -> ScenarioResult:
    """Condition B: the full self-healing graph."""
    repo = scenario.materialise()
    runner = _new_runner(timeout)
    client = ClaudeClient()
    started = time.perf_counter()
    error = None
    final = {}

    try:
        workspace = Workspace(str(repo))
        graph = build_graph(
            workspace,
            client=client,
            runner=runner,
            backend=ToolBackend(workspace),
            test_path=scenario.test_path,
        )
        state = build_initial_state(scenario.issue, str(repo), max_retries=max_retries)
        config = {"configurable": {"thread_id": scenario.id}}
        final = graph.invoke(state, config)
    except (LLMError, SandboxError) as exc:
        error = str(exc)
    finally:
        shutil.rmtree(repo, ignore_errors=True)

    return ScenarioResult(
        scenario_id=scenario.id,
        category=scenario.category,
        condition=CONDITION_AGENT,
        passed=bool(final.get("test_passed")),
        retries=int(final.get("retry_count", 0)),
        wall_ms=(time.perf_counter() - started) * 1000,
        sandbox_ms=runner.total_ms,
        sandbox_runs=runner.runs,
        tokens=client.usage.as_dict(),
        error=error,
    )


def render_summary(report: dict, console: Console) -> None:
    table = Table(title="Volatix benchmark")
    table.add_column("Metric", style="bold")
    table.add_column("Baseline (A)", justify="right")
    table.add_column("Agent (B)", justify="right")

    baseline = report["conditions"][CONDITION_BASELINE]
    agent = report["conditions"][CONDITION_AGENT]

    def row(label: str, key: str, suffix: str = "") -> None:
        table.add_row(
            label,
            f"{baseline.get(key, 0)}{suffix}",
            f"{agent.get(key, 0)}{suffix}",
        )

    row("Scenarios", "scenarios")
    row("Passed", "passed")
    row("Pass@1", "pass_rate", "%")
    row("Avg retries (on success)", "avg_retries_on_success")
    row("Avg total tokens", "avg_total_tokens")
    row("Avg billable input tokens", "avg_billable_input_tokens")
    row("Avg model calls", "avg_model_calls")
    row("Avg sandbox ms / run", "avg_sandbox_ms_per_run")
    row("Avg wall ms", "avg_wall_ms")
    row("Errors", "errors")

    console.print(table)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Volatix-Agent benchmark suite.")
    parser.add_argument("--output-file", default="evals/results.json", help="Metrics destination")
    parser.add_argument(
        "--condition",
        choices=["baseline", "agent", "both"],
        default="both",
        help="Which experimental condition to run (default: %(default)s)",
    )
    parser.add_argument("--category", action="append", help="Limit to a bug category")
    parser.add_argument("--scenario", action="append", help="Limit to specific scenario ids")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=60, help="Sandbox timeout, seconds")
    return parser


def select_scenarios(args: argparse.Namespace) -> List[Scenario]:
    scenarios = load_all(categories=args.category)
    if args.scenario:
        wanted = set(args.scenario)
        scenarios = [s for s in scenarios if s.id in wanted]
    return scenarios


def run(args: argparse.Namespace, console: Optional[Console] = None) -> int:
    console = console or Console()
    scenarios = select_scenarios(args)
    if not scenarios:
        console.print("[red]No scenarios matched the given filters.[/red]")
        return 2

    conditions = (
        [CONDITION_BASELINE, CONDITION_AGENT]
        if args.condition == "both"
        else [CONDITION_BASELINE if args.condition == "baseline" else CONDITION_AGENT]
    )

    results: List[ScenarioResult] = []
    for condition in conditions:
        for scenario in scenarios:
            console.print(f"[dim]{condition:<8}[/dim] {scenario.id} ...", end=" ")
            if condition == CONDITION_BASELINE:
                result = run_baseline_scenario(scenario, args.timeout)
            else:
                result = run_agent_scenario(scenario, args.timeout, args.max_retries)
            results.append(result)

            if result.error:
                console.print(f"[yellow]error[/yellow] ({result.error[:60]})")
            else:
                verdict = "[green]pass[/green]" if result.passed else "[red]fail[/red]"
                console.print(f"{verdict} ({result.retries} retries)")

    report = build_report(results)
    destination = Path(args.output_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    console.print()
    render_summary(report, console)
    console.print(f"\nWrote {destination}")
    return 0


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
