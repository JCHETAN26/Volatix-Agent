"""Router branching logic -- the core of the self-correction loop."""

import pytest

from agent.router import ROUTE_FAIL, ROUTE_FINALIZE, ROUTE_RETRY, route_after_validation
from agent.state import build_initial_state


def make_state(*, test_passed: bool, retry_count: int, max_retries: int = 3):
    state = build_initial_state("bug", "/repo", max_retries=max_retries)
    state["test_passed"] = test_passed
    state["retry_count"] = retry_count
    return state


def test_passing_tests_route_to_finalize():
    state = make_state(test_passed=True, retry_count=0)
    assert route_after_validation(state) == ROUTE_FINALIZE


def test_passing_tests_finalize_even_at_retry_ceiling():
    state = make_state(test_passed=True, retry_count=3)
    assert route_after_validation(state) == ROUTE_FINALIZE


@pytest.mark.parametrize("retry_count", [0, 1, 2])
def test_failure_below_ceiling_routes_to_retry(retry_count):
    state = make_state(test_passed=False, retry_count=retry_count)
    assert route_after_validation(state) == ROUTE_RETRY


@pytest.mark.parametrize("retry_count", [3, 4])
def test_failure_at_or_above_ceiling_routes_to_fail(retry_count):
    state = make_state(test_passed=False, retry_count=retry_count)
    assert route_after_validation(state) == ROUTE_FAIL


def test_zero_max_retries_fails_immediately():
    state = make_state(test_passed=False, retry_count=0, max_retries=0)
    assert route_after_validation(state) == ROUTE_FAIL
