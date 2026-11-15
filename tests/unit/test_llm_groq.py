"""The Groq adapter, against a local transport. No request leaves the machine."""

import json
from typing import Any

import httpx
import pytest

from patchpilot.agent.baseline import RunLimits
from patchpilot.agent.trace import secrets_from_environment
from patchpilot.llm.base import APPROVED_MODELS, PINNED_MODELS, ModelError, ModelRequest, ToolSpec
from patchpilot.llm.budget import prices_for
from patchpilot.llm.groq_client import GROQ_URL, GroqModel

MODEL = "openai/gpt-oss-120b"
KEY = "gsk_test_not_a_real_key_0123456789"
TOOL = ToolSpec(name="read_file", description="Read a file.", input_schema={"type": "object"})
SCHEMA = {"type": "object", "required": ["summary"]}
FIRST: tuple[dict[str, Any], ...] = ({"role": "user", "content": "Fix it."},)


def _request(messages: tuple[dict[str, Any], ...] = FIRST) -> ModelRequest:
    return ModelRequest(
        model=MODEL,
        system="You repair code.",
        messages=messages,
        tools=(TOOL,),
        output_schema=SCHEMA,
        max_tokens=4096,
    )


class Server:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    def body(self, index: int = 0) -> dict[str, Any]:
        return dict(json.loads(self.requests[index].content))

    def model(self) -> GroqModel:
        return GroqModel(transport=httpx.MockTransport(self), api_key=KEY, tokens_per_minute=10**6)


def _answer(
    content: str = "", calls: list[Any] | None = None, finish: str = "stop", model: str = MODEL
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content, "reasoning": "hidden"}
    if calls is not None:
        message["tool_calls"] = calls
    return {
        "id": "chatcmpl-1",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def _call(call_id: str = "fc_1", arguments: str = '{"path": "src/a.py"}') -> dict[str, Any]:
    function = {"name": "read_file", "arguments": arguments}
    return {"id": call_id, "type": "function", "function": function}


def test_request_shape_key_header_and_answer() -> None:
    server = Server(_answer('{"summary": "ok"}'))
    turn = server.model().complete(_request())
    request = server.requests[0]
    assert str(request.url) == f"{GROQ_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    body = server.body()
    assert KEY not in json.dumps(body)
    assert body["model"] == MODEL and body["temperature"] == 0 and body["stream"] is False
    assert body["max_completion_tokens"] == 4096 and body["reasoning_effort"] == "medium"
    assert body["tools"][0]["function"]["name"] == "read_file"
    assert json.dumps(SCHEMA, sort_keys=True) in body["messages"][0]["content"]
    assert turn.stop_reason == "end_turn" and turn.text == '{"summary": "ok"}'
    assert (turn.usage.input_tokens, turn.usage.output_tokens) == (120, 30)


def test_tool_calls_round_trip_with_their_ids_and_without_reasoning() -> None:
    server = Server(_answer(calls=[_call()], finish="tool_calls"), _answer('{"summary": "ok"}'))
    model = server.model()
    first = model.complete(_request())
    assert first.stop_reason == "tool_use"
    assert first.tool_calls[0].id == "fc_1"
    assert first.tool_calls[0].arguments == {"path": "src/a.py"}
    follow_up = (
        *FIRST,
        {"role": "assistant", "content": list(first.content)},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "fc_1", "content": "x = 1"}],
        },
    )
    model.complete(_request(follow_up))
    sent = server.body(1)["messages"]
    assert sent[2]["tool_calls"][0]["id"] == "fc_1" and "reasoning" not in sent[2]
    assert sent[3] == {"role": "tool", "tool_call_id": "fc_1", "content": "x = 1"}


@pytest.mark.parametrize("arguments", ["{not json", "[1]"])
def test_malformed_tool_calls_are_refused(arguments: str) -> None:
    server = Server(_answer(calls=[_call(arguments=arguments)], finish="tool_calls"))
    with pytest.raises(ModelError, match="malformed"):
        server.model().complete(_request())


def test_a_different_model_is_refused() -> None:
    server = Server(_answer("{}", model="llama-3.1-8b-instant"))
    with pytest.raises(ModelError, match="different model"):
        server.model().complete(_request())


def _error(status: int, message: str, headers: dict[str, str] | None = None) -> httpx.Response:
    body = {"error": {"message": message, "code": "rate_limit_exceeded"}}
    return httpx.Response(status, json=body, headers=headers)


@pytest.mark.parametrize(
    ("response", "kind", "wait"),
    [
        (_error(429, "tokens per minute (TPM)", {"retry-after": "7"}), "rate_limited", 7),
        (_error(429, "on TPM: Please try again in 1m30.5s."), "rate_limited", 60),
        (_error(429, "on tokens per day (TPD): Limit 200000"), "quota_exhausted", 0),
        (_error(401, "Invalid API Key"), "not_configured", 0),
        (_error(413, "Request too large"), "api_error", 0),
        (_error(503, "over capacity"), "unavailable", 30),
    ],
)
def test_errors_are_classified(response: httpx.Response, kind: str, wait: float) -> None:
    with pytest.raises(ModelError) as caught:
        Server(response).model().complete(_request())
    assert caught.value.kind == kind
    assert caught.value.retry_after_seconds == wait
    assert KEY not in str(caught.value)


def test_no_key_means_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ModelError, match="GROQ_API_KEY") as caught:
        GroqModel()
    assert caught.value.kind == "not_configured"


def test_one_approved_model_bounded_output_and_redacted_key() -> None:
    assert PINNED_MODELS["groq"] == MODEL and APPROVED_MODELS["groq"] == (MODEL,)
    limits = RunLimits.for_provider("groq")
    assert limits.model == MODEL and limits.max_output_tokens == 2048
    assert prices_for(MODEL).input > 0, "the ledger keeps a list-price upper bound"
    assert secrets_from_environment({"GROQ_API_KEY": KEY}) == (KEY,)


def test_requests_are_paced_to_the_per_minute_token_limit() -> None:
    now = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    server = Server(*[_answer("{}") for _ in range(3)])
    model = GroqModel(
        transport=httpx.MockTransport(server),
        api_key=KEY,
        tokens_per_minute=9_000,
        clock=lambda: now[0],
        sleep=sleep,
    )
    for _ in range(3):
        model.complete(_request())
    assert len(server.requests) == 3
    assert waits == [60.0], "the third request waited for the first to leave the window"
    with pytest.raises(ModelError, match="larger than"):
        GroqModel(
            transport=httpx.MockTransport(server), api_key=KEY, tokens_per_minute=100
        ).complete(_request())
