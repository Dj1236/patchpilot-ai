"""A ``ModelClient`` for one model on Groq's free API tier.

Groq serves an OpenAI-style chat completions endpoint. The agent's messages
are translated into that format: tool results become ``tool`` messages that
name the call they answer, and the model's own messages go back exactly as
received, without its reasoning text.

The key is read from ``GROQ_API_KEY`` in the process environment and sent
only in the ``Authorization`` header to the fixed address below. A rate
limit with a known wait is retryable by the agent's bounded loop; a daily
quota stops the run. There is no fallback to another model.
"""

import contextlib
import hashlib
import json
import os
import re
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import httpx

from patchpilot.llm.base import ModelError, ModelRequest, ModelTurn, StopReason, ToolCall, Usage
from patchpilot.llm.ollama_client import MAX_RESPONSE_BYTES, _schema_instruction, _unfenced

GROQ_URL = "https://api.groq.com/openai/v1"
_BLOCK = "groq_message"
_MAX_RETRY_AFTER_SECONDS = 60.0
_EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}
# Groq's free tier allows 8,000 tokens a minute and counts each request's
# prompt plus its requested output. A little is kept back for estimate error.
TOKENS_PER_MINUTE = 7_500
_WINDOW_SECONDS = 60.0
_WAIT = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s")


def _messages(request: ModelRequest) -> list[dict[str, Any]]:
    """Translate the agent's messages into chat completion messages."""
    system = request.system
    if request.output_schema is not None:
        system += _schema_instruction(request.output_schema)
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in request.messages:
        content = message["content"]
        if message["role"] == "assistant":
            for block in content:
                if block.get("type") != _BLOCK:
                    raise ModelError("api_error", "an assistant block has no Groq equivalent")
                out.append(dict(block["message"]))
        elif isinstance(content, str):
            out.append({"role": "user", "content": content})
        else:
            for block in content:
                if block.get("type") != "tool_result":
                    raise ModelError("api_error", "a message block has no Groq equivalent")
                value = block["content"]
                text = value if isinstance(value, str) else json.dumps(value)
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": text})
    return out


def _tool_call(raw: object) -> ToolCall:
    function = raw.get("function") if isinstance(raw, dict) else None
    call_id = raw.get("id") if isinstance(raw, dict) else None
    name = function.get("name") if isinstance(function, dict) else None
    arguments: object = function.get("arguments") if isinstance(function, dict) else None
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            arguments = None
    if not (isinstance(call_id, str) and isinstance(name, str) and isinstance(arguments, dict)):
        raise ModelError("api_error", "the model sent a malformed tool call")
    return ToolCall(id=call_id, name=name, arguments=arguments)


def _code(body: str) -> str:
    try:
        return str((json.loads(body).get("error") or {}).get("code", ""))
    except (ValueError, AttributeError):
        return ""


def failure(status: int, body: str, retry_after: str | None) -> ModelError:
    """Map an error answer to a ``ModelError``. The body is never echoed in full."""
    try:
        error = json.loads(body).get("error") or {}
        message, code = str(error.get("message", "")), str(error.get("code", ""))
    except (ValueError, AttributeError):
        message, code = "", ""
    if status == 401:
        return ModelError("not_configured", "Groq refused the key (HTTP 401)")
    if status == 429 and ("per day" in message or "(TPD)" in message or "(RPD)" in message):
        return ModelError("quota_exhausted", "the Groq daily free quota is used up")
    if status == 429:
        wait = _MAX_RETRY_AFTER_SECONDS
        found = _WAIT.search(message)
        if retry_after:
            with contextlib.suppress(ValueError):
                wait = float(retry_after)
        elif found:
            wait = int(found.group(1) or 0) * 60 + float(found.group(2))
        return ModelError(
            "rate_limited",
            "Groq's per-minute free limit was reached",
            retry_after_seconds=min(max(wait, 1.0), _MAX_RETRY_AFTER_SECONDS),
        )
    if status == 413:
        return ModelError("api_error", "the request is larger than Groq's free per-minute limit")
    if status in (500, 502, 503):
        return ModelError("unavailable", f"Groq answered HTTP {status}", retry_after_seconds=30.0)
    return ModelError("api_error", f"Groq answered HTTP {status} ({code[:40] or 'no code'})")


