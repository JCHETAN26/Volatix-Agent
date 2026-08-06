"""MCP server wiring: the four spec tools plus discovery are registered and delegate.

Tool bodies are exercised through the registered handlers rather than the MCP transport,
so these stay fast and need no client session.
"""

from unittest.mock import MagicMock

import pytest

from mcp_server.server import build_server
from mcp_server.workspace import Workspace
from sandbox.runner import ExecutionResult

SPEC_TOOLS = {"read_file_content", "write_file_patch", "get_ast_symbols", "run_test_suite"}


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "calc.py").write_text('"""Calc."""\n\n\ndef add(a, b):\n    return a + b\n')
    return Workspace(str(tmp_path))


@pytest.fixture
def runner():
    r = MagicMock()
    r.run_tests.return_value = ExecutionResult(0, "1 passed", "", 1234.0, False)
    return r


@pytest.fixture
def server(workspace, runner):
    return build_server(workspace, runner=runner)


async def call(server, name, **kwargs):
    """Invoke a registered tool the way an MCP client would."""
    return await server.call_tool(name, kwargs)


@pytest.mark.anyio
async def test_all_spec_tools_are_registered(server):
    names = {t.name for t in await server.list_tools()}
    assert SPEC_TOOLS.issubset(names)
    assert "list_workspace_files" in names


@pytest.mark.anyio
async def test_every_tool_documents_when_to_call_it(server):
    """A description that omits the trigger condition produces wrong tool selection."""
    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert len(tool.description) > 120, f"{tool.name} description is too thin"


@pytest.mark.anyio
async def test_read_file_content_delegates(server):
    result = await call(server, "read_file_content", path="calc.py")
    assert "def add(a, b):" in str(result)


@pytest.mark.anyio
async def test_read_file_content_range(server):
    result = await call(server, "read_file_content", path="calc.py", start_line=4, end_line=4)
    assert "def add(a, b):" in str(result)
    assert "return a + b" not in str(result)


@pytest.mark.anyio
async def test_get_ast_symbols_strips_bodies(server):
    result = str(await call(server, "get_ast_symbols", path="calc.py"))
    assert "def add(a, b):" in result
    assert "return a + b" not in result


@pytest.mark.anyio
async def test_list_workspace_files(server):
    assert "calc.py" in str(await call(server, "list_workspace_files"))


@pytest.mark.anyio
async def test_list_workspace_files_reports_no_matches(server):
    assert "No files match" in str(await call(server, "list_workspace_files", pattern="**/*.rs"))


@pytest.mark.anyio
async def test_write_file_patch_targeted_edit(server, workspace):
    await call(server, "write_file_patch", path="calc.py", old_string="a + b", new_string="a - b")
    assert "a - b" in (workspace.root / "calc.py").read_text()


@pytest.mark.anyio
async def test_write_file_patch_full_rewrite(server, workspace):
    await call(server, "write_file_patch", path="new.py", content="x = 1\n")
    assert (workspace.root / "new.py").read_text() == "x = 1\n"


@pytest.mark.anyio
async def test_write_file_patch_rejects_both_modes(server):
    with pytest.raises(Exception, match="not both"):
        await call(
            server,
            "write_file_patch",
            path="calc.py",
            content="x = 1",
            old_string="a",
            new_string="b",
        )


@pytest.mark.anyio
async def test_write_file_patch_rejects_half_an_edit(server):
    with pytest.raises(Exception, match="both old_string and new_string"):
        await call(server, "write_file_patch", path="calc.py", old_string="a + b")


@pytest.mark.anyio
async def test_write_file_patch_rejects_empty_call(server):
    with pytest.raises(Exception, match="Provide content"):
        await call(server, "write_file_patch", path="calc.py")


@pytest.mark.anyio
async def test_run_test_suite_reports_pass(server, runner, workspace):
    result = str(await call(server, "run_test_suite"))

    runner.run_tests.assert_called_once_with(str(workspace.root), "tests/")
    assert "PASSED" in result
    assert "1 passed" in result


@pytest.mark.anyio
async def test_run_test_suite_reports_failure_with_output(server, runner):
    runner.run_tests.return_value = ExecutionResult(1, "1 failed", "AssertionError", 90.0, False)

    result = str(await call(server, "run_test_suite"))

    assert "FAILED" in result
    assert "AssertionError" in result


@pytest.mark.anyio
async def test_run_test_suite_flags_a_timeout(server, runner):
    runner.run_tests.return_value = ExecutionResult(124, "", "", 30_000.0, True)
    assert "TIMED OUT" in str(await call(server, "run_test_suite"))


@pytest.mark.anyio
async def test_run_test_suite_forwards_the_test_path(server, runner, workspace):
    await call(server, "run_test_suite", test_path="tests/test_calc.py::test_add")
    runner.run_tests.assert_called_once_with(str(workspace.root), "tests/test_calc.py::test_add")


def test_backend_captures_the_pre_edit_baseline(workspace):
    """Phase 6's diff depends on this; without it every patch looks like a new file."""
    from agent.tools import ToolBackend

    backend = ToolBackend(workspace)
    original = (workspace.root / "calc.py").read_text()

    backend.dispatch("write_file_patch", {"path": "calc.py", "content": "x = 1\n"})

    assert backend.original_files["calc.py"] == original


def test_backend_records_none_for_a_created_file(workspace):
    from agent.tools import ToolBackend

    backend = ToolBackend(workspace)
    backend.dispatch("write_file_patch", {"path": "brand_new.py", "content": "x = 1\n"})

    assert backend.original_files["brand_new.py"] is None


def test_backend_keeps_the_first_baseline_across_repeated_edits(workspace):
    """A second edit must not overwrite the baseline with the first edit's result."""
    from agent.tools import ToolBackend

    backend = ToolBackend(workspace)
    original = (workspace.root / "calc.py").read_text()

    backend.dispatch("write_file_patch", {"path": "calc.py", "content": "first\n"})
    backend.dispatch("write_file_patch", {"path": "calc.py", "content": "second\n"})

    assert backend.original_files["calc.py"] == original


@pytest.mark.anyio
async def test_path_traversal_is_refused_through_the_tool_layer(server):
    """Confinement must hold at the MCP boundary, not just in Workspace."""
    with pytest.raises(Exception, match="escapes the workspace root"):
        await call(server, "read_file_content", path="../../etc/passwd")
