"""A scripted model for tests and for replaying stored traces.

It never touches the network. Each call returns the next scripted turn, so a
test can make the "model" do anything at all, including things a real model
should never do. That is the point: the code around the model must hold even
when the model misbehaves.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any

from patchpilot.llm.base import ModelError, ModelRequest, ModelTurn, ToolCall, Usage

Scripted = ModelTurn | ModelError | Callable[[ModelRequest], ModelTurn]


class ScriptExhaustedError(AssertionError):
    """The code asked the scripted model for more turns than were scripted."""


def tool_turn(*calls: tuple[str, dict[str, Any]], usage: Usage | None = None) -> ModelTurn:
    """Build a turn in which the model asks for the given tool calls."""
    tool_calls = tuple(
        ToolCall(id=f"call_{index}", name=name, arguments=arguments)
        for index, (name, arguments) in enumerate(calls)
    )
    content = tuple(
        {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        for call in tool_calls
    )
    return ModelTurn(
        stop_reason="tool_use",
        content=content,
        tool_calls=tool_calls,
        usage=usage or Usage(input_tokens=100, output_tokens=20),
        model="scripted",
    )


def final_turn(payload: dict[str, Any] | str, usage: Usage | None = None) -> ModelTurn:
    """Build a final turn whose text is ``payload`` (JSON-encoded if a dict)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return ModelTurn(
        stop_reason="end_turn",
        content=({"type": "text", "text": text},),
        text=text,
        usage=usage or Usage(input_tokens=100, output_tokens=50),
        model="scripted",
    )


class ScriptedModel:
    """Returns scripted turns in order and records every request it was sent."""

    def __init__(self, turns: Sequence[Scripted], *, tokens_per_request: int = 1000) -> None:
        self._turns = list(turns)
        self._tokens_per_request = tokens_per_request
        self.requests: list[ModelRequest] = []

    def count_input_tokens(self, request: ModelRequest) -> int:
        return self._tokens_per_request

    def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        if len(self.requests) > len(self._turns):
            raise ScriptExhaustedError(
                f"request {len(self.requests)} was sent, but only {len(self._turns)} are scripted"
            )
        scripted = self._turns[len(self.requests) - 1]
        if isinstance(scripted, ModelError):
            raise scripted
        if isinstance(scripted, ModelTurn):
            return scripted
        return scripted(request)

    def sent_text(self) -> str:
        """Everything that was sent to the model, as one searchable string."""
        return "\n".join(request.model_dump_json() for request in self.requests)
