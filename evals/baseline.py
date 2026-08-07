"""Condition A: the single-pass baseline -- Phase 7.

Deliberately the naive approach the project argues against: one prompt carrying the full
text of every source file, no AST trimming, no tools, no sandbox feedback, no retry. The
model returns a corrected file, it is written, and the tests run exactly once.

This is what the multi-agent loop has to beat, so it is implemented honestly -- same
model and effort as the Executor, and the whole repository in context rather than a
deliberately crippled prompt.
"""

from pathlib import Path
from typing import Dict

from agent.llm import EXECUTOR_EFFORT, ClaudeClient, LLMError, text_of

MAX_SOURCE_CHARS = 60_000

FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Repository-relative path of the single file to rewrite.",
        },
        "content": {
            "type": "string",
            "description": "The complete corrected contents of that file.",
        },
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}

SYSTEM = """You are fixing a bug in a small Python repository.

You are given the bug report and the full contents of every source file. Identify the
defect and return the complete corrected contents of the single file that needs to
change. Make the smallest change that fixes the reported defect.

You get one attempt. You cannot run the tests and you will not see the result."""


def read_sources(repo: Path) -> Dict[str, str]:
    """Every Python source file in the repository, excluding the tests directory."""
    sources: Dict[str, str] = {}
    budget = MAX_SOURCE_CHARS
    for path in sorted(repo.rglob("*.py")):
        relative = path.relative_to(repo)
        if "tests" in relative.parts or path.name == "conftest.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if len(text) > budget:
            break
        sources[str(relative)] = text
        budget -= len(text)
    return sources


def render_prompt(issue: str, sources: Dict[str, str]) -> str:
    blocks = "\n\n".join(
        f'<file path="{path}">\n{content}\n</file>' for path, content in sources.items()
    )
    return (
        f"<bug_report>\n{issue}\n</bug_report>\n\n"
        f"<repository>\n{blocks}\n</repository>\n\n"
        "Return the corrected contents of the one file that needs to change."
    )


def run_baseline(issue: str, repo: Path, client: ClaudeClient) -> str:
    """Apply a single-pass fix. Returns the repository-relative path that was written.

    Raises:
        LLMError: The model refused, returned unparseable JSON, or named a path outside
            the repository.
    """
    import json

    sources = read_sources(repo)
    response = client.complete(
        system=SYSTEM,
        messages=[{"role": "user", "content": render_prompt(issue, sources)}],
        effort=EXECUTOR_EFFORT,
        output_schema=FIX_SCHEMA,
    )

    try:
        parsed = json.loads(text_of(response))
    except json.JSONDecodeError as exc:
        raise LLMError(f"Baseline returned unparseable JSON: {exc}") from exc

    relative = parsed["path"]
    target = (repo / relative).resolve()
    if repo.resolve() not in target.parents and target != repo.resolve():
        # The baseline gets no Workspace, so it needs its own confinement check.
        raise LLMError(f"Baseline tried to write outside the repository: {relative}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(parsed["content"], encoding="utf-8")
    return relative
