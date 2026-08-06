"""Validator node -- Phase 4.

Runs the repository's test suite inside the Docker sandbox and records the verdict. This
is the only node that decides whether a fix worked; the Executor is deliberately not
given the test-running tool so it cannot mark its own homework.

A sandbox failure (missing image, dead daemon) is recorded as a failed validation rather
than raised, so the run degrades into the retry loop instead of crashing the graph.
"""

from agent.state import AgentState
from sandbox.runner import SandboxError, SandboxRunner

DEFAULT_TEST_PATH = "tests/"


def validator_node(
    state: AgentState,
    runner: SandboxRunner,
    test_path: str = DEFAULT_TEST_PATH,
) -> dict:
    """Execute the test suite and record pass/fail plus the raw output."""
    try:
        result = runner.run_tests(state["codebase_path"], test_path)
    except SandboxError as exc:
        return {
            "test_passed": False,
            "test_output": f"Sandbox could not run the tests: {exc}",
        }

    output = result.combined_output
    if result.timed_out:
        output = (
            f"Test run exceeded the {runner.timeout}s sandbox timeout and was killed. "
            "This usually means the change introduced an infinite loop.\n\n" + output
        )

    return {"test_passed": result.passed, "test_output": output}
