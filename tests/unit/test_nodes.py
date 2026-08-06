"""Planner, Executor, and Validator node behaviour, driven by a scripted fake client."""

from unittest.mock import MagicMock

import pytest

from agent.llm import LLMError
from agent.nodes.executor import MAX_TOOL_ITERATIONS, executor_node
from agent.nodes.planner import build_repository_outline, planner_node
from agent.nodes.validator import validator_node
from agent.state import build_initial_state
from agent.tools import ToolBackend
from sandbox.runner import ExecutionResult, SandboxError
from tests.unit.conftest import (
    FakeClaudeClient,
    message,
    plan_message,
    text_block,
    tool_block,
)


@pytest.fixture
def state(repo):
    return build_initial_state("add() returns the wrong value", str(repo))


# --- planner ---------------------------------------------------------------------


def test_planner_returns_steps_analysis_and_targets(state, workspace):
    client = FakeClaudeClient([plan_message()])

    result = planner_node(state, client=client, workspace=workspace)

    assert result["plan"] == ["Fix the operator in calc.add"]
    assert result["analysis"] == "off-by-one in add()"
    assert result["target_files"] == ["calc.py"]


def test_planner_requests_structured_output(state, workspace):
    client = FakeClaudeClient([plan_message()])
    planner_node(state, client=client, workspace=workspace)

    schema = client.requests[0]["output_schema"]
    assert schema["required"] == ["analysis", "target_files", "steps"]
    assert schema["additionalProperties"] is False


def test_planner_sees_the_outline_not_the_file_bodies(state, workspace):
    client = FakeClaudeClient([plan_message()])
    planner_node(state, client=client, workspace=workspace)

    prompt = client.requests[0]["messages"][0]["content"]
    assert "def add(a, b):" in prompt
    assert "return a - b" not in prompt


def test_planner_rejects_an_empty_plan(state, workspace):
    client = FakeClaudeClient([plan_message(steps=[])])
    with pytest.raises(LLMError, match="no steps"):
        planner_node(state, client=client, workspace=workspace)


def test_outline_reports_a_syntax_error_instead_of_failing(workspace):
    (workspace.root / "broken.py").write_text("def oops(\n")
    assert "SyntaxError" in build_repository_outline(workspace)


def test_outline_handles_a_repository_with_no_python(tmp_path):
    from mcp_server.workspace import Workspace

    assert "no Python files" in build_repository_outline(Workspace(str(tmp_path)))


# --- executor --------------------------------------------------------------------


def test_executor_applies_an_edit_and_records_it(state, workspace):
    state["plan"] = ["Fix the operator"]
    backend = ToolBackend(workspace)
    client = FakeClaudeClient(
        [
            message(
                [
                    tool_block(
                        "write_file_patch",
                        {"path": "calc.py", "content": "def add(a,b):\n    return a + b\n"},
                    )
                ]
            ),
            message([text_block("Changed the operator to +.")]),
        ]
    )

    result = executor_node(state, client=client, backend=backend)

    assert "calc.py" in result["modified_files"]
    assert "a + b" in (workspace.root / "calc.py").read_text()


def test_executor_reports_token_usage(state, workspace):
    state["plan"] = ["Fix it"]
    client = FakeClaudeClient(
        [
            message([tool_block("write_file_patch", {"path": "n.py", "content": "x = 1\n"})]),
            message([text_block("done")]),
        ]
    )

    result = executor_node(state, client=client, backend=ToolBackend(workspace))

    assert result["token_usage"]["calls"] == 2
    assert result["token_usage"]["total_tokens"] > 0


def test_executor_feeds_tool_errors_back_instead_of_crashing(state, workspace):
    """A rejected edit must become a message the model can recover from."""
    state["plan"] = ["Fix it"]
    client = FakeClaudeClient(
        [
            message(
                [
                    tool_block(
                        "write_file_patch",
                        {"path": "calc.py", "old_string": "nope", "new_string": "x"},
                    )
                ]
            ),
            message([tool_block("write_file_patch", {"path": "calc.py", "content": "x = 1\n"})]),
            message([text_block("recovered")]),
        ]
    )

    result = executor_node(state, client=client, backend=ToolBackend(workspace))

    error_turn = client.requests[1]["messages"][-1]["content"][0]
    assert error_turn["is_error"] is True
    assert "not found" in error_turn["content"]
    assert "calc.py" in result["modified_files"]


