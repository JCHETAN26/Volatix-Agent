"""Validator node -- Phase 4.

Invokes the ``run_test_suite`` MCP tool against the Docker sandbox and records
``test_output`` and ``test_passed``.
"""

from agent.state import AgentState


def validator_node(state: AgentState) -> AgentState:
    raise NotImplementedError("Validator node lands in Phase 4.")
