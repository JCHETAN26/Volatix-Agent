"""End-to-end graph traversal: happy path, retry loop, and budget exhaustion.

No API key and no Docker daemon -- the client and sandbox are both fakes, so these prove
the wiring and the routing rather than the model's judgement.
"""

from unittest.mock import MagicMock

import pytest

from agent.graph import build_graph
from agent.nodes.stack_parser import MAX_CHARS
from agent.state import build_initial_state
from agent.tools import ToolBackend
from sandbox.runner import ExecutionResult
from tests.unit.conftest import (
    FakeClaudeClient,
    message,
    plan_message,
    prompt_text,
    text_block,
    tool_block,
)

CONFIG = {"configurable": {"thread_id": "test"}}


def edit_then_finish(path="calc.py", content="def add(a, b):\n    return a + b\n"):
    """One Executor pass: a single write, then a closing summary."""
    return [
        message([tool_block("write_file_patch", {"path": path, "content": content})]),
        message([text_block("Applied the fix.")]),
    ]


def runner_returning(*results):
    runner = MagicMock()
    runner.timeout = 30
    runner.run_tests.side_effect = list(results)
    return runner


PASS = ExecutionResult(0, "1 passed", "", 12.0, False)
FAIL = ExecutionResult(1, "1 failed", "AssertionError: assert -1 == 5", 12.0, False)


def run(workspace, client, runner, max_retries=3):
    graph = build_graph(workspace, client=client, runner=runner, backend=ToolBackend(workspace))
    state = build_initial_state("add() is wrong", str(workspace.root), max_retries=max_retries)
    return graph.invoke(state, CONFIG)


def test_happy_path_reaches_a_passing_state(workspace):
    client = FakeClaudeClient([plan_message(), *edit_then_finish()])

    final = run(workspace, client, runner_returning(PASS))

    assert final["test_passed"] is True
    assert final["retry_count"] == 0
    assert "calc.py" in final["modified_files"]


def test_plan_and_analysis_are_threaded_into_state(workspace):
    client = FakeClaudeClient([plan_message(), *edit_then_finish()])

    final = run(workspace, client, runner_returning(PASS))

    assert final["plan"] == ["Fix the operator in calc.add"]
    assert final["analysis"] == "off-by-one in add()"
    assert final["target_files"] == ["calc.py"]


def test_failure_then_success_consumes_exactly_one_retry(workspace):
    client = FakeClaudeClient([plan_message(), *edit_then_finish(), *edit_then_finish()])

    final = run(workspace, client, runner_returning(FAIL, PASS))

    assert final["test_passed"] is True
    assert final["retry_count"] == 1


def test_the_failure_is_injected_into_the_second_executor_pass(workspace):
    """This is the self-correction loop: the Executor must see why it failed."""
    client = FakeClaudeClient([plan_message(), *edit_then_finish(), *edit_then_finish()])

    run(workspace, client, runner_returning(FAIL, PASS))

    retry_prompt = prompt_text(client.requests[3])
    assert "did not pass the test suite" in retry_prompt
    assert "assert -1 == 5" in retry_prompt


def test_planner_runs_once_no_matter_how_many_retries(workspace):
    """Retries re-enter the Executor, not the Planner -- re-planning would waste tokens."""
    client = FakeClaudeClient(
        [plan_message(), *edit_then_finish(), *edit_then_finish(), *edit_then_finish()]
    )

    run(workspace, client, runner_returning(FAIL, FAIL, PASS))

    planning_calls = [r for r in client.requests if r["output_schema"] is not None]
    assert len(planning_calls) == 1


def test_budget_exhaustion_stops_the_loop(workspace):
    client = FakeClaudeClient(
        [plan_message(), *edit_then_finish(), *edit_then_finish(), *edit_then_finish()]
    )

    final = run(workspace, client, runner_returning(FAIL, FAIL, FAIL), max_retries=2)

    assert final["test_passed"] is False
    assert final["retry_count"] == 2
    assert "Gave up after 2 retries" in final["error_summary"]


