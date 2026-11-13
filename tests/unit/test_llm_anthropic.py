"""The Anthropic client against a stand-in SDK object. No request leaves the machine."""

from typing import Any

import anthropic
import httpx2
import pytest
from anthropic.types import Message

from patchpilot.agent.prompts import SYSTEM_PROMPT, tool_specs
from patchpilot.agent.proposal import PROPOSAL_SCHEMA
from patchpilot.llm.anthropic_client import AnthropicModel
from patchpilot.llm.base import ModelError, ModelRequest

REQUEST = ModelRequest(
    model="claude-sonnet-5-5",
    system=SYSTEM_PROMPT,
    messages=({"role": "user", "content": "find the bug"},),
    tools=tool_specs(),
    output_schema=PROPOSAL_SCHEMA,
    max_tokens=16_000,
    effort="medium",
)
THINKING = {"type": "thinking", "thinking": "", "signature": "opaque-signature"}
TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.py"}}


def message(content: list[dict[str, Any]], stop_reason: str, **usage: int) -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 30, **usage},
        }
    )


class FakeMessages:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.created: list[dict[str, Any]] = []
        self.counted: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.created.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def count_tokens(self, **kwargs: Any) -> Any:
        self.counted.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return type("Count", (), {"input_tokens": 1000})()


class FakeClient:
    def __init__(self, result: Any) -> None:
        self.messages = FakeMessages(result)


def test_the_request_uses_the_settings_this_model_accepts() -> None:
    client = FakeClient(message([{"type": "text", "text": "{}"}], "end_turn"))

    AnthropicModel(client).complete(REQUEST)

    (sent,) = client.messages.created
    assert sent["model"] == "claude-sonnet-5-5" and sent["max_tokens"] == 16_000
    assert sent["thinking"] == {"type": "adaptive"}
    assert sent["output_config"]["effort"] == "medium"
    assert sent["output_config"]["format"] == {"type": "json_schema", "schema": PROPOSAL_SCHEMA}
    assert [tool["name"] for tool in sent["tools"]] == [
        "list_files",
        "read_file",
        "search_code",
        "outline",
    ]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Rejected by this model, or deliberately not used:
    for absent in ("temperature", "top_p", "top_k", "tool_choice", "fallbacks", "betas"):
        assert absent not in sent


def test_blocks_come_back_as_received_so_they_can_be_returned_unchanged() -> None:
    client = FakeClient(message([THINKING, TOOL_USE], "tool_use", cache_read_input_tokens=90))

    turn = AnthropicModel(client).complete(REQUEST)

    assert turn.stop_reason == "tool_use"
    assert list(turn.content) == [THINKING, TOOL_USE]
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "a.py"}
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (120, 30)
    assert turn.usage.cache_read_input_tokens == 90
    assert turn.model == "claude-sonnet-5-5"


def test_the_final_text_is_joined_from_text_blocks_only() -> None:
    client = FakeClient(message([THINKING, {"type": "text", "text": '{"a": 1}'}], "end_turn"))

    assert AnthropicModel(client).complete(REQUEST).text == '{"a": 1}'


@pytest.mark.parametrize(
    "reported,expected",
    [("refusal", "refusal"), ("max_tokens", "max_tokens"), ("pause_turn", "other")],
)
def test_stop_reasons_are_mapped(reported: str, expected: str) -> None:
    client = FakeClient(message([{"type": "text", "text": ""}], reported))

    assert AnthropicModel(client).complete(REQUEST).stop_reason == expected


def test_token_counting_adds_a_safety_margin_and_sends_no_output_limit() -> None:
    client = FakeClient(None)

    assert AnthropicModel(client).count_input_tokens(REQUEST) == 1356
    assert "max_tokens" not in client.messages.counted[0]


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> anthropic.APIStatusError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request)
    return cls("sk-ant-should-not-be-echoed in the message", response=response, body=None)


@pytest.mark.parametrize(
    "error,kind",
    [
        (_status_error(anthropic.RateLimitError, 429), "rate_limited"),
        (_status_error(anthropic.BadRequestError, 400), "api_error"),
        (_status_error(anthropic.AuthenticationError, 401), "api_error"),
        (_status_error(anthropic.InternalServerError, 500), "connection"),
        (
            anthropic.APITimeoutError(httpx2.Request("POST", "https://api.anthropic.com")),
            "timeout",
        ),
        (
            anthropic.APIConnectionError(
                request=httpx2.Request("POST", "https://api.anthropic.com")
            ),
            "connection",
        ),
    ],
)
def test_sdk_errors_become_model_errors_without_echoing_details(
    error: Exception, kind: str
) -> None:
    for call in ("complete", "count_input_tokens"):
        with pytest.raises(ModelError) as caught:
            getattr(AnthropicModel(FakeClient(error)), call)(REQUEST)

        assert caught.value.kind == kind
        assert "sk-ant" not in str(caught.value)
        assert caught.value.__cause__ is None


def test_the_real_client_is_built_without_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key-for-construction-only")

    model = AnthropicModel()

    assert model._client.max_retries == 0
