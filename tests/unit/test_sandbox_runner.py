"""SandboxRunner behaviour, verified against a mocked Docker client.

These run anywhere -- no daemon required. The real container round-trip is covered by
tests/integration/test_sandbox_smoke.py.
"""

from unittest.mock import MagicMock

import pytest
import requests
from docker.errors import APIError, ImageNotFound

from sandbox.runner import (
    EXIT_CODE_TIMEOUT,
    WORKSPACE_MOUNT,
    ExecutionResult,
    SandboxError,
    SandboxRunner,
)


def make_container(exit_code=0, stdout=b"", stderr=b""):
    container = MagicMock()
    container.wait.return_value = {"StatusCode": exit_code}
    out, err = stdout, stderr
    container.logs.side_effect = lambda **kw: out if kw.get("stdout") else err
    return container


def make_client(container=None):
    client = MagicMock()
    client.containers.create.return_value = container or make_container()
    return client


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "tests").mkdir()
    return str(tmp_path)


def test_successful_run_reports_pass(workspace):
    container = make_container(exit_code=0, stdout=b"2 passed\n")
    runner = SandboxRunner(client=make_client(container))

    result = runner.run(workspace, ["pytest", "-q"])

    assert isinstance(result, ExecutionResult)
    assert result.passed
    assert result.exit_code == 0
    assert result.stdout == "2 passed\n"
    assert result.timed_out is False
    assert result.duration_ms >= 0


def test_nonzero_exit_reports_failure(workspace):
    container = make_container(exit_code=1, stdout=b"1 failed\n", stderr=b"AssertionError\n")
    runner = SandboxRunner(client=make_client(container))

    result = runner.run(workspace, ["pytest", "-q"])

    assert result.passed is False
    assert result.exit_code == 1
    assert result.combined_output == "1 failed\n\nAssertionError\n"


def test_container_is_hardened_and_mounted(workspace):
    container = make_container()
    client = make_client(container)
    runner = SandboxRunner(client=client, mem_limit="256m", pids_limit=64)

    runner.run(workspace, ["pytest"])

    kwargs = client.containers.create.call_args.kwargs
    assert kwargs["network_disabled"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["mem_limit"] == "256m"
    assert kwargs["pids_limit"] == 64
    assert kwargs["working_dir"] == WORKSPACE_MOUNT
    assert kwargs["volumes"][workspace] == {"bind": WORKSPACE_MOUNT, "mode": "rw"}


def test_timeout_kills_container_and_flags_result(workspace):
    container = make_container()
    container.wait.side_effect = requests.exceptions.ReadTimeout("timed out")
    runner = SandboxRunner(client=make_client(container), timeout=1)

    result = runner.run(workspace, ["sleep", "600"])

    assert result.timed_out is True
    assert result.exit_code == EXIT_CODE_TIMEOUT
    assert result.passed is False
    container.kill.assert_called_once()


def test_connection_error_is_also_treated_as_timeout(workspace):
    """docker-py raises ConnectionError rather than ReadTimeout on some transports."""
    container = make_container()
    container.wait.side_effect = requests.exceptions.ConnectionError("read timeout")
    runner = SandboxRunner(client=make_client(container), timeout=1)

    assert runner.run(workspace, ["sleep", "600"]).timed_out is True


def test_container_is_removed_after_success(workspace):
    container = make_container()
    runner = SandboxRunner(client=make_client(container))

    runner.run(workspace, ["true"])

    container.remove.assert_called_once_with(force=True)


def test_container_is_removed_after_timeout(workspace):
    container = make_container()
    container.wait.side_effect = requests.exceptions.ReadTimeout("timed out")
    runner = SandboxRunner(client=make_client(container), timeout=1)

    runner.run(workspace, ["sleep", "600"])

    container.remove.assert_called_once_with(force=True)


def test_container_is_removed_when_start_explodes(workspace):
    """A leaked container is a resource leak on every retry cycle -- never allow it."""
    container = make_container()
    container.start.side_effect = APIError("boom")
    runner = SandboxRunner(client=make_client(container))

    with pytest.raises(APIError):
        runner.run(workspace, ["true"])

    container.remove.assert_called_once_with(force=True)


def test_removal_failure_does_not_mask_the_result(workspace):
    container = make_container(exit_code=0)
    container.remove.side_effect = APIError("already gone")
    runner = SandboxRunner(client=make_client(container))

    assert runner.run(workspace, ["true"]).passed


def test_missing_workspace_raises_before_touching_docker(tmp_path):
    client = make_client()
    runner = SandboxRunner(client=client)

    with pytest.raises(SandboxError, match="not a directory"):
        runner.run(str(tmp_path / "nope"), ["pytest"])

    client.containers.create.assert_not_called()


def test_missing_image_raises_actionable_error(workspace):
    client = make_client()
    client.images.get.side_effect = ImageNotFound("missing")
    runner = SandboxRunner(client=client)

    with pytest.raises(SandboxError, match="docker build"):
        runner.run(workspace, ["pytest"])

    client.containers.create.assert_not_called()


def test_run_tests_builds_a_pytest_invocation(workspace):
    container = make_container()
    client = make_client(container)

    SandboxRunner(client=client).run_tests(workspace, "tests/unit/")

    assert client.containers.create.call_args.kwargs["command"] == [
        "pytest",
        "tests/unit/",
        "-q",
        "--no-header",
    ]


def test_combined_output_skips_empty_streams():
    result = ExecutionResult(0, "only stdout", "", 1.0, False)
    assert result.combined_output == "only stdout"
