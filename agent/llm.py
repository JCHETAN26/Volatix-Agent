"""Claude client used by every graph node -- Phase 4.

One place to hold model choice, effort, and usage accounting so the nodes stay about
reasoning rather than API mechanics.

Notes on this model's request surface:
  * ``temperature``/``top_p``/``top_k`` are rejected on Claude Opus 5 -- steer with the
    prompt and ``effort`` instead.
  * Thinking is on by default; it is set explicitly here so the intent is visible.
  * ``max_tokens`` caps thinking *plus* response text, so it is sized generously.
  * A safety classifier can decline a request with ``stop_reason == "refusal"`` and an
    empty ``content``. Reading ``content[0]`` without checking would raise IndexError.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import anthropic

DEFAULT_MODEL = "claude-opus-5"

# xhigh is the recommended setting for coding and agentic work; planning is a single
# well-scoped reasoning step, so it runs one notch lower.
PLANNER_EFFORT = "high"
EXECUTOR_EFFORT = "xhigh"

DEFAULT_MAX_TOKENS = 16_000


class LLMError(RuntimeError):
    """Raised when the model declines a request or returns nothing usable."""


@dataclass
class TokenUsage:
    """Running token totals, for the Phase 7 token-efficiency metric.

    ``input_tokens`` counts only the *uncached* remainder, so the true prompt size is
    ``input + cache_creation + cache_read``. Reporting the three separately is what makes
    the effect of prompt caching visible in the benchmark.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    calls: int = 0

    def add(self, usage: Any) -> None:
        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_creation_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @property
    def total(self) -> int:
        """Every prompt token processed, cached or not, plus output."""
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
            + self.output_tokens
        )

    @property
    def billable_input(self) -> float:
        """Input tokens weighted by what each kind actually costs.

        Cache reads bill at ~0.1x and 5-minute writes at ~1.25x, so a raw token count
        overstates the cost of a cache-heavy run.
        """
        return self.input_tokens + self.cache_creation_tokens * 1.25 + self.cache_read_tokens * 0.1

    def as_dict(self) -> Dict[str, float]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "calls": self.calls,
            "total_tokens": self.total,
            "billable_input_tokens": round(self.billable_input, 1),
        }


@dataclass
class ClaudeClient:
    """Thin wrapper over the Anthropic SDK with per-run usage accounting."""

    model: str = DEFAULT_MODEL
    max_tokens: int = DEFAULT_MAX_TOKENS
    client: Optional[anthropic.Anthropic] = None
    usage: TokenUsage = field(default_factory=TokenUsage)

    def __post_init__(self) -> None:
        if self.client is None:
            # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login`
            # profile, in that order.
            self.client = anthropic.Anthropic()

    def complete(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        effort: str = "high",
        tools: Optional[List[Dict[str, Any]]] = None,
        output_schema: Optional[Dict[str, Any]] = None,
        cache_prompt: bool = True,
    ) -> Any:
        """Send one request and return the raw Message.

        With ``cache_prompt``, a breakpoint is placed on the last system block. Requests
        render as tools -> system -> messages, so that one breakpoint caches the tool
        schemas *and* the system prompt together -- the whole stable prefix that the
        Executor would otherwise re-send on every tool iteration.

        Raises:
            LLMError: The model refused the request.
        """
        request: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system_blocks(system, cache=cache_prompt),
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        if tools:
            request["tools"] = tools
        if output_schema is not None:
            request["output_config"]["format"] = {
                "type": "json_schema",
                "schema": output_schema,
            }

        response = self.client.messages.create(**request)
        self.usage.add(response.usage)

        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) if detail else None
            raise LLMError(f"Model declined the request (category={category}).")
        return response


def system_blocks(system: str, cache: bool = True) -> List[Dict[str, Any]]:
    """Render the system prompt as blocks, optionally marking a cache breakpoint."""
    block: Dict[str, Any] = {"type": "text", "text": system}
    if cache:
        block["cache_control"] = {"type": "ephemeral"}
    return [block]


def cacheable_text(text: str) -> List[Dict[str, Any]]:
    """A user-message content block carrying a cache breakpoint.

    Used for the Executor's opening turn: the plan and issue never change across tool
    iterations, so caching them keeps the whole growing conversation's prefix warm.
    """
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def text_of(response: Any) -> str:
    """Concatenate the text blocks of a response, ignoring thinking and tool blocks."""
    return "".join(block.text for block in response.content if block.type == "text")


def tool_uses(response: Any) -> List[Any]:
    """Every tool_use block in a response, in order."""
    return [block for block in response.content if block.type == "tool_use"]
