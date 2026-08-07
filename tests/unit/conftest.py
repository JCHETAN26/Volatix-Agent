"""Fakes for driving the graph without an API key or a Docker daemon."""

import copy
import json
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from mcp_server.workspace import Workspace


class FakeUsage(SimpleNamespace):
    def __init__(self, input_tokens=100, output_tokens=50, cache_read_input_tokens=0):
        super().__init__(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def tool_block(name: str, tool_input: Dict[str, Any], block_id: str = "toolu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def message(content: List[Any], stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=None,
        usage=FakeUsage(),
    )


def plan_message(steps=None, analysis="off-by-one in add()", targets=None):
    """A Planner response, which is JSON text because of the structured-output schema."""
    payload = {
        "analysis": analysis,
        "target_files": targets if targets is not None else ["calc.py"],
        "steps": steps if steps is not None else ["Fix the operator in calc.add"],
    }
    return message([text_block(json.dumps(payload))])


class FakeClaudeClient:
    """Replays a scripted list of responses and records the requests it received."""

    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.requests: List[Dict[str, Any]] = []
        from agent.llm import TokenUsage

        self.usage = TokenUsage()

    def complete(self, system, messages, effort="high", tools=None, output_schema=None):
        if not self._responses:
            raise AssertionError("FakeClaudeClient ran out of scripted responses")
        self.requests.append(
            {
                "system": system,
                # Snapshot: the executor mutates this list in place across iterations, so
                # storing the reference would make every recorded request alias the last.
                "messages": copy.deepcopy(messages),
                "effort": effort,
                "tools": tools,
                "output_schema": output_schema,
            }
        )
        response = self._responses.pop(0)
        self.usage.add(response.usage)
        return response

    @property
    def exhausted(self) -> bool:
        return not self._responses


def prompt_text(request: Dict[str, Any]) -> str:
    """Text of a recorded request's first user turn, which may be blocks or a string."""
    content = request["messages"][0]["content"]
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in content if block.get("type") == "text")


@pytest.fixture
def repo(tmp_path):
    """A tiny broken repository: add() subtracts, and a test asserts it adds."""
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "conftest.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    return tmp_path


@pytest.fixture
def workspace(repo):
    return Workspace(str(repo))
