"""LangGraph state machine -- Phase 4.

    planner -> executor -> validator -+- pass -> finalize -> END
                   ^                  |
                   +----- retry ------+  (increments retry_count, injects the failure)
                                      |
                                      +- budget spent -> give_up -> END

Node callables are built with their dependencies bound, so the graph itself holds no
global state and a test can drive it with a fake client and a fake sandbox.
"""

from functools import partial
from typing import Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agent.llm import ClaudeClient
from agent.nodes.executor import executor_node
from agent.nodes.planner import planner_node
from agent.nodes.validator import validator_node
from agent.router import ROUTE_FAIL, ROUTE_FINALIZE, ROUTE_RETRY, route_after_validation
from agent.state import AgentState
from agent.tools import ToolBackend
from mcp_server.workspace import Workspace
from sandbox.runner import SandboxRunner

# Cap on how much raw pytest output is fed back to the Executor. Phase 5 replaces this
# with a real stack-trace parser; the truncation is a stopgap so the loop is closed and
# testable now rather than a considered summarisation strategy.
MAX_RAW_FEEDBACK_CHARS = 4_000


def _retry_node(state: AgentState) -> dict:
    """Consume one retry and hand the failure evidence to the Executor."""
    output = state["test_output"]
    if len(output) > MAX_RAW_FEEDBACK_CHARS:
        output = output[-MAX_RAW_FEEDBACK_CHARS:]
    return {"retry_count": state["retry_count"] + 1, "error_summary": output}


def _finalize_node(state: AgentState) -> dict:
    """Terminal success node. Phase 6 attaches diff and report generation here."""
    return {}


def _give_up_node(state: AgentState) -> dict:
    """Terminal failure node, reached once the retry budget is exhausted."""
    return {
        "error_summary": (
            f"Gave up after {state['retry_count']} retries. "
            f"Last test output:\n{state['test_output']}"
        )
    }


def build_graph(
    workspace: Workspace,
    client: Optional[ClaudeClient] = None,
    runner: Optional[SandboxRunner] = None,
    backend: Optional[ToolBackend] = None,
    checkpointer: Optional[object] = None,
    test_path: str = "tests/",
):
    """Compile the self-healing graph with its dependencies bound."""
    client = client or ClaudeClient()
    runner = runner or SandboxRunner()
    backend = backend or ToolBackend(workspace)

    graph = StateGraph(AgentState)
    graph.add_node("planner", partial(planner_node, client=client, workspace=workspace))
    graph.add_node("executor", partial(executor_node, client=client, backend=backend))
    graph.add_node("validator", partial(validator_node, runner=runner, test_path=test_path))
    graph.add_node("retry", _retry_node)
    graph.add_node("finalize", _finalize_node)
    graph.add_node("give_up", _give_up_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "validator")
    graph.add_conditional_edges(
        "validator",
        route_after_validation,
        {
            ROUTE_FINALIZE: "finalize",
            ROUTE_RETRY: "retry",
            ROUTE_FAIL: "give_up",
        },
    )
    graph.add_edge("retry", "executor")
    graph.add_edge("finalize", END)
    graph.add_edge("give_up", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())
