"""Initial state must be fully populated so no node reads a missing key."""

from agent.state import DEFAULT_MAX_RETRIES, AgentState, build_initial_state


def test_initial_state_has_every_declared_key():
    state = build_initial_state("TypeError in parser", "/repo")
    assert set(state) == set(AgentState.__annotations__)


def test_initial_state_defaults():
    state = build_initial_state("TypeError in parser", "/repo")
    assert state["issue_description"] == "TypeError in parser"
    assert state["codebase_path"] == "/repo"
    assert state["plan"] == []
    assert state["modified_files"] == {}
    assert state["test_passed"] is False
    assert state["retry_count"] == 0
    assert state["max_retries"] == DEFAULT_MAX_RETRIES
    assert state["error_summary"] is None


def test_max_retries_is_overridable():
    assert build_initial_state("bug", "/repo", max_retries=7)["max_retries"] == 7