class GroqModel:
    """A ``ModelClient`` for Groq chat completions."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 300.0,
        transport: httpx.BaseTransport | None = None,
        api_key: str | None = None,
        tokens_per_minute: int = TOKENS_PER_MINUTE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("GROQ_API_KEY", "")
        if not key.strip():
            raise ModelError(
                "not_configured",
                "no credential is configured; set GROQ_API_KEY in the environment that "
                "starts PatchPilot",
            )
        self._client = httpx.Client(
            base_url=GROQ_URL,
            timeout=timeout_seconds,
            transport=transport,
            trust_env=False,
            headers={"Authorization": f"Bearer {key.strip()}"},
        )
        self._per_minute = tokens_per_minute
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[tuple[float, int]] = deque()

    def _pace(self, tokens: int) -> None:
        """Wait until the last minute's requests leave room for this one."""
        if tokens > self._per_minute:
            raise ModelError("api_error", "the request is larger than Groq's free per-minute limit")
        while True:
            now = self._clock()
            while self._sent and self._sent[0][0] <= now - _WINDOW_SECONDS:
                self._sent.popleft()
            if sum(used for _, used in self._sent) + tokens <= self._per_minute:
                self._sent.append((now, tokens))
                return
            self._sleep(self._sent[0][0] + _WINDOW_SECONDS - now)

    def body(self, request: ModelRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": _messages(request),
            "max_completion_tokens": request.max_tokens,
            "temperature": 0,
            "reasoning_effort": _EFFORT[request.effort],
            "stream": False,
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
        """Estimate input tokens from the request size; Groq has no counting endpoint."""
        return len(json.dumps(self.body(request)).encode()) // 3 + 1_000

    def _unusable(self, request: ModelRequest, body: dict[str, Any]) -> ModelTurn:
        """Groq refused the model's own output as a malformed tool call.

        That is a model answer with no usable content, not an outage: the agent
        rejects it and gives feedback like any invalid answer. Groq reports no
        usage, so the request is charged in full at its estimate.
        """
        kept = {"role": "assistant", "content": ""}
        return ModelTurn(
            stop_reason="other",
            content=({"type": _BLOCK, "message": kept},),
            text="",
            tool_calls=(),
            usage=Usage(
                input_tokens=len(json.dumps(body).encode()) // 3 + 1_000,
                output_tokens=request.max_tokens,
            ),
            model=request.model,
            request_id="groq-tool-use-failed",
        )

    def complete(self, request: ModelRequest) -> ModelTurn:
        body = self.body(request)
        # About 4 bytes a token; Groq counts the requested output in full.
        self._pace(len(json.dumps(body).encode()) // 4 + request.max_tokens)
        try:
            response = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException:
            raise ModelError("timeout", "Groq did not answer in time") from None
        except httpx.TransportError as error:
            raise ModelError(
                "connection", f"Groq is not reachable ({type(error).__name__})"
            ) from None
        if response.status_code == 400 and _code(response.text) == "tool_use_failed":
            return self._unusable(request, body)
        if response.status_code != 200:
            raise failure(
                response.status_code, response.text[:4_000], response.headers.get("retry-after")
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ModelError("api_error", "the model's answer is too large")
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
            reported = str(data.get("model", ""))
            usage = data.get("usage") or {}
        except (ValueError, KeyError, TypeError, IndexError):
            raise ModelError("api_error", "Groq's answer is not in the expected format") from None
        if reported != request.model:
            raise ModelError("api_error", "Groq answered with a different model")

        # The reasoning text is not sent back; only the answer and its tool calls are.
        kept: dict[str, Any] = {"role": "assistant", "content": str(message.get("content") or "")}
        raw_calls = message.get("tool_calls") or []
        if raw_calls:
            kept["tool_calls"] = raw_calls
        calls = [_tool_call(raw) for raw in raw_calls]
        digest = hashlib.sha256(json.dumps(kept, sort_keys=True).encode()).hexdigest()[:12]

        finish = choice.get("finish_reason")
        stop: StopReason
        if calls:
            stop = "tool_use"
        elif finish == "length":
            stop = "max_tokens"
        elif finish == "stop":
            stop = "end_turn"
        else:
            stop = "other"
        return ModelTurn(
            stop_reason=stop,
            content=({"type": _BLOCK, "message": kept},),
            text=_unfenced(kept["content"]),
            tool_calls=tuple(calls),
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
            model=reported,
            request_id=str(data.get("id") or f"groq-{digest}"),
        )
