"""Confined filesystem access for the MCP tool suite -- Phase 3.

Every path an agent supplies is untrusted: it comes from an LLM reasoning about a bug
report, and a traversal like ``../../.ssh/id_rsa`` is one plausible token sequence away.
``Workspace`` resolves each path and refuses anything that escapes the configured root,
so no tool can read or write outside the repository under repair.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

# Reading a whole vendored dependency would blow the context window and the token budget.
MAX_READ_BYTES = 256_000
DEFAULT_GLOB = "**/*.py"

# Directories that are never useful to an agent and would swamp a file listing.
IGNORED_DIRS = frozenset(
    {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache"}
)


class WorkspaceError(RuntimeError):
    """Raised for an escaped path, a missing file, or unreadable content."""


@dataclass
class WriteResult:
    """What a write actually changed, so the agent can confirm its edit landed."""

    path: str
    created: bool
    lines_before: int
    lines_after: int

    def describe(self) -> str:
        action = "Created" if self.created else "Updated"
        return f"{action} {self.path} " f"({self.lines_before} -> {self.lines_after} lines)"


class Workspace:
    """Filesystem operations confined to a single root directory."""

    def __init__(self, root: str):
        resolved = Path(root).expanduser().resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"Workspace root is not a directory: {resolved}")
        self.root = resolved

    def resolve(self, path: str) -> Path:
        """Map an agent-supplied path to a real path inside the workspace.

        Raises:
            WorkspaceError: The path escapes the root, via ``..``, an absolute path,
                a symlink pointing outside, or a NUL byte.
        """
        if "\x00" in path:
            raise WorkspaceError("Path contains a null byte")

        candidate = Path(path)
        if candidate.is_absolute():
            # Absolute paths are allowed only if they already live under the root.
            resolved = candidate.resolve()
        else:
            resolved = (self.root / candidate).resolve()

        # resolve() follows symlinks, so this also catches a link pointing outside.
        if resolved != self.root and self.root not in resolved.parents:
            raise WorkspaceError(f"Path escapes the workspace root: {path}")
        return resolved

    def relative(self, resolved: Path) -> str:
        """Render a resolved path back as workspace-relative, for agent-facing output."""
        return str(resolved.relative_to(self.root))

    def read(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> str:
        """Read a UTF-8 text file, optionally a 1-indexed inclusive line range."""
        resolved = self.resolve(path)
        if not resolved.is_file():
            raise WorkspaceError(f"Not a file: {path}")

        size = resolved.stat().st_size
        if size > MAX_READ_BYTES:
            raise WorkspaceError(
                f"File is {size} bytes, over the {MAX_READ_BYTES} byte read limit. "
                "Use start_line/end_line to read a range."
            )

        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceError(f"File is not UTF-8 text: {path}") from exc

        if start_line is None and end_line is None:
            return text
        return self._slice_lines(text, start_line, end_line, path)

    @staticmethod
    def _slice_lines(
        text: str, start_line: Optional[int], end_line: Optional[int], path: str
    ) -> str:
        lines = text.splitlines()
        start = 1 if start_line is None else start_line
        end = len(lines) if end_line is None else end_line
        if start < 1:
            raise WorkspaceError("start_line is 1-indexed and must be >= 1")
        # Check the file bound first: with end_line omitted it defaults to the last line,
        # so an out-of-range start would otherwise surface as a confusing inverted range.
        if start > len(lines):
            raise WorkspaceError(f"{path} has only {len(lines)} lines; start_line={start}")
        if end < start:
            raise WorkspaceError(f"end_line ({end}) is before start_line ({start})")
        return "\n".join(lines[start - 1 : end])

    def write(self, path: str, content: str) -> WriteResult:
        """Replace a file's contents wholesale, creating parent directories as needed."""
        resolved = self.resolve(path)
        if resolved.is_dir():
            raise WorkspaceError(f"Path is a directory: {path}")

        created = not resolved.exists()
        before = 0 if created else len(resolved.read_text(encoding="utf-8").splitlines())

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")

        return WriteResult(
            path=self.relative(resolved),
            created=created,
            lines_before=before,
            lines_after=len(content.splitlines()),
        )

    def replace(self, path: str, old_string: str, new_string: str) -> WriteResult:
        """Replace one exact occurrence of ``old_string``.

        Refusing on zero or multiple matches is deliberate: a silent no-op or a
        shotgun replacement both produce a "successful" edit that didn't do what the
        agent intended, and the Validator would then attribute the failure to the wrong
        cause.
        """
        resolved = self.resolve(path)
        if not resolved.is_file():
            raise WorkspaceError(f"Not a file: {path}")
        if old_string == new_string:
            raise WorkspaceError("old_string and new_string are identical")

        text = resolved.read_text(encoding="utf-8")
        occurrences = text.count(old_string)
        if occurrences == 0:
            raise WorkspaceError(
                f"old_string not found in {path}. Read the file first and match it exactly, "
                "including indentation."
            )
        if occurrences > 1:
            raise WorkspaceError(
                f"old_string appears {occurrences} times in {path}. "
                "Include surrounding context to make it unique."
            )

        updated = text.replace(old_string, new_string)
        resolved.write_text(updated, encoding="utf-8")
        return WriteResult(
            path=self.relative(resolved),
            created=False,
            lines_before=len(text.splitlines()),
            lines_after=len(updated.splitlines()),
        )

    def list_files(self, pattern: str = DEFAULT_GLOB, limit: int = 500) -> List[str]:
        """List workspace-relative file paths matching a glob, skipping noise dirs."""
        matches = []
        for candidate in sorted(self.root.glob(pattern)):
            if not candidate.is_file():
                continue
            if IGNORED_DIRS.intersection(candidate.relative_to(self.root).parts):
                continue
            matches.append(self.relative(candidate))
            if len(matches) >= limit:
                break
        return matches


def workspace_from_env(env_var: str = "VOLATIX_WORKSPACE") -> Workspace:
    """Build the Workspace the MCP server serves, from the environment."""
    root = os.environ.get(env_var)
    if not root:
        raise WorkspaceError(
            f"{env_var} is not set. Point it at the repository the agent should repair."
        )
    return Workspace(root)
