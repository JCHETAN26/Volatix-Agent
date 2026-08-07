"""ClaudeClient request shaping and usage accounting."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.llm import (
    DEFAULT_MODEL,
    ClaudeClient,
    LLMError,
    TokenUsage,
    cacheable_text,
    system_blocks,
    text_of,
    tool_uses,
)
from tests.unit.conftest import FakeUsage, message, text_block, tool_block


def sdk_client(response):
    inner = MagicMock()
    inner.messages.create.return_value = response
    return inner


def test_request_uses_the_configured_model_and_adaptive_thinking():
    inner = sdk_client(message([text_block("hi")]))
    ClaudeClient(client=inner).complete(system="s", messages=[{"role": "user", "content": "q"}])

    kwargs = inner.messages.create.call_args.kwargs
    assert kwargs["model"] == DEFAULT_MODEL
    assert kwargs["thinking"] == {"type": "adaptive"}


def test_sampling_parameters_are_never_sent():
    """temperature/top_p/top_k are rejected by this model -- sending one 400s the run."""
    inner = sdk_client(message([text_block("hi")]))
    ClaudeClient(client=inner).complete(system="s", messages=[])

    kwargs = inner.messages.create.call_args.kwargs
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs
    assert "top_k" not in kwargs


def test_effort_is_passed_through_output_config():
    inner = sdk_client(message([text_block("hi")]))
    ClaudeClient(client=inner).complete(system="s", messages=[], effort="xhigh")

    assert inner.messages.create.call_args.kwargs["output_config"]["effort"] == "xhigh"


def test_output_schema_becomes_a_json_schema_format():
    inner = sdk_client(message([text_block("{}")]))
    schema = {"type": "object", "properties": {}}
    ClaudeClient(client=inner).complete(system="s", messages=[], output_schema=schema)

    fmt = inner.messages.create.call_args.kwargs["output_config"]["format"]
    assert fmt == {"type": "json_schema", "schema": schema}


def test_tools_are_omitted_when_absent():
    inner = sdk_client(message([text_block("hi")]))
    ClaudeClient(client=inner).complete(system="s", messages=[])
    assert "tools" not in inner.messages.create.call_args.kwargs


def test_refusal_raises_rather_than_indexing_empty_content():
    """A refusal returns HTTP 200 with empty content; reading content[0] would crash."""
    refused = SimpleNamespace(
        content=[],
        stop_reason="refusal",
        stop_details=SimpleNamespace(category="cyber"),
        usage=FakeUsage(),
    )
    with pytest.raises(LLMError, match="declined"):
        ClaudeClient(client=sdk_client(refused)).complete(system="s", messages=[])


def test_usage_accumulates_across_calls():
    inner = sdk_client(message([text_block("hi")]))
    client = ClaudeClient(client=inner)

    client.complete(system="s", messages=[])
    client.complete(system="s", messages=[])

    assert client.usage.calls == 2
    assert client.usage.input_tokens == 200
    assert client.usage.total == 300


def test_usage_is_recorded_even_for_a_refusal():
    """A pre-output refusal is unbilled, but a mid-stream one is not -- record either way."""
    refused = SimpleNamespace(
        content=[], stop_reason="refusal", stop_details=None, usage=FakeUsage()
    )
    client = ClaudeClient(client=sdk_client(refused))
    with pytest.raises(LLMError):
        client.complete(system="s", messages=[])
    assert client.usage.calls == 1


def test_token_usage_as_dict():
    usage = TokenUsage()
    usage.add(FakeUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=3))
    assert usage.as_dict() == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 3,
        "cache_creation_tokens": 0,
        "calls": 1,
        "total_tokens": 18,
        "billable_input_tokens": 10.3,
    }


def test_total_counts_cached_prompt_tokens():
    """input_tokens is only the uncached remainder, so a naive sum under-reports."""
    usage = TokenUsage()
    usage.add(FakeUsage(input_tokens=10, output_tokens=5, cache_read_input_tokens=1000))
    assert usage.total == 1015


def test_billable_input_discounts_cache_reads():
    """A cache-heavy run costs far less than its raw token count suggests."""
    cached = TokenUsage()
    cached.add(FakeUsage(input_tokens=0, output_tokens=0, cache_read_input_tokens=1000))
    uncached = TokenUsage()
    uncached.add(FakeUsage(input_tokens=1000, output_tokens=0))

    assert cached.total == uncached.total == 1000
    assert cached.billable_input == 100.0
    assert uncached.billable_input == 1000.0


# --- prompt caching ---------------------------------------------------------------


def test_system_prompt_carries_a_cache_breakpoint():
    """Requests render tools -> system -> messages, so this caches both."""
    inner = sdk_client(message([text_block("hi")]))
    ClaudeClient(client=inner).complete(system="stable prompt", messages=[])

    system = inner.messages.create.call_args.kwargs["system"]
    assert system[0]["text"] == "stable prompt"
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_caching_can_be_disabled():
    inner = sdk_client(message([text_block("hi")]))
    ClaudeClient(client=inner).complete(system="s", messages=[], cache_prompt=False)

    assert "cache_control" not in inner.messages.create.call_args.kwargs["system"][0]


def test_system_blocks_helper():
    assert system_blocks("x", cache=False) == [{"type": "text", "text": "x"}]
    assert system_blocks("x")[0]["cache_control"] == {"type": "ephemeral"}


def test_cacheable_text_marks_a_breakpoint():
    blocks = cacheable_text("plan goes here")
    assert blocks[0]["text"] == "plan goes here"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}


def test_token_usage_tolerates_missing_fields():
    usage = TokenUsage()
    usage.add(SimpleNamespace())
    assert usage.total == 0


def test_text_of_ignores_non_text_blocks():
    msg = message([text_block("a"), tool_block("t", {}), text_block("b")])
    assert text_of(msg) == "ab"


def test_tool_uses_returns_only_tool_blocks():
    msg = message([text_block("a"), tool_block("t", {"x": 1})])
    assert [b.name for b in tool_uses(msg)] == ["t"]
