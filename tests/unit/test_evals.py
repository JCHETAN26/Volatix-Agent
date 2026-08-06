"""Benchmark harness: baseline condition, metric aggregation, and the sweep loop."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from agent.llm import LLMError
from evals.baseline import read_sources, render_prompt, run_baseline
from evals.metrics import (
    CONDITION_AGENT,
    CONDITION_BASELINE,
    ScenarioResult,
    build_report,
    by_category,
    summarise,
)
from evals.run_evals import MeasuringRunner, build_parser, run, select_scenarios
from sandbox.runner import ExecutionResult
from tests.unit.conftest import FakeClaudeClient, message, text_block


@pytest.fixture
def console():
    return Console(file=open("/dev/null", "w"), force_terminal=False)


@pytest.fixture
def sample_repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    assert True\n")
    return tmp_path


# --- baseline condition -----------------------------------------------------------


def test_read_sources_excludes_tests_and_conftest(sample_repo):
    sources = read_sources(sample_repo)
    assert set(sources) == {"calc.py"}


def test_prompt_carries_full_file_contents(sample_repo):
    """Condition A must see whole files -- no AST trimming is the point of the baseline."""
    prompt = render_prompt("add is wrong", read_sources(sample_repo))
    assert "return a - b" in prompt
    assert 'path="calc.py"' in prompt


def test_baseline_writes_the_returned_file(sample_repo):
    client = FakeClaudeClient(
        [
            message(
                [
                    text_block(
                        json.dumps(
                            {"path": "calc.py", "content": "def add(a, b):\n    return a + b\n"}
                        )
                    )
                ]
            )
        ]
    )

    written = run_baseline("add is wrong", sample_repo, client)

    assert written == "calc.py"
    assert "a + b" in (sample_repo / "calc.py").read_text()


def test_baseline_uses_no_tools(sample_repo):
    """Single-pass means no tool loop -- otherwise it is not a baseline."""
    client = FakeClaudeClient(
        [message([text_block(json.dumps({"path": "calc.py", "content": "x = 1\n"}))])]
    )
    run_baseline("bug", sample_repo, client)

    assert client.requests[0]["tools"] is None


def test_baseline_makes_exactly_one_model_call(sample_repo):
    client = FakeClaudeClient(
        [message([text_block(json.dumps({"path": "calc.py", "content": "x = 1\n"}))])]
    )
    run_baseline("bug", sample_repo, client)

    assert len(client.requests) == 1
    assert client.exhausted


def test_baseline_rejects_a_path_outside_the_repository(sample_repo):
    """The baseline has no Workspace, so it needs its own confinement check."""
    client = FakeClaudeClient(
        [message([text_block(json.dumps({"path": "../escaped.py", "content": "pwned"}))])]
    )
    with pytest.raises(LLMError, match="outside the repository"):
        run_baseline("bug", sample_repo, client)

    assert not (sample_repo.parent / "escaped.py").exists()


def test_baseline_rejects_unparseable_json(sample_repo):
    client = FakeClaudeClient([message([text_block("not json at all")])])
    with pytest.raises(LLMError, match="unparseable JSON"):
        run_baseline("bug", sample_repo, client)


# --- metrics ----------------------------------------------------------------------


def results_fixture():
    return [
        ScenarioResult(
            "a",
            "logic",
            CONDITION_AGENT,
            True,
            retries=1,
            tokens={"total_tokens": 100, "calls": 4, "billable_input_tokens": 60},
        ),
        ScenarioResult(
            "b",
            "logic",
            CONDITION_AGENT,
            False,
            retries=3,
            tokens={"total_tokens": 300, "calls": 8, "billable_input_tokens": 200},
        ),
        ScenarioResult(
            "a",
            "logic",
            CONDITION_BASELINE,
            False,
            tokens={"total_tokens": 50, "calls": 1, "billable_input_tokens": 40},
        ),
        ScenarioResult(
            "b",
            "logic",
            CONDITION_BASELINE,
            True,
            tokens={"total_tokens": 60, "calls": 1, "billable_input_tokens": 45},
        ),
    ]


def test_pass_rate():
    assert summarise(results_fixture(), CONDITION_AGENT)["pass_rate"] == 50.0


def test_retries_are_averaged_over_successes_only():
    """A failed run always sits at the ceiling and would skew convergence speed."""
    summary = summarise(results_fixture(), CONDITION_AGENT)
    assert summary["avg_retries_on_success"] == 1.0


def test_token_metrics_are_averaged():
    summary = summarise(results_fixture(), CONDITION_AGENT)
    assert summary["avg_total_tokens"] == 200.0
    assert summary["avg_billable_input_tokens"] == 130.0
    assert summary["avg_model_calls"] == 6.0


def test_sandbox_latency_is_per_run_not_per_scenario():
    results = [
        ScenarioResult("a", "logic", CONDITION_AGENT, True, sandbox_ms=900.0, sandbox_runs=3)
    ]
    assert summarise(results, CONDITION_AGENT)["avg_sandbox_ms_per_run"] == 300.0


def test_summary_of_an_empty_condition():
    assert summarise([], CONDITION_AGENT)["scenarios"] == 0


def test_errors_are_counted():
    results = [ScenarioResult("a", "logic", CONDITION_AGENT, False, error="boom")]
    assert summarise(results, CONDITION_AGENT)["errors"] == 1


def test_by_category_breakdown():
    breakdown = by_category(results_fixture(), CONDITION_AGENT)
    assert breakdown["logic"] == {"scenarios": 2, "passed": 1, "pass_rate": 50.0}


def test_report_contains_both_conditions_and_raw_rows():
    report = build_report(results_fixture())
    assert set(report["conditions"]) == {CONDITION_BASELINE, CONDITION_AGENT}
    assert len(report["results"]) == 4


# --- measuring runner -------------------------------------------------------------


def test_measuring_runner_accumulates_latency():
    inner = MagicMock()
    inner.run_tests.side_effect = [
        ExecutionResult(0, "", "", 100.0, False),
        ExecutionResult(1, "", "", 200.0, False),
    ]
    runner = MeasuringRunner(inner)

    runner.run_tests("/repo")
    runner.run_tests("/repo")

    assert runner.runs == 2
    assert runner.total_ms == 300.0


def test_measuring_runner_exposes_timeout():
    inner = MagicMock()
    inner.timeout = 45
    assert MeasuringRunner(inner).timeout == 45


# --- sweep ------------------------------------------------------------------------


def test_scenario_filtering_by_id():
    args = build_parser().parse_args(["--scenario", "logic_001"])
    assert [s.id for s in select_scenarios(args)] == ["logic_001"]


def test_scenario_filtering_by_category():
    args = build_parser().parse_args(["--category", "syntax"])
    assert all(s.category == "syntax" for s in select_scenarios(args))


def test_no_matching_scenarios_exits_with_a_config_error(console, tmp_path):
    args = build_parser().parse_args(
        ["--scenario", "nope", "--output-file", str(tmp_path / "r.json")]
    )
    assert run(args, console=console) == 2


def test_sweep_writes_a_results_file(console, tmp_path):
    output = tmp_path / "results.json"
    args = build_parser().parse_args(
        ["--scenario", "logic_001", "--condition", "agent", "--output-file", str(output)]
    )
    fake = ScenarioResult("logic_001", "logic", CONDITION_AGENT, True, retries=0)

    with patch("evals.run_evals.run_agent_scenario", return_value=fake):
        assert run(args, console=console) == 0

    report = json.loads(output.read_text())
    assert report["conditions"][CONDITION_AGENT]["pass_rate"] == 100.0


def test_sweep_records_a_failing_scenario_and_keeps_going(console, tmp_path):
    """A benchmark that aborts on scenario 3 of 10 tells you nothing."""
    output = tmp_path / "results.json"
    args = build_parser().parse_args(
        ["--category", "syntax", "--condition", "agent", "--output-file", str(output)]
    )
    outcomes = [
        ScenarioResult("syntax_001", "syntax", CONDITION_AGENT, False, error="model declined"),
        ScenarioResult("syntax_002", "syntax", CONDITION_AGENT, True),
    ]

    with patch("evals.run_evals.run_agent_scenario", side_effect=outcomes):
        assert run(args, console=console) == 0

    report = json.loads(output.read_text())
    assert report["conditions"][CONDITION_AGENT]["scenarios"] == 2
    assert report["conditions"][CONDITION_AGENT]["errors"] == 1


def test_sweep_creates_the_output_directory(console, tmp_path):
    output = tmp_path / "nested" / "deep" / "results.json"
    args = build_parser().parse_args(
        ["--scenario", "logic_001", "--condition", "agent", "--output-file", str(output)]
    )
    fake = ScenarioResult("logic_001", "logic", CONDITION_AGENT, True)

    with patch("evals.run_evals.run_agent_scenario", return_value=fake):
        run(args, console=console)

    assert Path(output).exists()
