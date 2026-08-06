"""Workspace confinement and file operations.

The escape tests are the important ones: every path here originates from an LLM, so a
traversal is a realistic input rather than a hypothetical.
"""

import pytest

from mcp_server.workspace import MAX_READ_BYTES, Workspace, WorkspaceError, workspace_from_env


@pytest.fixture
def ws(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "README.md").write_text("# hi\n")
    return Workspace(str(tmp_path))


# --- confinement -----------------------------------------------------------------


@pytest.mark.parametrize(
    "escape",
    [
        "../secrets.txt",
        "../../etc/passwd",
        "pkg/../../outside.py",
        "./pkg/../../../outside.py",
    ],
)
def test_relative_traversal_is_rejected(ws, escape):
    with pytest.raises(WorkspaceError, match="escapes the workspace root"):
        ws.resolve(escape)


def test_absolute_path_outside_root_is_rejected(ws):
    with pytest.raises(WorkspaceError, match="escapes the workspace root"):
        ws.resolve("/etc/passwd")


def test_absolute_path_inside_root_is_allowed(ws):
    assert ws.resolve(str(ws.root / "pkg" / "calc.py")).name == "calc.py"


def test_symlink_pointing_outside_is_rejected(ws, tmp_path):
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret")
    (ws.root / "link.txt").symlink_to(outside)

    with pytest.raises(WorkspaceError, match="escapes the workspace root"):
        ws.resolve("link.txt")


def test_null_byte_is_rejected(ws):
    with pytest.raises(WorkspaceError, match="null byte"):
        ws.resolve("pkg/calc\x00.py")


def test_traversal_that_returns_inside_is_allowed(ws):
    """`pkg/../README.md` never leaves the root, so it is legitimate."""
    assert ws.resolve("pkg/../README.md").name == "README.md"


def test_root_must_be_a_directory(tmp_path):
    with pytest.raises(WorkspaceError, match="not a directory"):
        Workspace(str(tmp_path / "nope"))


# --- reading ---------------------------------------------------------------------


def test_read_whole_file(ws):
    assert ws.read("pkg/calc.py") == "def add(a, b):\n    return a + b\n"


def test_read_line_range_is_inclusive_and_one_indexed(ws):
    (ws.root / "n.txt").write_text("one\ntwo\nthree\nfour\n")
    assert ws.read("n.txt", start_line=2, end_line=3) == "two\nthree"


def test_read_open_ended_range(ws):
    (ws.root / "n.txt").write_text("one\ntwo\nthree\n")
    assert ws.read("n.txt", start_line=2) == "two\nthree"


def test_read_rejects_inverted_range(ws):
    # Both lines exist, so this isolates the ordering check rather than the file bound.
    (ws.root / "n.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
    with pytest.raises(WorkspaceError, match="before start_line"):
        ws.read("n.txt", start_line=5, end_line=2)


def test_read_rejects_zero_start_line(ws):
    with pytest.raises(WorkspaceError, match="1-indexed"):
        ws.read("pkg/calc.py", start_line=0)


def test_read_rejects_start_past_end_of_file(ws):
    with pytest.raises(WorkspaceError, match="only 2 lines"):
        ws.read("pkg/calc.py", start_line=99)


def test_read_rejects_missing_file(ws):
    with pytest.raises(WorkspaceError, match="Not a file"):
        ws.read("pkg/ghost.py")


def test_read_rejects_binary(ws):
    (ws.root / "blob.bin").write_bytes(b"\xff\xfe\x00\x01binary")
    with pytest.raises(WorkspaceError, match="not UTF-8"):
        ws.read("blob.bin")


def test_read_rejects_oversized_file(ws):
    (ws.root / "huge.py").write_text("x = 1\n" * (MAX_READ_BYTES // 3))
    with pytest.raises(WorkspaceError, match="read limit"):
        ws.read("huge.py")


# --- writing ---------------------------------------------------------------------


def test_write_creates_file_and_parents(ws):
    result = ws.write("new/deep/mod.py", "x = 1\n")

    assert result.created is True
    assert (ws.root / "new" / "deep" / "mod.py").read_text() == "x = 1\n"
    assert "Created" in result.describe()


def test_write_overwrites_and_reports_line_delta(ws):
    result = ws.write("pkg/calc.py", "def add(a, b):\n    return a + b\n\n\nX = 1\n")

    assert result.created is False
    assert result.lines_before == 2
    assert result.lines_after == 5
    assert "2 -> 5 lines" in result.describe()


def test_write_outside_root_is_rejected(ws):
    with pytest.raises(WorkspaceError, match="escapes the workspace root"):
        ws.write("../escaped.py", "pwned")


def test_write_rejects_a_directory(ws):
    with pytest.raises(WorkspaceError, match="is a directory"):
        ws.write("pkg", "nope")


# --- targeted replacement --------------------------------------------------------


def test_replace_single_occurrence(ws):
    ws.replace("pkg/calc.py", "a + b", "a - b")
    assert (ws.root / "pkg" / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"


def test_replace_rejects_zero_matches(ws):
    with pytest.raises(WorkspaceError, match="not found"):
        ws.replace("pkg/calc.py", "a * b", "a + b")


def test_replace_rejects_multiple_matches(ws):
    """Ambiguity must fail loudly -- a shotgun edit corrupts the file silently."""
    (ws.root / "dup.py").write_text("x = 1\ny = 1\n")

    with pytest.raises(WorkspaceError, match="appears 2 times"):
        ws.replace("dup.py", "= 1", "= 2")


def test_replace_rejects_identical_strings(ws):
    with pytest.raises(WorkspaceError, match="identical"):
        ws.replace("pkg/calc.py", "a + b", "a + b")


def test_replace_rejects_missing_file(ws):
    with pytest.raises(WorkspaceError, match="Not a file"):
        ws.replace("ghost.py", "a", "b")


# --- listing ---------------------------------------------------------------------


def test_list_files_returns_relative_paths(ws):
    assert ws.list_files("**/*.py") == ["pkg/calc.py"]


def test_list_files_skips_noise_directories(ws):
    (ws.root / "__pycache__").mkdir()
    (ws.root / "__pycache__" / "calc.pyc.py").write_text("cached")
    (ws.root / ".git").mkdir()
    (ws.root / ".git" / "hook.py").write_text("hook")

    assert ws.list_files("**/*.py") == ["pkg/calc.py"]


def test_list_files_honours_limit(ws):
    for i in range(10):
        (ws.root / f"m{i}.py").write_text("")
    assert len(ws.list_files("*.py", limit=4)) == 4


def test_list_files_empty_result(ws):
    assert ws.list_files("**/*.rs") == []


# --- env wiring ------------------------------------------------------------------


def test_workspace_from_env(ws, monkeypatch):
    monkeypatch.setenv("VOLATIX_WORKSPACE", str(ws.root))
    assert workspace_from_env().root == ws.root


def test_workspace_from_env_requires_the_variable(monkeypatch):
    monkeypatch.delenv("VOLATIX_WORKSPACE", raising=False)
    with pytest.raises(WorkspaceError, match="is not set"):
        workspace_from_env()
