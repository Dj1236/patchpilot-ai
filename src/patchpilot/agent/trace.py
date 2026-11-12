"""The raw, local record of one run's conversation with the model.

A trace holds full prompts and responses, including thinking blocks and
repository content. It is for local debugging and replay only: its directory
and file are readable by the owner alone, it lives under an ignored path, and
it is never part of a public report. Known secret values are removed from
every line before it is written.
"""

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from patchpilot.llm.base import ModelError, ModelRequest, ModelTurn
from patchpilot.llm.fake import Scripted, ScriptedModel

TRACE_FILE = "trace.jsonl"
_SECRET_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "PATCHPILOT_GITHUB_TOKEN")
_MIN_SECRET_LENGTH = 8
REDACTED = "[redacted]"


class TraceError(ValueError):
    """A trace is missing, damaged, or does not match the run being replayed."""


def secrets_from_environment(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    values = os.environ if environ is None else environ
    return tuple(
        values[name]
        for name in _SECRET_VARIABLES
        if len(values.get(name, "")) >= _MIN_SECRET_LENGTH
    )


def request_digest(request: ModelRequest) -> str:
    """Return a hash identifying exactly what was sent to the model."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class TraceWriter:
    """Appends events to a private trace file, redacting secrets."""

    def __init__(self, directory: Path, secrets: Iterable[str] = ()) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.path = directory / TRACE_FILE
        self._secrets = tuple(secret for secret in secrets if len(secret) >= _MIN_SECRET_LENGTH)
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        os.close(descriptor)

    def _write(self, event: dict[str, Any]) -> None:
        # Key order is kept as it is, so content blocks are stored exactly as received.
        line = json.dumps(event)
        for secret in self._secrets:
            line = line.replace(secret, REDACTED)
            line = line.replace(json.dumps(secret)[1:-1], REDACTED)
        with self.path.open("a") as handle:
            handle.write(line + "\n")

    def start(self, header: dict[str, Any]) -> None:
        self._write({"type": "start", **header})

    def request(self, index: int, request: ModelRequest) -> None:
        self._write(
            {
                "type": "request",
                "index": index,
                "digest": request_digest(request),
                "request": request.model_dump(mode="json"),
            }
        )

    def response(self, index: int, turn: ModelTurn) -> None:
        self._write({"type": "response", "index": index, "turn": turn.model_dump(mode="json")})

    def error(self, index: int, error: ModelError) -> None:
        self._write({"type": "error", "index": index, "kind": error.kind, "message": str(error)})

    def finish(self, status: str, detail: str) -> None:
        self._write({"type": "finish", "status": status, "detail": detail})


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise TraceError(f"{path} is not a trace file")
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except ValueError as error:
        raise TraceError(f"{path} is not a valid trace: {error}") from None


def replay_model(path: Path) -> ScriptedModel:
    """Return a model that replays a trace and makes no network call.

    Every request the pipeline makes during the replay is compared with the
    request recorded at the same position. A difference means the inputs,
    prompts or code changed since the trace was recorded, and stops the
    replay instead of pairing an old answer with a new question.
    """
    events = read_trace(path)
    digests = {event["index"]: event["digest"] for event in events if event["type"] == "request"}
    scripted: list[Scripted] = []
    for event in events:
        if event["type"] == "error":
            scripted.append(ModelError(event["kind"], event["message"]))
        elif event["type"] == "response":
            turn = ModelTurn.model_validate(event["turn"])
            expected = digests.get(event["index"])

            def checked(
                request: ModelRequest, turn: ModelTurn = turn, expected: str | None = expected
            ) -> ModelTurn:
                if expected is not None and request_digest(request) != expected:
                    raise TraceError(
                        "the replayed run sent a different request than the recorded one"
                    )
                return turn

            scripted.append(checked)
    return ScriptedModel(scripted)
