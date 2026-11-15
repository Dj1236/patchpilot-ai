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
OMITTED = (
    "[earlier tool output omitted to fit Groq's free per-request limit; "
    "call the tool again if you need it]"
)
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


# Tokens per byte of request JSON. 0.4 (2.5 bytes a token) is the measured
# rate for code; prose is lower. Each answer's real prompt count replaces it.
DEFAULT_TOKENS_PER_BYTE = 0.4
# A proposal with its reasoning needs about this much room to answer.
MIN_OUTPUT_TOKENS = 1_024


def estimated_tokens(body: dict[str, Any], per_byte: float = DEFAULT_TOKENS_PER_BYTE) -> int:
    """Prompt tokens of a request, from its size."""
    return int(len(json.dumps(body).encode()) * per_byte) + 1


def fit(
    body: dict[str, Any], limit: int, per_byte: float = DEFAULT_TOKENS_PER_BYTE
) -> dict[str, Any]:
    """Shrink a request until its prompt and requested output fit within ``limit``.

    First the content of the oldest tool results is replaced, oldest first,
    with a note that the model may call the tool again. The task, every
    feedback message, every assistant turn and the newest tool result are
    kept. Then the requested output is lowered, never below
    ``MIN_OUTPUT_TOKENS``. The result may still be too large; the caller checks.
    """
    messages = [dict(message) for message in body["messages"]]
    fitted = {**body, "messages": messages}
    output = body["max_completion_tokens"]
    tools = [i for i, message in enumerate(messages) if message["role"] == "tool"]
    for index in tools[:-1]:
        if estimated_tokens(fitted, per_byte) + output <= limit:
            break
        messages[index]["content"] = OMITTED
    room = limit - estimated_tokens(fitted, per_byte)
    fitted["max_completion_tokens"] = max(min(output, room), min(output, MIN_OUTPUT_TOKENS))
    return fitted


def _unwrapped(text: str) -> str:
    """gpt-oss sometimes wraps its final answer in a call to a tool named ``json``."""
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if (
        isinstance(value, dict)
        and set(value) == {"name", "arguments"}
        and value["name"] == "json"
        and isinstance(value["arguments"], dict)
    ):
        return json.dumps(value["arguments"])
    return text


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
        self._per_byte = DEFAULT_TOKENS_PER_BYTE

    def _pace(self, tokens: int) -> None:
        """Wait until the last minute's requests leave room for this one."""
        if tokens > self._per_minute:
            # Waiting cannot help: one request may not exceed the per-minute limit.
            raise ModelError(
                "api_error",
                f"one request needs about {tokens} tokens, more than Groq's free "
                f"limit of {self._per_minute} a minute, even with older tool output omitted",
            )
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
        return estimated_tokens(fit(self.body(request), self._per_minute, self._per_byte))

    def _unusable(self, request: ModelRequest, body: dict[str, Any], error: str) -> ModelTurn:
        """Groq refused the model's own output as a malformed tool call.

        That is a model answer, not an outage. The model's raw output, which
        Groq returns as ``failed_generation``, becomes the turn's answer: the
        agent checks it like any answer and gives feedback if it is not a
        proposal. Groq reports no usage, so the request is charged in full.
        """
        try:
            generated = str(json.loads(error)["error"].get("failed_generation") or "")
        except (ValueError, KeyError, TypeError, AttributeError):
            generated = ""
        kept = {"role": "assistant", "content": generated or "(no answer)"}
        return ModelTurn(
            stop_reason="end_turn",
            content=({"type": _BLOCK, "message": kept},),
            text=_unwrapped(_unfenced(generated)),
            tool_calls=(),
            usage=Usage(
                input_tokens=estimated_tokens(body, self._per_byte),
                output_tokens=request.max_tokens,
            ),
            model=request.model,
            request_id="groq-tool-use-failed",
        )

    def complete(self, request: ModelRequest) -> ModelTurn:
        body = fit(self.body(request), self._per_minute, self._per_byte)
        # Groq counts the requested output in full against the per-minute limit.
        self._pace(estimated_tokens(body, self._per_byte) + body["max_completion_tokens"])
        try:
            response = self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException:
            raise ModelError("timeout", "Groq did not answer in time") from None
        except httpx.TransportError as error:
            raise ModelError(
                "connection", f"Groq is not reachable ({type(error).__name__})"
            ) from None
        if response.status_code == 400 and _code(response.text) == "tool_use_failed":
            return self._unusable(request, body, response.text[:MAX_RESPONSE_BYTES])
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
        prompt = int(usage.get("prompt_tokens") or 0)
        if prompt > 0:
            # Calibrate on the real count, with a 10% margin, within sane bounds.
            measured = prompt / len(json.dumps(body).encode()) * 1.1
            self._per_byte = min(0.5, max(0.2, measured))

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
