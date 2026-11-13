"""Gemini through the official Google GenAI SDK.

Nothing here is used by the automated tests except through a stand-in client
object; a real request is only ever made when a run is started with the
explicit live flag.

The Interactions API is used without server-side state. Choices that are
deliberate:

- ``store`` is off, so the provider keeps no interaction record and the whole
  history is sent with every request.
- The SDK's retries and its automatic continuation are off. Both send
  requests that were never reserved in the ledger.
- Vertex AI and enterprise mode are refused, because they are always billed.
  Only a Gemini Developer API key is used, on the standard service tier.
- Only function tools are declared. Search, code execution and the other
  server-side tools are never enabled.
- The model's steps are returned as plain dictionaries exactly as received,
  thought steps and their signatures included, and are sent back unchanged.
- One model is requested. An answer from a different model is an error, not
  a fallback.

The agent writes its messages in the shape of the first provider. This
module translates them, so the agent loop is the same for every provider.
"""

import json
import os
import re
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from patchpilot.llm.base import ModelError, ModelRequest, ModelTurn, StopReason, ToolCall, Usage

_COUNT_MARGIN = 1.10
_COUNT_PADDING = 256
_RATE_LIMIT_WAIT_SECONDS = 60.0
# Effort levels this model accepts. Others are refused, not rounded.
_THINKING_LEVELS = ("low", "medium", "high")
_PAID_MODES = ("GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_ENTERPRISE")
_BLOCK_WORDS = ("safety", "recitation", "prohibited", "blocklist", "spii")
_CUT_OFF = ("incomplete", "requires_continuation", "budget_exceeded")
_ERROR_CODE = re.compile(r"[A-Za-z_]{1,40}")
_COUNTED_PARTS = ("system_instruction", "input", "tools", "response_format")


def connect(timeout_seconds: float = 300.0, *, httpx_client: Any | None = None) -> Any:
    """Build the SDK client from the environment. The credential is never read here.

    ``httpx_client`` replaces the SDK's HTTP client; tests use it to answer
    requests locally.
    """
    for name in _PAID_MODES:
        if os.environ.get(name, "").strip().lower() not in ("", "0", "false"):
            raise ModelError("not_configured", f"{name} is set; that mode is billed and is refused")
    if "GOOGLE_API_KEY" in os.environ:
        # The SDK prefers this variable, and it may belong to a billed project.
        raise ModelError(
            "not_configured", "GOOGLE_API_KEY is set; unset it and use GEMINI_API_KEY only"
        )
    from google import genai
    from google.genai import types

    try:
        return genai.Client(
            vertexai=False,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(attempts=1),
                httpx_client=httpx_client,
            ),
        )
    except ValueError:
        raise ModelError(
            "not_configured",
            "no credential is configured; set GEMINI_API_KEY in the environment that "
            "starts PatchPilot",
        ) from None


def describe_error(error: Exception) -> tuple[int | None, str, bool]:
    """Return the HTTP status, the provider's error code, and whether a daily quota ran out.

    The SDK has two error families with different attribute names, so the
    error is read by attribute, not by class.
    """
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        status = getattr(error, "code", None)
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        body = getattr(error, "details", None)
    inner = body.get("error", body) if isinstance(body, dict) else {}
    inner = inner if isinstance(inner, dict) else {}
    code = next(
        (
            value
            for value in (inner.get("code"), inner.get("status"))
            if isinstance(value, str) and _ERROR_CODE.fullmatch(value)
        ),
        "",
    )
    daily = code == "quota_exceeded" or "perday" in json.dumps(body, default=str).lower()
    return (status if isinstance(status, int) else None), code, daily


