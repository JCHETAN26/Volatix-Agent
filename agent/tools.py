"""Tool definitions and dispatch for the Executor node -- Phase 4.

These are the same operations the MCP server exposes, backed by the same ``Workspace``
so path confinement and edit semantics cannot drift between the two surfaces. The MCP
server is the *external* protocol surface -- it lets any MCP client drive these tools.
The graph runs in-process against the same backend rather than round-tripping over
stdio, which keeps the self-correction loop fast without duplicating the logic.

``run_test_suite`` is deliberately absent: validation belongs to the Validator node, so
the Executor cannot declare itself finished.
"""

from typing import Any, Dict, List, Tuple

from mcp_server.ast_tools import extract_outline
from mcp_server.workspace import Workspace, WorkspaceError

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "list_workspace_files",
        "description": (
            "List files in the repository matching a glob pattern. Call this first "
            "whenever you do not already know the exact path of a file you need. Paths "
            "returned here are valid inputs to every other tool. Skips .git, "
            "__pycache__, and virtualenv directories."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": 'Glob relative to the repo root, e.g. "**/*.py".',
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_ast_symbols",
        "description": (
            "Summarize a Python file as class and function signatures with the bodies "
            "stripped. Call this before read_file_content on any file over roughly 100 "
            "lines: it costs a fraction of the tokens and reports the line range of each "
            "symbol so you can read only the region that matters. If the file does not "
            "parse, this returns the SyntaxError and its location, which is often the "
            "bug itself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to a .py file."}},
            "required": ["path"],
        },
    },
    {
        "name": "read_file_content",
        "description": (
            "Read a text file, or a single line range of one. Prefer a range once "
            "get_ast_symbols has told you where the relevant code lives; reading a whole "
            "large file wastes context you will need for the fix."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the repo root."},
                "start_line": {"type": "integer", "description": "1-indexed first line."},
                "end_line": {"type": "integer", "description": "1-indexed last line, inclusive."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file_patch",
        "description": (
            "Apply a code change, either as an exact substring edit or a full rewrite. "
            "Use exactly one mode. Targeted edit (preferred): pass old_string and "
            "new_string, where old_string appears exactly once and matches whitespace "
            "and indentation exactly. Full rewrite: pass content alone, for new files or "
            "when the edit touches most of the file. A targeted edit matching zero or "
            "several times is rejected rather than guessed, so read the region first and "
            "include enough surrounding context to be unique."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the repo root."},
                "content": {"type": "string", "description": "Complete new file contents."},
                "old_string": {"type": "string", "description": "Exact text to replace."},
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path"],
        },
    },
]

TOOL_NAMES = frozenset(t["name"] for t in TOOL_DEFINITIONS)


class ToolBackend:
    """Executes tool calls against a confined workspace, tracking what was modified."""

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.modified_files: Dict[str, str] = {}

    def dispatch(self, name: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
        """Run one tool call.

        Returns:
            ``(result_text, is_error)``. Tool failures are returned rather than raised so
            the model can read the message and correct itself on the next turn -- an
            exception here would abort a run that is still recoverable.
        """
        try:
            handler = getattr(self, f"_{name}", None)
            if handler is None:
                return f"Unknown tool: {name}", True
            return handler(**arguments), False
        except WorkspaceError as exc:
            return str(exc), True
        except TypeError as exc:
            return f"Invalid arguments for {name}: {exc}", True

    # --- handlers ----------------------------------------------------------------

    def _list_workspace_files(self, pattern: str = "**/*.py") -> str:
        matches = self.workspace.list_files(pattern)
        return "\n".join(matches) if matches else f"No files match {pattern!r}."

    def _get_ast_symbols(self, path: str) -> str:
        return extract_outline(self.workspace.read(path), path=path).render()

    def _read_file_content(self, path, start_line=None, end_line=None) -> str:
        return self.workspace.read(path, start_line=start_line, end_line=end_line)

    def _write_file_patch(self, path, content=None, old_string=None, new_string=None) -> str:
        targeted = old_string is not None or new_string is not None
        if targeted and content is not None:
            raise WorkspaceError(
                "Pass either content (full rewrite) or old_string/new_string (targeted "
                "edit), not both."
            )
        if targeted:
            if old_string is None or new_string is None:
                raise WorkspaceError("A targeted edit needs both old_string and new_string.")
            result = self.workspace.replace(path, old_string, new_string)
        elif content is not None:
            result = self.workspace.write(path, content)
        else:
            raise WorkspaceError("Provide content, or an old_string/new_string pair.")

        # Record post-edit contents so the graph can emit a diff without re-reading.
        self.modified_files[result.path] = self.workspace.read(result.path)
        return result.describe()
