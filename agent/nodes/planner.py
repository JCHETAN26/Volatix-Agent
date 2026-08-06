"""Planner node -- Phase 4.

Consumes the issue description plus an AST outline of the repository and emits an
ordered, actionable resolution plan into ``state["plan"]``.

The outline is built from body-stripped signatures rather than file contents: that is
what makes it affordable to show the Planner the *whole* repository shape in one request
instead of guessing which files to open.
"""

import json
from typing import List

from agent.llm import PLANNER_EFFORT, ClaudeClient, LLMError, text_of
from agent.state import AgentState
from mcp_server.ast_tools import extract_outline
from mcp_server.workspace import Workspace, WorkspaceError

# Keeps a large repository from blowing the context window in a single planning call.
MAX_OUTLINE_FILES = 40
MAX_OUTLINE_CHARS = 60_000

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "analysis": {
            "type": "string",
            "description": "What the defect is and why it produces the reported symptom.",
        },
        "target_files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Repository-relative paths that most likely need editing.",
        },
        "steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Ordered, concrete repair steps. Each names a file and a change.",
        },
    },
    "required": ["analysis", "target_files", "steps"],
    "additionalProperties": False,
}

SYSTEM = """You are the planning stage of an automated bug-fixing pipeline.

You are given a bug report and an outline of a Python repository: every module's classes
and functions, with signatures and line ranges but no function bodies.

Produce a minimal, concrete repair plan. Name the specific file and function each step
touches. Plan only the change that fixes the reported defect and any test that must pass;
do not plan refactors, cleanups, added abstractions, or error handling for cases that
cannot happen.

If the outline shows a SyntaxError, that is almost certainly the defect itself -- plan to
fix it first.

You cannot see function bodies, so where a step depends on implementation detail, say
which file and line range the executor should read before editing."""


def build_repository_outline(workspace: Workspace) -> str:
    """Render body-stripped signatures for the repository's Python modules."""
    sections: List[str] = []
    budget = MAX_OUTLINE_CHARS
    paths = workspace.list_files("**/*.py", limit=MAX_OUTLINE_FILES)

    for path in paths:
        try:
            rendered = extract_outline(workspace.read(path), path=path).render()
        except WorkspaceError as exc:
            rendered = f"# {path}\n# (unreadable: {exc})"
        if len(rendered) > budget:
            sections.append(f"# ... outline truncated after {len(sections)} files ...")
            break
        sections.append(rendered)
        budget -= len(rendered)

    if not sections:
        return "# (no Python files found)"
    return "\n\n".join(sections)


def planner_node(state: AgentState, client: ClaudeClient, workspace: Workspace) -> dict:
    """Produce ``plan`` from the issue description and the repository outline."""
    outline = build_repository_outline(workspace)
    prompt = (
        f"<bug_report>\n{state['issue_description']}\n</bug_report>\n\n"
        f"<repository_outline>\n{outline}\n</repository_outline>\n\n"
        "Produce the repair plan."
    )

    response = client.complete(
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        effort=PLANNER_EFFORT,
        output_schema=PLAN_SCHEMA,
    )

    try:
        parsed = json.loads(text_of(response))
    except json.JSONDecodeError as exc:  # pragma: no cover - schema makes this unreachable
        raise LLMError(f"Planner returned unparseable JSON: {exc}") from exc

    steps = parsed.get("steps") or []
    if not steps:
        raise LLMError("Planner produced no steps.")

    return {
        "plan": steps,
        "analysis": parsed.get("analysis", ""),
        "target_files": parsed.get("target_files", []),
    }