def failure(error: Exception) -> ModelError:
    """Turn any SDK failure into a ``ModelError``.

    Only the class, the status and the provider's short error code are
    reported. Messages can echo request details.
    """
    if isinstance(error, ModelError):
        return error
    status, code, daily = describe_error(error)
    name = type(error).__name__
    if "Timeout" in name or status in (408, 504):
        return ModelError("timeout", "the model request timed out")
    if status == 429 and daily:
        return ModelError(
            "quota_exhausted",
            "the daily quota is used up; it resets at midnight Pacific time",
        )
    if status == 429:
        return ModelError(
            "rate_limited",
            "the model API rate limit was reached",
            retry_after_seconds=_RATE_LIMIT_WAIT_SECONDS,
        )
    if status is None:
        if "Connect" in name or isinstance(error, OSError):
            return ModelError("connection", "the model API could not be reached")
        return ModelError("api_error", f"unexpected model client error: {name}")
    detail = f"the model API returned HTTP {status}" + (f" ({code})" if code else "")
    # Server-side failures are worth one more attempt; client errors are not.
    return ModelError("connection" if status >= 500 else "api_error", detail)


def _steps(messages: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Translate the agent's messages into interaction steps."""
    steps: list[dict[str, Any]] = []
    names: dict[str, str] = {}
    for message in messages:
        content = message["content"]
        if message["role"] == "assistant":
            # The model's own steps go back exactly as they were received.
            for step in content:
                if step.get("type") == "function_call":
                    names[step["id"]] = step["name"]
                steps.append(dict(step))
        elif isinstance(content, str):
            steps.append({"type": "user_input", "content": [{"type": "text", "text": content}]})
        else:
            for block in content:
                if block.get("type") != "tool_result":
                    raise ModelError("api_error", "a message block has no Gemini equivalent")
                result = {
                    "type": "function_result",
                    "call_id": block["tool_use_id"],
                    "result": block["content"],
                    "is_error": bool(block.get("is_error")),
                }
                if block["tool_use_id"] in names:
                    result["name"] = names[block["tool_use_id"]]
                steps.append(result)
    return steps


def _stop(status: str, steps: tuple[dict[str, Any], ...], errors: list[Any]) -> StopReason:
    faults = [step["error"] for step in steps if step.get("error")] + errors
    if status == "failed" or faults:
        text = json.dumps(faults, default=str).lower()
        return "refusal" if any(word in text for word in _BLOCK_WORDS) else "other"
    if status in _CUT_OFF:
        return "max_tokens"
    if any(step.get("type") == "function_call" for step in steps):
        return "tool_use"
    return "end_turn" if status == "completed" else "other"


def padded(tokens: int) -> int:
    """Return the token count a reservation is made with."""
    return int(tokens * _COUNT_MARGIN) + _COUNT_PADDING


class GenerationLimiter:
    """Paces generation requests and caps how many one process may attempt.

    Every attempt counts, retries included, whether or not it succeeds. Token
    counting is free at the provider and has separate limits, so it is
    neither paced nor counted here.

    The defaults are the first pilot's: 4 requests in any 60 seconds, under
    the free tier's 5 per minute, and 8 attempts in all. Raise them only
    when a larger run is authorized.
    """

    def __init__(
        self,
        *,
        per_window: int = 4,
        window_seconds: float = 60.0,
        max_attempts: int = 8,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._per_window = per_window
        self._window = window_seconds
        self._max_attempts = max_attempts
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self.attempts = 0

    def check(self) -> None:
        """Raise if no generation attempt is left. Nothing is sent or reserved."""
        if self.attempts >= self._max_attempts:
            raise ModelError(
                "attempt_limit",
                f"the limit of {self._max_attempts} generation attempts is used up",
            )

    def acquire(self) -> None:
        """Count one attempt, first waiting until the rolling window has room."""
        self.check()
        now = self._clock()
        if len(self._sent) >= self._per_window:
            wait = self._sent[0] + self._window - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
            self._sent.popleft()
        self._sent.append(now)
        self.attempts += 1


class GeminiModel:
    """A ``ModelClient`` backed by the Gemini Interactions API."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        timeout_seconds: float = 300.0,
        limiter: GenerationLimiter | None = None,
    ) -> None:
        # Credentials come from the environment. They are never read, stored
        # or logged by PatchPilot.
        self._client = connect(timeout_seconds) if client is None else client
        self.limiter = limiter or GenerationLimiter()
        self.count_requests = 0

    @staticmethod
    def _arguments(request: ModelRequest) -> dict[str, Any]:
        if request.effort not in _THINKING_LEVELS:
            raise ModelError(
                "not_configured", f"effort {request.effort!r} is not offered by this model"
            )
        arguments: dict[str, Any] = {
            "model": request.model,
            "input": _steps(request.messages),
            "system_instruction": request.system,
            "generation_config": {
                "max_output_tokens": request.max_tokens,
                "thinking_level": request.effort,
            },
            "store": False,
        }
        if request.tools:
            arguments["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                }
                for tool in request.tools
            ]
        if request.output_schema is not None:
            arguments["response_format"] = {
                "type": "text",
                "mime_type": "application/json",
                "schema": request.output_schema,
            }
        return arguments

    @classmethod
    def flattened(cls, request: ModelRequest) -> str:
        """Return the request as one text, for the token-counting endpoint.

        That endpoint takes plain content only, so the system prompt, tools,
        answer schema and history are counted together as JSON. The result is
        an estimate of the real input, not the provider's exact figure.
        """
        arguments = cls._arguments(request)
        return json.dumps(
            {name: arguments[name] for name in _COUNTED_PARTS if name in arguments},
            sort_keys=True,
        )

    def count_input_tokens(self, request: ModelRequest) -> int:
        """Estimate the input tokens of the request, plus a safety margin."""
        # Sizing comes before the reservation, so a run that is out of
        # attempts stops here with nothing reserved and nothing sent.
        self.limiter.check()
        self.count_requests += 1
        try:
            counted = self._client.models.count_tokens(
                model=request.model, contents=self.flattened(request)
            )
            return padded(int(counted.total_tokens))
        except Exception as error:
            raise failure(error) from None

    def complete(self, request: ModelRequest) -> ModelTurn:
        self.limiter.acquire()
        try:
            response = self._client.interactions.create(
                automatic_continuation=False, **self._arguments(request)
            )
            steps = tuple(
                step.model_dump(mode="json", exclude_none=True, by_alias=True)
                for step in response.steps or ()
            )
            errors = [
                item.model_dump(mode="json", exclude_none=True) for item in response.errors or ()
            ]
            usage = response.usage
            reported = str(response.model or "").removeprefix("models/")
            status = str(response.status)
        except Exception as error:
            # Anything that goes wrong here happened after the reservation,
            # so it must surface as a failed, charged request.
            raise failure(error) from None
        # Exact, or with a numeric version suffix. A prefix match would accept
        # a sibling such as the "-lite" model. No reported model is no proof.
        if not re.fullmatch(re.escape(request.model) + r"(-\d+)*", reported):
            raise ModelError("api_error", "the provider answered with a different model")

        def count(name: str) -> int:
            return int(getattr(usage, name, 0) or 0)

        return ModelTurn(
            stop_reason=_stop(status, steps, errors),
            content=steps,
            text="".join(
                part.get("text", "")
                for step in steps
                if step.get("type") == "model_output"
                for part in step.get("content") or ()
                if part.get("type") == "text"
            ),
            tool_calls=tuple(
                ToolCall(
                    id=step["id"], name=step["name"], arguments=dict(step.get("arguments") or {})
                )
                for step in steps
                if step.get("type") == "function_call"
            ),
            # Upper bounds: the provider does not say whether thought tokens
            # are inside the output total, or cached tokens inside the input.
            usage=Usage(
                input_tokens=count("total_input_tokens") + count("total_tool_use_tokens"),
                output_tokens=count("total_output_tokens") + count("total_thought_tokens"),
                cache_read_input_tokens=count("total_cached_tokens"),
            ),
            model=reported,
            request_id=getattr(response, "id", None),
        )
