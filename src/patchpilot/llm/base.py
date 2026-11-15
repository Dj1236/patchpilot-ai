"""The provider-neutral shape of one model turn.

Messages and content blocks are kept as plain dictionaries. The blocks of an
assistant turn are in the provider's wire format and must be sent back
exactly as they were received, thinking blocks and signatures included. The
agent writes its own messages (the task and the tool results) in the shape
of the first provider; an adapter for another provider translates them.
"""

from typing import Any, Literal, Protocol

from pydantic import Field

from patchpilot.sandbox.models import FrozenModel

StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]
ErrorKind = Literal[
    "timeout",
    "rate_limited",
    "quota_exhausted",
    "attempt_limit",
    "not_configured",
    "unavailable",
    "connection",
    "api_error",
]
Provider = Literal["anthropic", "gemini", "ollama", "groq"]

# The default model of each provider.
PINNED_MODELS: dict[Provider, str] = {
    "anthropic": "claude-sonnet-5-5",
    "gemini": "gemini-3.8-flash",
    # A local open-weight model, served by Ollama on this machine.
    "ollama": "qwen3:8b",
    # Hosted on Groq's free API tier; a production model with tool use.
    "groq": "openai/gpt-oss-120b",
}

# The only models a run may use. This is a list to choose from, not a
# fallback order: the owner selects one model, and a run uses that model or
# it stops. A model is added here only after its documentation, free-tier
# limits and price have been checked.
APPROVED_MODELS: dict[Provider, tuple[str, ...]] = {
    "anthropic": ("claude-sonnet-5-5",),
    "gemini": ("gemini-3.8-flash", "gemini-3.6-flash"),
    "ollama": ("qwen3:8b",),
    "groq": ("openai/gpt-oss-120b",),
}


class ToolSpec(FrozenModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class Usage(FrozenModel):
    """Token counts as reported by the provider for one response."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)

    def plus(self, other: "Usage") -> "Usage":
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
        )


class ToolCall(FrozenModel):
    id: str
    name: str
    arguments: dict[str, Any]


class ModelRequest(FrozenModel):
    model: str
    system: str
    messages: tuple[dict[str, Any], ...]
    tools: tuple[ToolSpec, ...] = ()
    output_schema: dict[str, Any] | None = None
    max_tokens: int = Field(ge=1, le=128_000)
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"


class ModelTurn(FrozenModel):
    """One response. ``content`` is the raw block list, to be passed back unchanged."""

    stop_reason: StopReason
    content: tuple[dict[str, Any], ...]
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = Usage()
    model: str = ""
    request_id: str | None = None


class ModelError(Exception):
    """A request failed. It may still have been billed."""

    def __init__(self, kind: ErrorKind, message: str, *, retry_after_seconds: float = 0.0) -> None:
        super().__init__(message)
        self.kind = kind
        # How long to wait before the one retry, when the provider's limit
        # window makes an immediate retry pointless.
        self.retry_after_seconds = retry_after_seconds


class ModelClient(Protocol):
    def count_input_tokens(self, request: ModelRequest) -> int:
        """Return an upper estimate of the input tokens ``request`` will use."""
        ...

    def complete(self, request: ModelRequest) -> ModelTurn:
        """Send one request. Raise ``ModelError`` on failure. No hidden retries."""
        ...
