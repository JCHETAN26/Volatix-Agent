"""Conditional edge logic governing the self-correction loop.

Phase 5 wires these return values to concrete graph nodes; the decision function
itself is pure so it can be unit tested without a live graph.
"""

from agent.state import AgentState

ROUTE_FINALIZE = "finalize"
ROUTE_RETRY = "retry"
ROUTE_FAIL = "fail"


def route_after_validation(state: AgentState) -> str:
    """Decide where the graph goes once the Validator has reported a result.

    Returns one of ``ROUTE_FINALIZE``, ``ROUTE_RETRY``, or ``ROUTE_FAIL``.
    """
    if state["test_passed"]:
        return ROUTE_FINALIZE
    if state["retry_count"] < state["max_retries"]:
        return ROUTE_RETRY
    return ROUTE_FAIL
