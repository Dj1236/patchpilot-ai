"""A ``ModelClient`` for one local open-weight model served by Ollama.

Local inference has no per-request charge, so the model is priced at zero in
the ledger; every request is still reserved and settled like any other, and
every run limit applies. Only the local Ollama server is contacted: the
address is fixed here, never taken from settings or model output.

The agent's messages are in the first provider's shape. They are translated
into Ollama's chat format: tool results become ``tool`` messages, and the
model's own messages go back exactly as received.

Ollama's ``format`` option (a JSON schema for the answer) would also stop
the model from calling tools, and every agent request offers both. The
answer schema is therefore given in the system prompt, and the answer is
validated by PatchPilot exactly as for any provider.
"""

import hashlib
import json
from typing import Any

import httpx

from patchpilot.llm.base import ModelError, ModelRequest, ModelTurn, StopReason, ToolCall, Usage

OLLAMA_URL = "http://127.0.0.1:11434"
CONTEXT_TOKENS = 32_768
MAX_RESPONSE_BYTES = 2_000_000
_BLOCK = "ollama_message"


def _schema_instruction(schema: dict[str, Any]) -> str:
    return (
        "\n\nWhen you give your final answer, reply with one JSON object and nothing else "
        "(no code fence, no comment). It must match this JSON schema:\n"
        + json.dumps(schema, sort_keys=True)
    )


def _messages(request: ModelRequest) -> list[dict[str, Any]]:
    """Translate the agent's messages into Ollama chat messages."""
    system = request.system
    if request.output_schema is not None:
        system += _schema_instruction(request.output_schema)
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    names: dict[str, str] = {}
    for message in request.messages:
        content = message["content"]
        if message["role"] == "assistant":
            for block in content:
                if block.get("type") != _BLOCK:
                    raise ModelError("api_error", "an assistant block has no Ollama equivalent")
                for call in block.get("calls", ()):
                    names[call["id"]] = call["name"]
                out.append(dict(block["message"]))
        elif isinstance(content, str):
            out.append({"role": "user", "content": content})
        else:
            for block in content:
                if block.get("type") != "tool_result":
                    raise ModelError("api_error", "a message block has no Ollama equivalent")
                value = block["content"]
                text = value if isinstance(value, str) else json.dumps(value)
                result: dict[str, Any] = {"role": "tool", "content": text}
                if block["tool_use_id"] in names:
                    result["tool_name"] = names[block["tool_use_id"]]
                out.append(result)
    return out


def _unfenced(text: str) -> str:
    """Remove one code fence around the whole answer; the content is not changed."""
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```") and stripped.count("```") == 2:
        body = stripped[3:-3]
        first_newline = body.find("\n")
        if first_newline != -1 and body[:first_newline].strip() in ("", "json"):
            return body[first_newline + 1 :].strip()
    return text


def _tool_call(raw: object, digest: str, index: int) -> ToolCall:
    function = raw.get("function") if isinstance(raw, dict) else None
    arguments = function.get("arguments") if isinstance(function, dict) else None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = None
    name = function.get("name") if isinstance(function, dict) else None
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise ModelError("api_error", "the local model sent a malformed tool call")
    return ToolCall(id=f"call-{digest}-{index}", name=name, arguments=arguments)


class OllamaModel:
    """A ``ModelClient`` for the local Ollama chat endpoint."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 900.0,
        transport: httpx.BaseTransport | None = None,
        think: bool = False,
    ) -> None:
        self._client = httpx.Client(
            base_url=OLLAMA_URL, timeout=timeout_seconds, transport=transport, trust_env=False
        )
        self.think = think

    def body(self, request: ModelRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": _messages(request),
            "stream": False,
            "think": self.think,
            "options": {
                "num_predict": request.max_tokens,
                "num_ctx": CONTEXT_TOKENS,
                "temperature": 0,
            },
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        return body

    def count_input_tokens(self, request: ModelRequest) -> int:
        """Estimate input tokens from the request size; no tokenizer endpoint is used."""
        return len(json.dumps(self.body(request)).encode()) // 3 + 1_000

    def complete(self, request: ModelRequest) -> ModelTurn:
        try:
            response = self._client.post("/api/chat", json=self.body(request))
        except httpx.TimeoutException:
            raise ModelError("timeout", "the local model did not answer in time") from None
        except httpx.TransportError as error:
            raise ModelError(
                "connection", f"Ollama is not reachable at {OLLAMA_URL} ({type(error).__name__})"
            ) from None
        if response.status_code == 404:
            raise ModelError("not_configured", f"model {request.model!r} is not pulled in Ollama")
        if response.status_code != 200:
            raise ModelError("api_error", f"Ollama answered HTTP {response.status_code}")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ModelError("api_error", "the local model's answer is too large")
        try:
            data = response.json()
            message = data["message"]
            reported = str(data.get("model", ""))
        except (ValueError, KeyError, TypeError):
            raise ModelError("api_error", "Ollama's answer is not in the expected format") from None
        if reported != request.model:
            raise ModelError("api_error", "Ollama answered with a different model")

        kept: dict[str, Any] = {"role": "assistant", "content": str(message.get("content") or "")}
        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            kept["tool_calls"] = raw_calls
        digest = hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest()[:12]
        calls = [_tool_call(raw, digest, index) for index, raw in enumerate(raw_calls)]

        stop: StopReason
        if calls:
            stop = "tool_use"
        elif data.get("done_reason") == "length":
            stop = "max_tokens"
        elif data.get("done_reason") == "stop":
            stop = "end_turn"
        else:
            stop = "other"
        block = {
            "type": _BLOCK,
            "message": kept,
            "calls": [{"id": call.id, "name": call.name} for call in calls],
        }
        return ModelTurn(
            stop_reason=stop,
            content=(block,),
            text=_unfenced(kept["content"]),
            tool_calls=tuple(calls),
            usage=Usage(
                input_tokens=int(data.get("prompt_eval_count") or 0),
                output_tokens=int(data.get("eval_count") or 0),
            ),
            model=reported,
            request_id=f"ollama-{digest}",
        )