def test_zero_retries_gives_up_immediately(workspace):
    client = FakeClaudeClient([plan_message(), *edit_then_finish()])

    final = run(workspace, client, runner_returning(FAIL), max_retries=0)

    assert final["test_passed"] is False
    assert final["retry_count"] == 0
    assert client.exhausted


def test_validator_runs_once_per_executor_pass(workspace):
    client = FakeClaudeClient([plan_message(), *edit_then_finish(), *edit_then_finish()])
    runner = runner_returning(FAIL, PASS)

    run(workspace, client, runner)

    assert runner.run_tests.call_count == 2


def test_long_pytest_output_is_trimmed_before_being_fed_back(workspace):
    noisy = ExecutionResult(1, "x" * (MAX_CHARS * 3), "", 12.0, False)
    client = FakeClaudeClient([plan_message(), *edit_then_finish(), *edit_then_finish()])

    run(workspace, client, runner_returning(noisy, PASS))

    retry_prompt = prompt_text(client.requests[3])
    assert len(retry_prompt) < MAX_CHARS * 2


def test_retry_feeds_back_the_trimmed_failure_not_the_raw_log(workspace):
    """The Executor should see the assertion, not pytest's framework frames."""
    raw = (
        "=================================== FAILURES ===================================\n"
        "___________________________________ test_add ___________________________________\n"
        "/venv/lib/site-packages/_pytest/python.py:508: in importtestmodule\n"
        "    mod = import_path(\n"
        "E       assert -1 == 5\n"
        "\n"
        "tests/test_calc.py:5: AssertionError\n"
        "1 failed in 0.02s\n"
    )
    client = FakeClaudeClient([plan_message(), *edit_then_finish(), *edit_then_finish()])

    run(workspace, client, runner_returning(ExecutionResult(1, raw, "", 12.0, False), PASS))

    retry_prompt = prompt_text(client.requests[3])
    assert "assert -1 == 5" in retry_prompt
    assert "tests/test_calc.py:5" in retry_prompt
    assert "_pytest" not in retry_prompt
    assert "site-packages" not in retry_prompt


def test_give_up_summary_is_also_trimmed(workspace):
    raw = (
        "=== FAILURES ===\n___ test_x ___\n"
        "E       assert 1 == 2\n\nt.py:3: AssertionError\n1 failed\n"
    )
    client = FakeClaudeClient([plan_message(), *edit_then_finish()])

    final = run(
        workspace, client, runner_returning(ExecutionResult(1, raw, "", 1.0, False)), max_retries=0
    )

    assert "assert 1 == 2" in final["error_summary"]
    assert "Gave up after 0 retries" in final["error_summary"]


def test_token_usage_accumulates_across_the_whole_run(workspace):
    client = FakeClaudeClient([plan_message(), *edit_then_finish(), *edit_then_finish()])

    final = run(workspace, client, runner_returning(FAIL, PASS))

    # 1 planner call + 2 executor calls per pass, over two passes.
    assert final["token_usage"]["calls"] == 5
    assert final["token_usage"]["total_tokens"] > 0


def test_checkpointer_persists_state_under_the_thread_id(workspace):
    client = FakeClaudeClient([plan_message(), *edit_then_finish()])
    graph = build_graph(
        workspace, client=client, runner=runner_returning(PASS), backend=ToolBackend(workspace)
    )
    state = build_initial_state("add() is wrong", str(workspace.root))

    graph.invoke(state, CONFIG)

    assert graph.get_state(CONFIG).values["test_passed"] is True


@pytest.mark.parametrize("passed,expected", [(True, 0), (False, 1)])
def test_retry_count_only_increments_on_failure(workspace, passed, expected):
    client = FakeClaudeClient([plan_message(), *edit_then_finish(), *edit_then_finish()])
    first = PASS if passed else FAIL

    final = run(workspace, client, runner_returning(first, PASS))

    assert final["retry_count"] == expected
