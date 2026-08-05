"""Executor node -- Phase 4.

Reads target files through MCP tools, applies code modifications, and records the
result in ``state["modified_files"]``. On a retry pass it also consumes
``state["error_summary"]`` produced by the stack-trace parser.
"""

from agent.state import AgentState


def executor_node(state: AgentState) -> AgentState:
    raise NotImplementedError("Executor node lands in Phase 4.")
