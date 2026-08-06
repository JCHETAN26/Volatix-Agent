"""MCP server exposing the agent tool suite -- Phase 3.

Tools:
    read_file_content  -- read a source file from the workspace
    write_file_patch   -- replace file contents, or one exact substring
    get_ast_symbols    -- return class/function signatures with bodies stripped
    run_test_suite     -- execute the test suite inside the Docker sandbox
    list_workspace_files -- discover what exists before reaching for the others

This module is deliberately a thin wiring layer: every tool delegates to ``workspace``,
``ast_tools``, or ``SandboxRunner`` so the logic stays testable without an MCP client.
"""

import argparse
import os
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mcp_server.ast_tools import extract_outline
from mcp_server.workspace import Workspace, WorkspaceError, workspace_from_env
from sandbox.runner import SandboxError, SandboxRunner

SERVER_NAME = "volatix"

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True)


def build_server(workspace: Workspace, runner: Optional[SandboxRunner] = None) -> MCPServer:
    """Register the tool suite against a workspace. Returns the configured server."""
    server = MCPServer(
        name=SERVER_NAME,
        instructions=(
            "Tools for diagnosing and repairing a Python repository. Start with "
            "list_workspace_files and get_ast_symbols to locate the defect cheaply, read "
            "only the regions you need, apply a minimal edit, then run the test suite."
        ),
    )
    sandbox = runner or SandboxRunner()

    @server.tool(annotations=READ_ONLY)
    def list_workspace_files(pattern: str = "**/*.py") -> str:
        """List files in the workspace matching a glob pattern.

        Call this first, before any other tool, whenever you do not already know the
        exact path of the file you need. Paths returned here are valid inputs to every
        other tool. Skips .git, __pycache__, and virtualenv directories.

        Args:
            pattern: Glob relative to the workspace root, e.g. "**/*.py" or "src/**/*.py".
        """
        matches = workspace.list_files(pattern)
        if not matches:
            return f"No files match {pattern!r}."
        return "\n".join(matches)

    @server.tool(annotations=READ_ONLY)
    def get_ast_symbols(path: str) -> str:
        """Summarize a Python file as class and function signatures, bodies stripped.

        Call this before read_file_content on any file over ~100 lines: it costs a
        fraction of the tokens and tells you which line ranges are worth reading. Each
        symbol is annotated with its line range, so follow up with read_file_content
        using start_line/end_line.

        If the file does not parse, this returns the SyntaxError and its location rather
        than failing -- which is often the bug you are looking for.

        Args:
            path: Workspace-relative path to a .py file.
        """
        source = workspace.read(path)
        return extract_outline(source, path=path).render()

    @server.tool(annotations=READ_ONLY)
    def read_file_content(
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Read a UTF-8 text file from the workspace, optionally a single line range.

        Prefer a line range over a whole file once get_ast_symbols has told you where the
        relevant code lives. Reading a whole large file wastes context you will need for
        the fix. Files over 256 KB must be read as ranges.

        Args:
            path: Workspace-relative path.
            start_line: 1-indexed first line to return. Omit to start at the beginning.
            end_line: 1-indexed last line, inclusive. Omit to read to the end.
        """
        return workspace.read(path, start_line=start_line, end_line=end_line)

    @server.tool(annotations=MUTATING)
    def write_file_patch(
        path: str,
        content: Optional[str] = None,
        old_string: Optional[str] = None,
        new_string: Optional[str] = None,
    ) -> str:
        """Apply a code change, either as an exact substring edit or a full rewrite.

        Two modes, and you must use exactly one:

        - Targeted edit (preferred): pass old_string and new_string. old_string must
          appear exactly once in the file, matching whitespace and indentation exactly.
          Read the region first so your match is accurate.
        - Full rewrite: pass content alone. Use this only for new files or when the edit
          touches most of the file, since it re-sends the entire body.

        A targeted edit that matches zero or several times is rejected rather than
        guessing, so re-read the file and include more surrounding context.

        Args:
            path: Workspace-relative path to write.
            content: Complete new file contents (full-rewrite mode).
            old_string: Exact text to replace (targeted-edit mode).
            new_string: Replacement text (targeted-edit mode).
        """
        targeted = old_string is not None or new_string is not None
        if targeted and content is not None:
            raise WorkspaceError(
                "Pass either content (full rewrite) or old_string/new_string (targeted "
                "edit), not both."
            )
        if targeted:
            if old_string is None or new_string is None:
                raise WorkspaceError("A targeted edit needs both old_string and new_string.")
            return workspace.replace(path, old_string, new_string).describe()
        if content is None:
            raise WorkspaceError("Provide content, or an old_string/new_string pair.")
        return workspace.write(path, content).describe()

    @server.tool(annotations=READ_ONLY)
    def run_test_suite(test_path: str = "tests/") -> str:
        """Run the test suite in an isolated Docker container and return the output.

        Call this after every edit to verify the fix. The container has no network, a
        30-second timeout, and is destroyed afterwards, so it is safe to run repeatedly.
        A timeout is reported as a failure with exit code 124 -- treat it as an infinite
        loop in the code you just wrote.

        Args:
            test_path: Workspace-relative path or pytest node id, e.g. "tests/" or
                "tests/test_parser.py::test_payload".
        """
        result = sandbox.run_tests(str(workspace.root), test_path)
        verdict = "PASSED" if result.passed else "FAILED"
        detail = f"{verdict} (exit {result.exit_code}, {result.duration_ms:.0f}ms)"
        if result.timed_out:
            detail += " -- TIMED OUT"
        return f"{detail}\n\n{result.combined_output}"

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Volatix MCP tool server.")
    parser.add_argument(
        "--workspace",
        help="Repository the agent may read and edit. Defaults to $VOLATIX_WORKSPACE.",
    )
    args = parser.parse_args()

    if args.workspace:
        os.environ["VOLATIX_WORKSPACE"] = args.workspace

    try:
        workspace = workspace_from_env()
    except (WorkspaceError, SandboxError) as exc:
        raise SystemExit(str(exc)) from exc

    build_server(workspace).run(transport="stdio")


if __name__ == "__main__":
    main()
