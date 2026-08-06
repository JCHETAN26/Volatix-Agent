"""Stack-trace trimming -- Phase 5.

Raw pytest output is mostly noise to a model trying to fix a bug: session banners,
progress dots, passing tests, and framework frames from ``_pytest``, ``importlib``, and
``site-packages``. A collection error is the worst case -- roughly twenty frames of
import machinery wrapped around four useful lines.

This isolates, per failure, the assertion or exception text and the innermost line of
*the user's own code*, and drops everything else. Feeding the Executor a focused failure
both costs fewer tokens and stops it chasing frames inside pytest itself.

Parsing is deliberately forgiving: if the output does not look like pytest at all -- a
sandbox error, a timeout kill, a segfault -- the raw tail is returned instead. Returning
nothing would silently break the self-correction loop, which is worse than being verbose.
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional

# How much failure detail is worth feeding back. Beyond a few failures the model should
# fix the first ones and re-run rather than reason about all of them at once.
MAX_FAILURES = 3
MAX_CHARS = 3_000

# Frames from these paths are never the bug in the repository under repair.
NOISE_MARKERS = ("site-packages", "_pytest", "<frozen", "/importlib/", "/lib/python")

_SECTION = re.compile(r"^=+\s*(FAILURES|ERRORS|short test summary info)\s*=+$")
_BLOCK_HEADER = re.compile(r"^_{3,}\s*(.+?)\s*_{3,}$")
_SEPARATOR = re.compile(r"^[_\s]+$")
_LOCATION = re.compile(r"^(\S+\.py):(\d+):")
_COUNTS = re.compile(r"^=*\s*\d+ (?:failed|passed|error|skipped)")
_SUMMARY_LINE = re.compile(r"^(FAILED|ERROR)\s+(\S+)")


@dataclass
class Failure:
    """One failing test or collection error, stripped to its cause."""

    test_id: str
    location: Optional[str] = None
    detail: List[str] = field(default_factory=list)

    def render(self) -> str:
        head = self.test_id
        if self.location:
            head += f"  ({self.location})"
        body = "\n".join(f"  {line}" for line in self.detail)
        return f"{head}\n{body}" if body else head


@dataclass
class TrimmedFailures:
    """The parsed result. ``failures`` is empty when nothing failed or nothing parsed."""

    failures: List[Failure] = field(default_factory=list)
    counts: str = ""
    fallback: Optional[str] = None

    @property
    def parsed(self) -> bool:
        return bool(self.failures)

    def render(self) -> str:
        if self.fallback is not None:
            return self.fallback
        if not self.failures:
            return self.counts or "No failures reported."

        parts = [self.counts] if self.counts else []
        shown = self.failures[:MAX_FAILURES]
        parts.extend(f.render() for f in shown)
        hidden = len(self.failures) - len(shown)
        if hidden > 0:
            parts.append(f"... and {hidden} more failure(s) not shown.")
        return "\n\n".join(parts)


def _is_noise(line: str) -> bool:
    return any(marker in line for marker in NOISE_MARKERS)


def _tail(text: str, limit: int = MAX_CHARS) -> str:
    """Keep the end of the output -- the cause is nearer the bottom than the top."""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return "...(truncated)...\n" + stripped[-limit:]


def parse_pytest_output(output: str) -> TrimmedFailures:
    """Split raw pytest output into per-failure causes."""
    lines = output.splitlines()
    result = TrimmedFailures()

    section = None
    current: Optional[Failure] = None
    summary_ids: List[str] = []

    for line in lines:
        header = _SECTION.match(line.strip())
        if header:
            section = header.group(1)
            current = None
            continue

        if _COUNTS.match(line.strip()):
            result.counts = line.strip().strip("= ").strip()
            continue

        if section == "short test summary info":
            match = _SUMMARY_LINE.match(line.strip())
            if match:
                summary_ids.append(match.group(2))
            continue

        if section not in ("FAILURES", "ERRORS"):
            continue

        block = _BLOCK_HEADER.match(line.strip())
        if block:
            current = Failure(test_id=block.group(1).strip())
            result.failures.append(current)
            continue

        if current is None or _SEPARATOR.match(line):
            continue

        if line.startswith("E "):
            detail = line[1:].strip()
            if detail:
                current.detail.append(detail)
            continue

        location = _LOCATION.match(line.strip())
        if location and not _is_noise(line):
            # Later locations are deeper in the call chain, so the last one wins.
            current.location = f"{location.group(1)}:{location.group(2)}"

    _attach_summary_ids(result.failures, summary_ids)
    return result


def _attach_summary_ids(failures: List[Failure], summary_ids: List[str]) -> None:
    """Upgrade bare test names to full node ids using the short-summary section."""
    for failure in failures:
        name = failure.test_id.replace("ERROR collecting ", "").strip()
        for node_id in summary_ids:
            if node_id.endswith(f"::{name}") or node_id == name:
                failure.test_id = node_id
                break


def trim_stack_trace(output: str) -> str:
    """Reduce raw pytest output to the failing assertions and their locations.

    Falls back to the raw tail when the output is not recognisable pytest output, so the
    Executor always receives some evidence to work from.
    """
    if not output or not output.strip():
        return "The test run produced no output."

    result = parse_pytest_output(output)
    if not result.parsed:
        # Not pytest output, or a shape this parser does not know: better to hand back
        # the raw tail than to claim there is nothing wrong.
        result.fallback = _tail(output)

    rendered = result.render()
    return rendered if len(rendered) <= MAX_CHARS else _tail(rendered)