def test_executor_returns_all_results_in_one_user_message(state, workspace):
    """Splitting parallel results across messages suppresses future parallel calls."""
    state["plan"] = ["Read two files"]
    client = FakeClaudeClient(
        [
            message(
                [
                    tool_block("read_file_content", {"path": "calc.py"}, block_id="a"),
                    tool_block("get_ast_symbols", {"path": "calc.py"}, block_id="b"),
                ]
            ),
            message([tool_block("write_file_patch", {"path": "n.py", "content": "x = 1\n"})]),
            message([text_block("done")]),
        ]
    )

    executor_node(state, client=client, backend=ToolBackend(workspace))

    results = client.requests[1]["messages"][-1]["content"]
    assert len(results) == 2
    assert {r["tool_use_id"] for r in results} == {"a", "b"}


def test_executor_injects_the_failure_on_a_retry(state, workspace):
    state["plan"] = ["Fix it"]
    state["error_summary"] = "AssertionError: assert -1 == 5"
    client = FakeClaudeClient(
        [
            message([tool_block("write_file_patch", {"path": "n.py", "content": "x = 1\n"})]),
            message([text_block("done")]),
        ]
    )

    executor_node(state, client=client, backend=ToolBackend(workspace))

    prompt = client.requests[0]["messages"][0]["content"]
    assert "did not pass the test suite" in prompt
    assert "assert -1 == 5" in prompt


def test_executor_first_pass_has_no_retry_preamble(state, workspace):
    state["plan"] = ["Fix it"]
    client = FakeClaudeClient(
        [
            message([tool_block("write_file_patch", {"path": "n.py", "content": "x = 1\n"})]),
            message([text_block("done")]),
        ]
    )

    executor_node(state, client=client, backend=ToolBackend(workspace))

    assert "did not pass" not in client.requests[0]["messages"][0]["content"]


def test_executor_is_not_given_the_test_runner(state, workspace):
    """The Executor must not be able to mark its own homework."""
    state["plan"] = ["Fix it"]
    client = FakeClaudeClient(
        [
            message([tool_block("write_file_patch", {"path": "n.py", "content": "x = 1\n"})]),
            message([text_block("done")]),
        ]
    )

    executor_node(state, client=client, backend=ToolBackend(workspace))

    assert "run_test_suite" not in {t["name"] for t in client.requests[0]["tools"]}


def test_executor_raises_when_it_edits_nothing(state, workspace):
    state["plan"] = ["Fix it"]
    client = FakeClaudeClient([message([text_block("Looks fine to me.")])])

    with pytest.raises(LLMError, match="made no edits"):
        executor_node(state, client=client, backend=ToolBackend(workspace))


def test_executor_stops_at_the_iteration_ceiling(state, workspace):
    state["plan"] = ["Loop forever"]
    looping = [
        message([tool_block("read_file_content", {"path": "calc.py"})])
        for _ in range(MAX_TOOL_ITERATIONS + 1)
    ]

    with pytest.raises(LLMError, match="exceeded"):
        executor_node(state, client=FakeClaudeClient(looping), backend=ToolBackend(workspace))


# --- validator -------------------------------------------------------------------


def test_validator_records_a_pass(state):
    runner = MagicMock()
    runner.run_tests.return_value = ExecutionResult(0, "1 passed", "", 10.0, False)

    result = validator_node(state, runner=runner)

    assert result["test_passed"] is True
    assert "1 passed" in result["test_output"]


def test_validator_records_a_failure(state):
    runner = MagicMock()
    runner.run_tests.return_value = ExecutionResult(1, "1 failed", "AssertionError", 10.0, False)

    result = validator_node(state, runner=runner)

    assert result["test_passed"] is False
    assert "AssertionError" in result["test_output"]


def test_validator_explains_a_timeout(state):
    runner = MagicMock()
    runner.timeout = 30
    runner.run_tests.return_value = ExecutionResult(124, "", "", 30_000.0, True)

    result = validator_node(state, runner=runner)

    assert result["test_passed"] is False
    assert "infinite loop" in result["test_output"]


def test_validator_degrades_a_sandbox_error_into_a_failure(state):
    """A dead daemon must not crash the graph mid-run."""
    runner = MagicMock()
    runner.run_tests.side_effect = SandboxError("Docker daemon unreachable")

    result = validator_node(state, runner=runner)

    assert result["test_passed"] is False
    assert "Docker daemon unreachable" in result["test_output"]


def test_validator_runs_against_the_codebase_path(state):
    runner = MagicMock()
    runner.run_tests.return_value = ExecutionResult(0, "ok", "", 1.0, False)

    validator_node(state, runner=runner, test_path="tests/unit/")

    runner.run_tests.assert_called_once_with(state["codebase_path"], "tests/unit/")
