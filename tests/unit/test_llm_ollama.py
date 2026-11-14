"""The Ollama adapter, against a local mock transport. No server, no network."""

import json
from typing import Any

import httpx
import pytest

from patchpilot.agent.baseline import RunLimits
from patchpilot.config import Settings
from patchpilot.llm.base import APPROVED_MODELS, PINNED_MODELS, ModelError, ModelRequest, ToolSpec
from patchpilot.llm.budget import prices_for
from patchpilot.llm.ollama_client import OLLAMA_URL, OllamaModel

MODEL = "qwen3:8b"
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
        max_tokens=4000,
    )


class Server:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.bodies: list[dict[str, Any]] = []
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.bodies.append(json.loads(request.content))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return httpx.Response(answer, json={"error": "x"})
        return httpx.Response(200, json=answer)

    def model(self) -> OllamaModel:
        return OllamaModel(transport=httpx.MockTransport(self))


def _answer(
    content: str = "", calls: list[Any] | None = None, done: str = "stop", model: str = MODEL
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return {
        "model": model,
        "message": message,
        "done_reason": done,
        "prompt_eval_count": 120,
        "eval_count": 30,
    }


def test_only_the_local_server_is_called_and_the_schema_does_not_block_tools() -> None:
    server = Server(_answer('{"summary": "ok"}'))
    server.model().complete(_request())
    body = server.bodies[0]
    assert server.urls == [f"{OLLAMA_URL}/api/chat"]
    assert "format" not in body, "a format schema would stop the model from calling tools"
    assert body["tools"][0]["function"]["name"] == "read_file"
    assert body["stream"] is False and body["options"]["num_predict"] == 4000
    assert body["options"]["num_ctx"] >= 32_768 and body["options"]["temperature"] == 0
    assert body["messages"][0]["role"] == "system"
    assert json.dumps(SCHEMA, sort_keys=True) in body["messages"][0]["content"]


def test_tool_calls_round_trip_and_results_become_tool_messages() -> None:
    call = {"function": {"name": "read_file", "arguments": {"path": "src/a.py"}}}
    server = Server(_answer(calls=[call]), _answer('{"summary": "done"}'))
    model = server.model()
    turn = model.complete(_request())
    assert turn.stop_reason == "tool_use"
    assert [(c.name, c.arguments) for c in turn.tool_calls] == [("read_file", {"path": "src/a.py"})]
    assert turn.usage.input_tokens == 120 and turn.usage.output_tokens == 30
    assert turn.model == MODEL

    result = {
        "type": "tool_result",
        "tool_use_id": turn.tool_calls[0].id,
        "content": "x = 1",
        "is_error": False,
    }
    messages = (
        FIRST[0],
        {"role": "assistant", "content": list(turn.content)},
        {"role": "user", "content": [result]},
    )
    final = model.complete(_request(messages))
    sent = server.bodies[1]["messages"]
    assert sent[2] == {"role": "assistant", "content": "", "tool_calls": [call]}
    assert sent[3] == {"role": "tool", "content": "x = 1", "tool_name": "read_file"}
    assert final.stop_reason == "end_turn" and json.loads(final.text) == {"summary": "done"}


def test_string_arguments_are_parsed_and_malformed_calls_are_refused() -> None:
    good = {"function": {"name": "outline", "arguments": '{"path": "a.py"}'}}
    turn = Server(_answer(calls=[good])).model().complete(_request())
    assert turn.tool_calls[0].arguments == {"path": "a.py"}
    bad_calls: list[Any] = [
        {"function": {"name": "outline", "arguments": "not json"}},
        {"function": {"arguments": {}}},
        "x",
    ]
    for bad in bad_calls:
        with pytest.raises(ModelError) as error:
            Server(_answer(calls=[bad])).model().complete(_request())
        assert error.value.kind == "api_error"


def test_one_fence_around_the_answer_is_removed_and_nothing_else() -> None:
    fenced = '```json\n{"summary": "ok"}\n```'
    assert Server(_answer(fenced)).model().complete(_request()).text == '{"summary": "ok"}'
    mixed = 'Here it is: ```json\n{"summary": "ok"}\n```'
    assert Server(_answer(mixed)).model().complete(_request()).text == mixed


def test_cut_off_answers_and_other_models_are_reported_not_hidden() -> None:
    cut = Server(_answer("{", done="length")).model().complete(_request())
    assert cut.stop_reason == "max_tokens"
    with pytest.raises(ModelError) as error:
        Server(_answer("{}", model="qwen3:4b")).model().complete(_request())
    assert "different model" in str(error.value)


@pytest.mark.parametrize(
    ("answer", "kind"),
    [
        (404, "not_configured"),
        (500, "api_error"),
        (httpx.ConnectError("refused"), "connection"),
        (httpx.ReadTimeout("slow"), "timeout"),
    ],
)
def test_failures_become_model_errors(answer: Any, kind: str) -> None:
    with pytest.raises(ModelError) as error:
        Server(answer).model().complete(_request())
    assert error.value.kind == kind


def test_the_local_model_is_one_approved_zero_priced_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATCHPILOT_PROVIDER", "ollama")
    settings = Settings(_env_file=None)
    assert settings.selected_model == PINNED_MODELS["ollama"] == MODEL
    assert APPROVED_MODELS["ollama"] == (MODEL,)
    assert RunLimits.for_provider("ollama").model == MODEL
    price = prices_for(MODEL)
    assert (price.input, price.output, price.cache_write, price.cache_read) == (0, 0, 0, 0)
