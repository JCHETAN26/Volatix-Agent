"""Docker sandbox smoke test -- fills in once SandboxRunner lands in Phase 2."""

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skip(reason="SandboxRunner is implemented in Phase 2")
def test_runner_executes_command_in_container():
    raise AssertionError("placeholder")
