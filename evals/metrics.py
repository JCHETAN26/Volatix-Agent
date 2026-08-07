"""Benchmark result records and aggregation -- Phase 7.

Metrics mirror the build plan: completion rate (Pass@1), convergence speed, token
efficiency, and sandbox latency.
"""

from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional

CONDITION_BASELINE = "baseline"
CONDITION_AGENT = "agent"


@dataclass
class ScenarioResult:
    """The outcome of running one scenario under one condition."""

    scenario_id: str
    category: str
    condition: str
    passed: bool
    retries: int = 0
    wall_ms: float = 0.0
    sandbox_ms: float = 0.0
    sandbox_runs: int = 0
    tokens: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _avg(values: List[float]) -> float:
    return round(mean(values), 1) if values else 0.0


def summarise(results: List[ScenarioResult], condition: str) -> Dict[str, Any]:
    """Aggregate one condition's results into the plan's four metrics."""
    subset = [r for r in results if r.condition == condition]
    if not subset:
        return {"condition": condition, "scenarios": 0}

    passed = [r for r in subset if r.passed]
    return {
        "condition": condition,
        "scenarios": len(subset),
        # Completion rate (Pass@1)
        "passed": len(passed),
        "pass_rate": round(100 * len(passed) / len(subset), 1),
        # Convergence speed: retries are only meaningful where the run succeeded,
        # since a failure always sits at the retry ceiling and would skew the mean.
        "avg_retries_on_success": _avg([r.retries for r in passed]),
        # Token efficiency
        "avg_total_tokens": _avg([r.tokens.get("total_tokens", 0) for r in subset]),
        "avg_billable_input_tokens": _avg(
            [r.tokens.get("billable_input_tokens", 0) for r in subset]
        ),
        "avg_model_calls": _avg([r.tokens.get("calls", 0) for r in subset]),
        "cache_read_tokens": sum(r.tokens.get("cache_read_tokens", 0) for r in subset),
        # Sandbox latency
        "avg_sandbox_ms_per_run": _avg(
            [r.sandbox_ms / r.sandbox_runs for r in subset if r.sandbox_runs]
        ),
        "avg_wall_ms": _avg([r.wall_ms for r in subset]),
        "errors": sum(1 for r in subset if r.error),
    }


def by_category(results: List[ScenarioResult], condition: str) -> Dict[str, Dict[str, Any]]:
    """Pass rate per bug category -- shows which kinds of bug a condition handles."""
    subset = [r for r in results if r.condition == condition]
    categories = sorted({r.category for r in subset})
    output = {}
    for category in categories:
        rows = [r for r in subset if r.category == category]
        wins = sum(1 for r in rows if r.passed)
        output[category] = {
            "scenarios": len(rows),
            "passed": wins,
            "pass_rate": round(100 * wins / len(rows), 1),
        }
    return output


def build_report(results: List[ScenarioResult]) -> Dict[str, Any]:
    """The full metrics payload written to the results file."""
    return {
        "conditions": {
            condition: summarise(results, condition)
            for condition in (CONDITION_BASELINE, CONDITION_AGENT)
        },
        "by_category": {
            condition: by_category(results, condition)
            for condition in (CONDITION_BASELINE, CONDITION_AGENT)
        },
        "results": [r.as_dict() for r in results],
    }
