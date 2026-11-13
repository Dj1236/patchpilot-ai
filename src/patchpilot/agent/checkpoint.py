"""The durable state of a repair run.

``RepairState`` is everything a repair run knows, as plain JSON data. A
``Checkpoint`` wraps it with the run's fixed identity and limits and is
written to ``checkpoint.json`` in the run's own directory.

Writing is atomic: the new content goes to a temporary file in the same
directory, is flushed to disk, and then replaces the old file in one step.
A reader sees the old checkpoint or the new one, never a partial file.

Loading trusts nothing. The file must be a regular file of bounded size,
valid JSON, of a known schema version, with a matching content hash, and it
must validate against the model, which rejects unknown fields. A checkpoint
that fails any of this is refused before anything else happens.

A checkpoint holds what the model was shown and what it answered, like the
trace, so it stays local. Known secret values are removed before writing.
Hidden tests are never part of a run, so they cannot be in its state.
"""

import contextlib
import hashlib
import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from patchpilot.agent.baseline import Attempt, RepairLimits, RunLimits, RunStatus
from patchpilot.agent.proposal import PatchProposal
from patchpilot.agent.trace import redact
from patchpilot.llm.base import Usage
from patchpilot.sandbox.models import FrozenModel
from patchpilot.tasks.schema import TaskId

SCHEMA_VERSION = 1
CHECKPOINT_FILE = "checkpoint.json"
MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024
_TEMPORARY = f".{CHECKPOINT_FILE}.tmp"

Phase = Literal["propose", "evaluate", "feedback", "done"]


class CheckpointError(ValueError):
    """A checkpoint cannot be written, read or used. Nothing is resumed from it."""


class RepairState(BaseModel):
    """Everything a repair run knows, as plain JSON data."""

    model_config = ConfigDict(extra="forbid")

    phase: Phase = "propose"
    # The proposal being worked on, counted from one.
    attempt: int = Field(default=1, ge=1, le=5)
    messages: list[dict[str, Any]]
    # A proposal that was given and is waiting to be evaluated.
    proposal: PatchProposal | None = None
    attempts: list[Attempt] = []
    status: RunStatus | None = None
    detail: str = ""
    model_turns: int = Field(default=0, ge=0)
    generation_attempts: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    token_count_requests: int = Field(default=0, ge=0)
    usage: Usage = Usage()
    models_reported: list[str] = []
    tool_calls: int = Field(default=0, ge=0)
    tool_output_bytes: int = Field(default=0, ge=0)
    cost_micro_usd: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0.0, ge=0)


class Checkpoint(FrozenModel):
    """One stored state of one run, with the identity and limits that never change."""

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=200)
    task_id: TaskId
    mode: Literal["repair"] = "repair"
    started_at: datetime
    repository_tree_sha256: str
    repository_commit: str | None = None
    prompt_sha256: str
    config_sha256: str
    run_cap_usd: float = Field(gt=0)
    limits: RunLimits
    repair: RepairLimits
    # Counts the checkpoints of the run, so a later one can be told from an earlier one.
    sequence: int = Field(ge=1)
    state: RepairState


def _canonical(body: Any) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


def save_checkpoint(directory: Path, checkpoint: Checkpoint, secrets: Iterable[str] = ()) -> None:
    """Replace the run's checkpoint with ``checkpoint``, atomically and durably."""
    if directory.is_symlink() or not directory.is_dir():
        raise CheckpointError(f"{directory} is not a run directory")
    body = json.loads(redact(_canonical(checkpoint.model_dump(mode="json")), secrets))
    text = _canonical(
        {"sha256": hashlib.sha256(_canonical(body).encode()).hexdigest(), "checkpoint": body}
    )
    data = text.encode()
    if len(data) > MAX_CHECKPOINT_BYTES:
        raise CheckpointError(
            f"the checkpoint would be {len(data)} bytes; the limit is {MAX_CHECKPOINT_BYTES}"
        )
    temporary = directory / _TEMPORARY
    descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(directory / CHECKPOINT_FILE)
    except OSError as error:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise CheckpointError(f"the checkpoint could not be written: {error}") from None
    # Make the rename itself durable.
    folder = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)


def load_checkpoint(directory: Path) -> Checkpoint:
    """Read and validate a run's checkpoint. Raises ``CheckpointError`` on any doubt."""
    path = directory / CHECKPOINT_FILE
    if path.is_symlink() or not path.is_file():
        raise CheckpointError(f"{directory} has no checkpoint")
    if path.stat().st_size > MAX_CHECKPOINT_BYTES:
        raise CheckpointError("the checkpoint is larger than any PatchPilot writes")
    try:
        outer = json.loads(path.read_bytes())
    except (OSError, ValueError):
        raise CheckpointError("the checkpoint is not valid JSON; it is corrupt") from None
    if not isinstance(outer, dict) or set(outer) != {"sha256", "checkpoint"}:
        raise CheckpointError("the checkpoint is not a PatchPilot checkpoint")
    body = outer["checkpoint"]
    if not isinstance(body, dict):
        raise CheckpointError("the checkpoint is not a PatchPilot checkpoint")
    if body.get("schema_version") != SCHEMA_VERSION:
        raise CheckpointError(
            f"the checkpoint has schema version {body.get('schema_version')!r}; "
            f"this PatchPilot reads version {SCHEMA_VERSION}"
        )
    if hashlib.sha256(_canonical(body).encode()).hexdigest() != outer["sha256"]:
        raise CheckpointError("the checkpoint does not match its own hash; it is corrupt")
    try:
        return Checkpoint.model_validate(body)
    except ValidationError as error:
        problem = error.errors()[0]
        where = ".".join(str(part) for part in problem["loc"])
        raise CheckpointError(f"the checkpoint is not valid at {where}: {problem['msg']}") from None
