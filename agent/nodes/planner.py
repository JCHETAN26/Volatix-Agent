"""Planner node -- Phase 4.

Consumes the issue description plus the AST symbol map and emits an ordered,
actionable resolution plan into ``state["plan"]``.
"""

from agent.state import AgentState


def planner_node(state: AgentState) -> AgentState:
    raise NotImplementedError("Planner node lands in Phase 4.")
