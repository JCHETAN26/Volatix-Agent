"""CLI argument parsing, event description, and the run flow.

The graph is faked, so these exercise the console path without an API key or Docker.
"""

from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from agent.artifacts import DIFF_FILENAME, REPORT_FILENAME
from agent.llm import LLMError
from cli.main import build_parser, describe_event, iter_run_events, run


@pytest.fixture
def console():
    # force_terminal off keeps output plain so assertions match.
    return Console(file=open("/dev/null", "w"), force_terminal=False)


# --- argument parsing -------------------------------------------------------------


def test_issue_and_repo_path_are_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_defaults():
    args = build_parser().parse_args(["--issue", "bug", "--repo-path", "/repo"])
    assert args.max_retries == 3
    assert args.test_path == "tests/"
    assert args.output_dir == "artifacts"


def test_overrides():
    args = build_parser().parse_args(
        [
            "--issue",
            "bug",
            "--repo-path",
            "/repo",
            "--max-retries",
            "7",
            "--test-path",
            "tests/unit/",
            "--output-dir",
            "out",
        ]
    )
    assert args.max_retries == 7
    assert args.test_path == "tests/unit/"
    assert args.output_dir == "out"


# --- event description ------------------------------------------------------------


def test_planner_event_reports_step_count():
    event = describe_event("planner", {"plan": ["a", "b", "c"]})
    assert event.label == "Planning"
    assert "3-step plan" in event.detail


def test_executor_event_lists_modified_files():
    event = describe_event("executor", {"modified_files": {"calc.py": "x"}})
    assert event.label == "Executing"
    assert "calc.py" in event.detail


def test_executor_event_with_no_changes():
    assert "none" in describe_event("executor", {"modified_files": {}}).detail


def test_validator_event_reflects_the_verdict():
    assert describe_event("validator", {"test_passed": True}).detail == "Tests passed"
    assert describe_event("validator", {"test_passed": False}).detail == "Tests failed"


def test_retry_event_reports_the_count():
    assert "Retry 2" in describe_event("retry", {"retry_count": 2}).detail


def test_unknown_node_is_still_renderable():
    assert describe_event("mystery", {}).label == "Mystery"


def test_iter_run_events_flattens_the_stream():
    graph = MagicMock()
    graph.stream.return_value = [
        {"planner": {"plan": ["a"]}},
        {"executor": {"modified_files": {"calc.py": "x"}}},
    ]

    events = list(iter_run_events(graph, {}, {}))

    assert [e.node for e in events] == ["planner", "executor"]
    assert graph.stream.call_args.kwargs["stream_mode"] == "updates"


# --- run flow ---------------------------------------------------------------------


def make_args(tmp_path, repo, **overrides):
    args = build_parser().parse_args(
        [
            "--issue",
            "add() is wrong",
            "--repo-path",
            str(repo),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def fake_graph(final_values):
    graph = MagicMock()
    graph.stream.return_value = [
        {"planner": {"plan": ["fix it"]}},
        {"executor": {"modified_files": {"calc.py": "def add(a, b):\n    return a + b\n"}}},
        {"validator": {"test_passed": final_values.get("test_passed", True)}},
    ]
    graph.get_state.return_value = MagicMock(values=final_values)
    return graph


@pytest.fixture
def patched(monkeypatch):
    """Neutralise the real client and sandbox; the graph itself is injected per-test."""
    monkeypatch.setattr("cli.main.ClaudeClient", MagicMock())
    monkeypatch.setattr("cli.main.SandboxRunner", MagicMock())


def test_successful_run_returns_zero_and_writes_artifacts(tmp_path, repo, console, patched):
    final = {
        "test_passed": True,
        "retry_count": 0,
        "max_retries": 3,
        "modified_files": {"calc.py": "def add(a, b):\n    return a + b\n"},
        "plan": ["fix it"],
        "analysis": "wrong operator",
        "target_files": ["calc.py"],
        "test_output": "1 passed",
        "issue_description": "add() is wrong",
        "token_usage": {"total_tokens": 100, "calls": 2},
    }
    args = make_args(tmp_path, repo)

    with patch("cli.main.build_graph", return_value=fake_graph(final)):
        code = run(args, console=console)

    assert code == 0
    assert (tmp_path / "out" / DIFF_FILENAME).exists()
    assert (tmp_path / "out" / REPORT_FILENAME).exists()


def test_failed_run_returns_one_but_still_writes_artifacts(tmp_path, repo, console, patched):
    """A failed run is still worth a report -- it explains what was tried."""
    final = {
        "test_passed": False,
        "retry_count": 3,
        "max_retries": 3,
        "modified_files": {"calc.py": "x = 1\n"},
        "plan": ["fix it"],
        "analysis": "",
        "target_files": [],
        "test_output": "1 failed",
        "issue_description": "add() is wrong",
        "token_usage": {},
    }
    args = make_args(tmp_path, repo)

    with patch("cli.main.build_graph", return_value=fake_graph(final)):
        code = run(args, console=console)

    assert code == 1
    assert (tmp_path / "out" / REPORT_FILENAME).exists()


def test_missing_repository_exits_with_a_config_error(tmp_path, console, patched):
    args = make_args(tmp_path, tmp_path / "does-not-exist")
    assert run(args, console=console) == 2


def test_model_error_aborts_without_traceback(tmp_path, repo, console, patched):
    graph = MagicMock()
    graph.stream.side_effect = LLMError("model declined")

    with patch("cli.main.build_graph", return_value=graph):
        assert run(make_args(tmp_path, repo), console=console) == 1
