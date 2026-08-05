"""Real container round-trip for SandboxRunner -- requires a live Docker daemon.

Run with: pytest tests/integration/ -m integration
CI builds volatix-sandbox:latest before invoking these.
"""

import textwrap

import pytest

from sandbox.runner import EXIT_CODE_TIMEOUT, SandboxError, SandboxRunner

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def runner():
    r = SandboxRunner(timeout=20)
    try:
        r.ensure_image()
    except SandboxError as exc:
        pytest.skip(str(exc))
    return r


@pytest.fixture
def project(tmp_path):
    """A tiny project whose test suite passes."""
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    # Without a rootdir conftest, pytest puts tests/ on sys.path but not the project
    # root, so `from calc import add` would not resolve inside the container.
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(textwrap.dedent("""
            from calc import add

            def test_add():
                assert add(2, 3) == 5
            """))
    return tmp_path


def test_exit_code_and_stdout_are_captured(runner, project):
    result = runner.run(str(project), ["python", "-c", "print('hello sandbox')"])

    assert result.passed
    assert result.exit_code == 0
    assert "hello sandbox" in result.stdout
    assert result.duration_ms > 0


def test_streams_are_separated(runner, project):
    result = runner.run(
        str(project),
        ["python", "-c", "import sys; print('OUT'); print('ERR', file=sys.stderr)"],
    )

    assert "OUT" in result.stdout and "ERR" not in result.stdout
    assert "ERR" in result.stderr and "OUT" not in result.stderr


def test_nonzero_exit_code_propagates(runner, project):
    result = runner.run(str(project), ["python", "-c", "raise SystemExit(3)"])

    assert result.exit_code == 3
    assert result.passed is False


def test_passing_suite_in_mounted_workspace(runner, project):
    result = runner.run_tests(str(project))

    assert result.passed, result.combined_output
    assert "1 passed" in result.combined_output


def test_failing_suite_surfaces_the_assertion(runner, project):
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n")

    result = runner.run_tests(str(project))

    assert result.passed is False
    assert "1 failed" in result.combined_output


def test_workspace_edits_are_visible_to_the_container(runner, project):
    """The Executor writes on the host; the Validator must see those edits."""
    (project / "tests" / "test_calc.py").write_text(
        "def test_marker():\n    assert True  # rewritten on host\n"
    )

    result = runner.run(str(project), ["cat", "tests/test_calc.py"])

    assert "rewritten on host" in result.stdout


def test_container_can_write_into_the_mount(runner, project):
    """pytest needs to write __pycache__ and .pytest_cache; UID mapping must allow it."""
    result = runner.run(str(project), ["python", "-c", "open('written.txt','w').write('ok')"])

    assert result.passed, result.combined_output
    assert (project / "written.txt").read_text() == "ok"


def test_timeout_kills_a_hanging_command(runner, project):
    fast = SandboxRunner(timeout=3)

    result = fast.run(str(project), ["sleep", "120"])

    assert result.timed_out is True
    assert result.exit_code == EXIT_CODE_TIMEOUT
    assert result.passed is False
    assert result.duration_ms < 60_000


def test_network_is_disabled_by_default(runner, project):
    """Untrusted code must not be able to exfiltrate or phone home."""
    result = runner.run(
        str(project),
        ["python", "-c", "import socket; socket.create_connection(('1.1.1.1', 80), timeout=5)"],
    )

    assert result.passed is False


def test_containers_are_not_leaked(runner, project):
    before = len(runner.client.containers.list(all=True))

    runner.run(str(project), ["true"])
    runner.run(str(project), ["false"])

    assert len(runner.client.containers.list(all=True)) == before


def test_missing_workspace_is_rejected(runner, tmp_path):
    with pytest.raises(SandboxError, match="not a directory"):
        runner.run(str(tmp_path / "does-not-exist"), ["true"])
