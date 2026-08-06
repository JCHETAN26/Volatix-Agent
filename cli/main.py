"""Rich console entry point -- Phase 6.

Renders live agent transitions (Planning -> Executing -> Validating -> Retrying) while
the graph runs, then writes the diff and root-cause report.

Orchestration is kept separate from rendering: ``iter_run_events`` is a plain generator
over graph updates, so the flow can be tested without driving a terminal.
"""

import argparse
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.artifacts import build_artifacts
from agent.graph import build_graph
from agent.llm import ClaudeClient, LLMError
from agent.state import DEFAULT_MAX_RETRIES, build_initial_state
from agent.tools import ToolBackend
from mcp_server.workspace import Workspace, WorkspaceError
from sandbox.runner import SandboxError, SandboxRunner

# Node name -> what the user sees while it runs.
NODE_LABELS = {
    "planner": ("Planning", "Analysing the issue against the repository outline"),
    "executor": ("Executing", "Applying code changes"),
    "validator": ("Validating", "Running the test suite in the sandbox"),
    "retry": ("Retrying", "Feeding the trimmed failure back to the executor"),
    "finalize": ("Finalizing", "Tests pass — preparing artifacts"),
    "give_up": ("Failed", "Retry budget exhausted"),
}


@dataclass
class RunEvent:
    """One node transition, already reduced to what the console needs."""

    node: str
    label: str
    detail: str
    update: Dict[str, Any]


def describe_event(node: str, update: Dict[str, Any]) -> RunEvent:
    """Turn a raw graph update into a renderable event."""
    label, detail = NODE_LABELS.get(node, (node.title(), ""))

    if node == "planner":
        steps = len(update.get("plan") or [])
        detail = f"Produced a {steps}-step plan"
    elif node == "executor":
        changed = list(update.get("modified_files") or {})
        detail = f"Modified {len(changed)} file(s): {', '.join(sorted(changed)) or 'none'}"
    elif node == "validator":
        detail = "Tests passed" if update.get("test_passed") else "Tests failed"
    elif node == "retry":
        detail = f"Retry {update.get('retry_count', '?')} — re-running the executor"

    return RunEvent(node=node, label=label, detail=detail, update=update)


def iter_run_events(graph, state, config) -> Iterator[RunEvent]:
    """Stream the graph, yielding one event per node completion."""
    for chunk in graph.stream(state, config, stream_mode="updates"):
        for node, update in chunk.items():
            yield describe_event(node, update or {})


def _summary_table(state: Dict[str, Any]) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()

    passed = state.get("test_passed", False)
    table.add_row("Result", "[green]tests pass[/green]" if passed else "[red]tests fail[/red]")
    table.add_row("Retries", f"{state.get('retry_count', 0)} of {state.get('max_retries', 0)}")
    table.add_row("Files changed", str(len(state.get("modified_files") or {})))

    usage = state.get("token_usage") or {}
    if usage:
        table.add_row(
            "Tokens",
            f"{usage.get('total_tokens', 0):,} over {usage.get('calls', 0)} calls",
        )
    return table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="volatix",
        description="Turn a bug report into a verified, test-passing patch.",
    )
    parser.add_argument("--issue", required=True, help="Bug report, ticket text, or error log")
    parser.add_argument("--repo-path", required=True, help="Path to the repository under repair")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Self-correction cycles before giving up (default: %(default)s)",
    )
    parser.add_argument(
        "--test-path",
        default="tests/",
        help="Test target passed to pytest inside the sandbox (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts",
        help="Where to write the diff and report (default: %(default)s)",
    )
    parser.add_argument(
        "--thread-id",
        default="volatix",
        help="Checkpoint thread id, for resuming a run (default: %(default)s)",
    )
    return parser


def run(args: argparse.Namespace, console: Optional[Console] = None) -> int:
    """Execute one repair run. Returns a process exit code."""
    console = console or Console()

    try:
        workspace = Workspace(args.repo_path)
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    backend = ToolBackend(workspace)
    graph = build_graph(
        workspace,
        client=ClaudeClient(),
        runner=SandboxRunner(),
        backend=backend,
        test_path=args.test_path,
    )
    state = build_initial_state(args.issue, str(workspace.root), max_retries=args.max_retries)
    config = {"configurable": {"thread_id": args.thread_id}}

    console.print(
        Panel(
            Text(args.issue, style="italic"),
            title="[bold]Volatix-Agent[/bold]",
            subtitle=str(workspace.root),
        )
    )

    try:
        for event in iter_run_events(graph, state, config):
            console.print(f"  [bold cyan]{event.label:<11}[/bold cyan] {event.detail}")
    except (LLMError, SandboxError) as exc:
        console.print(f"\n[red]Run aborted: {exc}[/red]")
        return 1

    final = graph.get_state(config).values
    console.print()
    console.print(_summary_table(final))

    artifacts = build_artifacts(final, backend.original_files)
    written = artifacts.write(args.output_dir)
    console.print()
    width = max(len(name) for name in written)
    for name, path in written.items():
        console.print(f"  [bold]{name:<{width}}[/bold]  {path}")

    return 0 if final.get("test_passed") else 1


def main() -> None:
    sys.exit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
