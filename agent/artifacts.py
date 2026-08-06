"""Output artifacts -- Phase 6.

Turns a finished run into two things a human can act on:

  * a unified ``.diff`` that ``git apply`` accepts, so the fix can be reviewed and
    landed like any other patch;
  * a markdown root-cause report tying the bug to the change and the proof it works.

Both are built from run state alone, so they are pure functions and stay testable.
"""

import difflib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from agent.nodes.stack_parser import trim_stack_trace
from agent.state import AgentState

DIFF_FILENAME = "fix.diff"
REPORT_FILENAME = "root_cause_analysis.md"


def missing_baselines(
    originals: Mapping[str, Optional[str]],
    modified: Mapping[str, str],
) -> List[str]:
    """Modified paths with no recorded pre-edit content.

    An absent key is not the same as a ``None`` value. ``None`` means "the run created
    this file" and diffs correctly against /dev/null; an absent key means the baseline
    was never captured, and treating that as a creation would emit a patch claiming an
    existing file is new -- which either fails to apply or silently clobbers it.
    """
    return sorted(path for path in modified if path not in originals)


def build_unified_diff(
    originals: Mapping[str, Optional[str]],
    modified: Mapping[str, str],
) -> str:
    """Render a unified diff across every modified file.

    ``originals[path] is None`` marks a file the run created, rendered against /dev/null
    so the patch applies cleanly. Files with no recorded baseline are skipped rather than
    guessed at -- an incomplete patch that says so beats a wrong one that does not.
    """
    chunks = []
    for path in sorted(modified):
        if path not in originals:
            continue
        after = modified[path]
        before = originals[path]
        if before == after:
            continue

        from_file = "/dev/null" if before is None else f"a/{path}"
        diff = difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=from_file,
            tofile=f"b/{path}",
            n=3,
        )
        text = "".join(diff)
        if not text:
            continue
        # A diff whose last line lacks a newline confuses `git apply`.
        if not text.endswith("\n"):
            text += "\n\\ No newline at end of file\n"
        chunks.append(text)

    return "".join(chunks)


def _format_usage(usage: Mapping[str, int]) -> str:
    if not usage:
        return "_not recorded_"
    return (
        f"{usage.get('total_tokens', 0):,} tokens "
        f"({usage.get('input_tokens', 0):,} in / {usage.get('output_tokens', 0):,} out) "
        f"across {usage.get('calls', 0)} model calls"
    )


def build_report(state: AgentState, diff: str = "", skipped: Optional[List[str]] = None) -> str:
    """Render the markdown root-cause analysis for a finished run.

    ``skipped`` lists files excluded from the patch for want of a baseline; they are
    called out so a reviewer never assumes the diff is complete when it is not.
    """
    passed = state.get("test_passed", False)
    verdict = "Resolved" if passed else "Unresolved"
    icon = "✅" if passed else "❌"

    modified = state.get("modified_files") or {}
    plan = state.get("plan") or []
    targets = state.get("target_files") or []

    lines = [
        f"# Root Cause Analysis — {verdict}",
        "",
        f"**Status:** {icon} {verdict}  ",
        f"**Retries used:** {state.get('retry_count', 0)} of {state.get('max_retries', 0)}  ",
        f"**Token usage:** {_format_usage(state.get('token_usage') or {})}  ",
        f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## Issue",
        "",
        state.get("issue_description", "_none supplied_"),
        "",
        "## Root cause",
        "",
        state.get("analysis") or "_The planner did not record an analysis._",
        "",
        "## Plan",
        "",
    ]
    lines += [f"{i}. {step}" for i, step in enumerate(plan, 1)] or ["_No plan recorded._"]

    lines += ["", "## Files changed", ""]
    if modified:
        lines += [f"- `{path}`" for path in sorted(modified)]
    else:
        lines.append("_No files were modified._")

    if skipped:
        lines += [
            "",
            "> **Incomplete patch.** No pre-edit baseline was recorded for "
            + ", ".join(f"`{p}`" for p in skipped)
            + ", so these are not represented in the diff below. Review them directly.",
        ]

    if targets:
        predicted = set(targets)
        actual = set(modified)
        if predicted != actual:
            # A mismatch is a useful review signal: the plan aimed somewhere else.
            lines += [
                "",
                f"> Planner predicted {sorted(predicted)} but the executor changed "
                f"{sorted(actual)}.",
            ]

    lines += ["", "## Verification", ""]
    if passed:
        lines.append("The repository's test suite passes in the sandbox after this change.")
    else:
        lines.append("The test suite still fails after the retry budget was exhausted.")
    lines += [
        "",
        "```",
        trim_stack_trace(state.get("test_output") or "").strip() or "(no test output)",
        "```",
    ]

    if diff.strip():
        lines += ["", "## Patch", "", "```diff", diff.rstrip(), "```"]

    return "\n".join(lines) + "\n"


@dataclass
class RunArtifacts:
    """The files produced by a completed run."""

    diff: str
    report: str

    def write(self, output_dir: str) -> Dict[str, Path]:
        """Write both artifacts, creating the directory if needed."""
        directory = Path(output_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)

        written: Dict[str, Path] = {}
        diff_path = directory / DIFF_FILENAME
        diff_path.write_text(self.diff, encoding="utf-8")
        written["diff"] = diff_path

        report_path = directory / REPORT_FILENAME
        report_path.write_text(self.report, encoding="utf-8")
        written["report"] = report_path
        return written


def build_artifacts(
    state: AgentState,
    originals: Mapping[str, Optional[str]],
) -> RunArtifacts:
    """Build both artifacts from a finished run."""
    modified = state.get("modified_files") or {}
    diff = build_unified_diff(originals, modified)
    skipped = missing_baselines(originals, modified)
    return RunArtifacts(diff=diff, report=build_report(state, diff, skipped=skipped))
