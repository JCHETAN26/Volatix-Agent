"""Executor node -- Phase 4.

Runs a tool-use loop that reads the repository and applies the plan's edits, recording
every write in ``state["modified_files"]``.

On a retry pass it also consumes ``state["error_summary"]`` -- the trimmed stack trace
from the previous failed validation -- which is what closes the self-correction loop.

This is a hand-written loop rather than the SDK's beta tool runner: the run needs
per-iteration token accounting for the Phase 7 metrics and a hard iteration ceiling, and
the benchmark should not depend on a beta surface.
"""

from typing import Any, Dict, List

from agent.llm import (
    EXECUTOR_EFFORT,
    ClaudeClient,
    LLMError,
    cacheable_text,
    text_of,
    tool_uses,
)
from agent.state import AgentState
from agent.tools import TOOL_DEFINITIONS, ToolBackend

# Bounds a run that would otherwise ping-pong between reads without ever editing.
MAX_TOOL_ITERATIONS = 25

SYSTEM = """You are the execution stage of an automated bug-fixing pipeline.

Apply the repair plan to the repository using the tools provided. Work directly: read
what you need, make the edit, and stop. You do not run the tests -- a separate validation
stage does that and will report back to you if the fix is wrong.

Make the smallest change that fixes the defect. Do not refactor, add abstractions, add
error handling for cases that cannot happen, or tidy code you happen to be reading.

Before a targeted edit, read the exact region you are about to change so old_string
matches byte for byte, including indentation. If an edit is rejected for matching zero or
several times, read more surrounding context and retry with a longer, unique old_string.

When the plan is applied, reply with a one-or-two-sentence summary of what you changed
and make no further tool calls."""

RETRY_PREAMBLE = """Your previous attempt did not pass the test suite.

<test_failure>
{error_summary}
</test_failure>

Diagnose why the change failed from the evidence above, then correct it. Do not repeat
the previous edit unchanged. If your earlier diagnosis was wrong, say so briefly and fix
the actual cause rather than adding a workaround on top."""


def _initial_prompt(state: AgentState) -> str:
    plan = "\n".join(f"{i}. {step}" for i, step in enumerate(state["plan"], start=1))
    parts = [
        f"<bug_report>\n{state['issue_description']}\n</bug_report>",
        f"<root_cause>\n{state.get('analysis', '')}\n</root_cause>",
        f"<repair_plan>\n{plan}\n</repair_plan>",
    ]
    if state.get("error_summary"):
        parts.append(RETRY_PREAMBLE.format(error_summary=state["error_summary"]))
    else:
        parts.append("Apply the plan.")
    return "\n\n".join(parts)


def executor_node(
    state: AgentState,
    client: ClaudeClient,
    backend: ToolBackend,
) -> dict:
    """Apply the plan via tool calls, returning the files that were modified."""
    # The opening turn is stable for the whole loop, so it carries a cache breakpoint;
    # combined with the one on the system block, the entire fixed prefix stays warm
    # across every tool iteration instead of being re-sent at full price.
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": cacheable_text(_initial_prompt(state))}
    ]
    summary = ""

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.complete(
            system=SYSTEM,
            messages=messages,
            effort=EXECUTOR_EFFORT,
            tools=TOOL_DEFINITIONS,
        )
        summary = text_of(response) or summary

        calls = tool_uses(response)
        if not calls:
            break

        # Echo the full content back, including thinking blocks, or the next turn 400s.
        messages.append({"role": "assistant", "content": response.content})

        results = []
        for call in calls:
            output, is_error = backend.dispatch(call.name, dict(call.input))
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": output,
                    "is_error": is_error,
                }
            )
        # All results for one assistant turn go back in a single user message; splitting
        # them trains the model out of making parallel calls.
        messages.append({"role": "user", "content": results})
    else:
        raise LLMError(
            f"Executor exceeded {MAX_TOOL_ITERATIONS} tool iterations without finishing."
        )

    if not backend.modified_files:
        # A pass that edits nothing cannot fix anything; surfacing it here beats letting
        # the Validator report a confusing unchanged-test failure.
        raise LLMError(f"Executor made no edits. Model said: {summary.strip() or '(nothing)'}")

    return {
        "modified_files": dict(backend.modified_files),
        "token_usage": client.usage.as_dict(),
    }
