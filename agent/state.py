"""Central state schema shared by every node in the LangGraph execution graph.

The graph is a single mutable ``AgentState`` threaded through Planner -> Executor ->
Validator, with the router deciding whether to loop back to the Executor on failure.
"""

from typing import Dict, List, Optional, TypedDict

DEFAULT_MAX_RETRIES = 3


class AgentState(TypedDict):
    """State carried across all agent nodes.

    Attributes:
        issue_description: The raw bug report, ticket, or failing error log.
        codebase_path: Absolute path to the repository under repair.
        plan: Ordered, actionable resolution steps produced by the Planner.
        modified_files: Map of file path -> full post-edit contents written by the Executor.
        test_output: Raw stdout/stderr captured from the sandboxed test run.
        test_passed: Whether the most recent validation run succeeded.
        retry_count: Number of self-correction cycles consumed so far.
        max_retries: Ceiling on self-correction cycles before giving up.
        error_summary: Trimmed stack trace injected back into the Executor on failure.
    """

    issue_description: str
    codebase_path: str
    plan: List[str]
    modified_files: Dict[str, str]
    test_output: str
    test_passed: bool
    retry_count: int
    max_retries: int
    error_summary: Optional[str]


def build_initial_state(
    issue_description: str,
    codebase_path: str,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AgentState:
    """Create a fully populated starting state so nodes never read a missing key."""
    return AgentState(
        issue_description=issue_description,
        codebase_path=codebase_path,
        plan=[],
        modified_files={},
        test_output="",
        test_passed=False,
        retry_count=0,
        max_retries=max_retries,
        error_summary=None,
    )
