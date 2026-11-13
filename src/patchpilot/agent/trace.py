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

from pydantic import ValidationError

from patchpilot.llm.base import ModelError, ModelRequest, ModelTurn
from patchpilot.llm.fake import Scripted, ScriptedModel, ScriptExhaustedError

TRACE_FILE = "trace.jsonl"
_SECRET_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "PATCHPILOT_GITHUB_TOKEN",
)
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


def redact(text: str, secrets: Iterable[str]) -> str:
    """Remove known secret values, in plain and JSON-escaped form."""
    for secret in secrets:
        if len(secret) >= _MIN_SECRET_LENGTH:
            text = text.replace(secret, REDACTED).replace(json.dumps(secret)[1:-1], REDACTED)
    return text


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def request_digest(request: ModelRequest) -> str:
    """Return a hash identifying exactly what was sent to the model."""
    canonical = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class TraceWriter:
    """Appends events to a private trace file, redacting secrets."""

    def __init__(
        self, directory: Path, secrets: Iterable[str] = (), *, append: bool = False
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        self.path = directory / TRACE_FILE
        self._secrets = tuple(secret for secret in secrets if len(secret) >= _MIN_SECRET_LENGTH)
        # A resumed run continues its trace; a new run starts an empty one.
        mode = os.O_APPEND if append else os.O_TRUNC
        descriptor = os.open(self.path, os.O_CREAT | os.O_WRONLY | mode, 0o600)
        os.close(descriptor)

    def _write(self, event: dict[str, Any]) -> None:
        # Key order is kept as it is, so content blocks are stored exactly as received.
        line = redact(json.dumps(event), self._secrets)
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

    def resumed(self, sequence: int, phase: str, attempt: int) -> None:
        """Record that the run continues in a new process from a checkpoint."""
        self._write({"type": "resumed", "sequence": sequence, "phase": phase, "attempt": attempt})

    def transition(self, source: str, target: str, attempt: int, reason: str) -> None:
        """Record one step of a repair run's state machine."""
        self._write(
            {
                "type": "transition",
                "from": source,
                "to": target,
                "attempt": attempt,
                "reason": reason,
            }
        )

    def finish(self, status: str, detail: str, diff: str = "") -> None:
        self._write(
            {"type": "finish", "status": status, "detail": detail, "diff_sha256": text_digest(diff)}
        )


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise TraceError(f"{path} is not a trace file")
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except ValueError as error:
        raise TraceError(f"{path} is not a valid trace: {error}") from None


def recorded_mode(path: Path) -> str:
    """Return the mode a trace was recorded in: ``baseline`` or ``repair``."""
    for event in read_trace(path):
        if isinstance(event, dict) and event.get("type") == "start":
            return str(event.get("mode", "baseline"))
    return "baseline"


def recorded_outcome(path: Path) -> tuple[str, str] | None:
    """Return the status and diff digest the trace recorded at its end, if any."""
    for event in reversed(read_trace(path)):
        if isinstance(event, dict) and event.get("type") == "finish":
            status, digest = event.get("status"), event.get("diff_sha256")
            if isinstance(status, str) and isinstance(digest, str):
                return status, digest
            return None
    return None


class ReplayModel(ScriptedModel):
    """A scripted model that reports a damaged trace as such, not as a crash."""

    def complete(self, request: ModelRequest) -> ModelTurn:
        try:
            return super().complete(request)
        except ScriptExhaustedError:
            raise TraceError("the trace ends before the run does; it is incomplete") from None


def replay_model(path: Path, *, skip: int = 0) -> ReplayModel:
    """Return a model that replays a trace and makes no network call.

    Every request the pipeline makes during the replay is compared with the
    request recorded at the same position. A difference means the inputs,
    prompts, code or the trace itself changed since it was recorded, and
    stops the replay instead of pairing an old answer with a new question.
    A trace that is not well formed is refused before anything runs.

    ``skip`` leaves out the first answers of the trace, for a run that is
    resumed after it has already received them.
    """
    scripted: list[Scripted] = []
    try:
        events = read_trace(path)
        digests = {
            event["index"]: event["digest"] for event in events if event["type"] == "request"
        }
        for event in events:
            if event["type"] == "error":
                scripted.append(ModelError(event["kind"], str(event["message"])))
            elif event["type"] == "response":
                turn = ModelTurn.model_validate(event["turn"])
                expected = digests[event["index"]]

                def checked(
                    request: ModelRequest, turn: ModelTurn = turn, expected: str = expected
                ) -> ModelTurn:
                    if request_digest(request) != expected:
                        raise TraceError(
                            "the replayed run sent a different request than the recorded one"
                        )
                    return turn

                scripted.append(checked)
    except (KeyError, TypeError, AttributeError, ValidationError):
        raise TraceError(f"{path} is damaged or is not a PatchPilot trace") from None
    return ReplayModel(scripted[skip:])
