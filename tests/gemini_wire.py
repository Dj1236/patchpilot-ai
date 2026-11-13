"""Drive the real Google SDK without a network.

Every HTTP request the SDK makes is answered by ``Wire`` from a script, so
the tests exercise the SDK's own request building and response parsing. No
request leaves the machine.
"""

import json
from collections.abc import Sequence
from typing import Any

import httpx

from patchpilot.llm.gemini_client import GeminiModel, connect

MODEL = "gemini-3.8-flash"
# A reply is an interaction body, an (HTTP status, body) pair, or an error to raise.
Reply = dict[str, Any] | tuple[int, dict[str, Any]] | Exception


def thought(signature: str = "c2lnbmF0dXJlLTE=") -> dict[str, Any]:
    return {
        "type": "thought",
        "signature": signature,
        "summary": [{"type": "text", "text": "Looking at the cache."}],
    }


def call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function_call", "id": call_id, "name": name, "arguments": arguments}


def answer(payload: dict[str, Any] | str) -> dict[str, Any]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"type": "model_output", "content": [{"type": "text", "text": text}]}


def interaction(
    *steps: dict[str, Any],
    status: str | None = None,
    model: str = MODEL,
    usage: dict[str, int] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the body of one interaction response."""
    calls = any(step["type"] == "function_call" for step in steps)
    return {
        "id": "interaction-1",
        "model": model,
        "status": status or ("requires_action" if calls else "completed"),
        "created": "2026-11-13T00:00:00Z",
        "updated": "2026-11-13T00:00:01Z",
        "steps": list(steps),
        "usage": usage or {"total_input_tokens": 100, "total_output_tokens": 20},
        **extra,
    }


def api_error(
    status: int, code: str, message: str = "provider detail"
) -> tuple[int, dict[str, Any]]:
    return status, {"error": {"code": code, "message": message}}


class Wire:
    """Answers the SDK's HTTP requests from a script and records what was sent."""

    def __init__(
        self,
        replies: Sequence[Reply] = (),
        *,
        count: int | tuple[int, dict[str, Any]] = 1800,
        model_info: tuple[int, dict[str, Any]] | None = None,
    ) -> None:
        self._replies = list(replies)
        self._count = count
        self._model_info = model_info or (
            200,
            {"name": f"models/{MODEL}", "displayName": "Gemini 3.8 Flash"},
        )
        self.sent: list[dict[str, Any]] = []
        self.counted: list[dict[str, Any]] = []
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(f"{request.method} {url}")
        if url.endswith(":countTokens"):
            self.counted.append(json.loads(request.content))
            if isinstance(self._count, int):
                return httpx.Response(200, json={"totalTokens": self._count})
            return httpx.Response(self._count[0], json=self._count[1])
        if request.method == "GET":
            return httpx.Response(self._model_info[0], json=self._model_info[1])
        self.sent.append(json.loads(request.content))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, tuple):
            return httpx.Response(reply[0], json=reply[1])
        return httpx.Response(200, json=reply)

    def client(self) -> Any:
        """The real SDK client, built by the production code, answered locally."""
        return connect(5.0, httpx_client=httpx.Client(transport=httpx.MockTransport(self)))

    def model(self) -> GeminiModel:
        return GeminiModel(self.client())
