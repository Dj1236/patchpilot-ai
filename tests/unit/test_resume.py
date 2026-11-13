"""Checkpoints and resume of a repair run, with a scripted model and sandbox.

Each "process" is one call of ``run_repair`` or ``resume_repair``. A stop
between them is made by ``interrupt_after`` or by an injected failure.
"""

import hashlib
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from patchpilot.agent import checkpoint as checkpoint_module
from patchpilot.agent.baseline import RepairLimits, RunLimits, RunRecord, RunStatus
from patchpilot.agent.checkpoint import (
    CHECKPOINT_FILE,
    Checkpoint,
    CheckpointError,
    load_checkpoint,
    save_checkpoint,
)
from patchpilot.agent.repair import Interrupted, resume_repair, run_repair
from patchpilot.agent.trace import TraceWriter, read_trace, replay_model
from patchpilot.llm.base import ModelRequest, ModelTurn, Usage
from patchpilot.llm.budget import RunBudget, SpendLedger
from patchpilot.llm.fake import Scripted, ScriptedModel, final_turn, tool_turn
from patchpilot.sandbox.models import SandboxLimits, SandboxStatus
from tests.unit.test_baseline import BUG, COMMANDS, FILES, proposal
from tests.unit.test_repair import GOOD, STILL_FAILING, TalkingSandbox

SECRET = "patchpilot-checkpoint-canary-not-a-real-key"
LOOK = tool_turn(("list_files", {}))


def good(**changes: Any) -> Any:
    return final_turn(proposal(**changes))


# Investigation, a proposal that fails its check, then one that holds.
SCRIPT: list[Scripted] = [LOOK, good(summary="first"), good(summary="second")]
SANDBOX = (*STILL_FAILING, *GOOD)
BOUNDARIES = 8  # checkpoints an uninterrupted run of SCRIPT writes


class ProcessDied(Exception):
    """Stands in for a process that stopped in the middle of something."""


class Session:
    """One run directory and ledger, used by several processes in turn."""

    def __init__(
        self,
        tmp_path: Path,
        script: Sequence[Scripted] = SCRIPT,
        sandbox: Sequence[SandboxStatus] = SANDBOX,
        *,
        repair: RepairLimits | None = None,
        limits: RunLimits | None = None,
        run_cap: float = 0.75,
    ) -> None:
        self.directory = tmp_path / "runs" / "demo-1"
        self.ledger = SpendLedger(tmp_path / "runs" / "ledger.jsonl", cap_usd=5.0)
        self.script = list(script)
        self.sandbox = TalkingSandbox(sandbox)
        self.repair, self.limits, self.run_cap = repair, limits, run_cap
        self.requests = 0
        self.record: RunRecord | None = None
        self.patched: dict[str, bytes] | None = None

    def _model(self, answered: int) -> ScriptedModel:
        return ScriptedModel(self.script[answered:])

    def _finish(self, model: ScriptedModel, work: Callable[[], Any]) -> bool:
        """Run one process. Returns whether it was stopped before the run ended."""
        try:
            self.record, self.patched = work()
        except Interrupted:
            return True
        finally:
            self.requests += len(model.requests)
        return False

    def start(self, interrupt_after: int | None = None, **extra: Any) -> bool:
        model = extra.pop("model", None) or self._model(0)
        return self._finish(
            model,
            lambda: run_repair(
                task_id="demo",
                bug=BUG,
                files=FILES,
                source_roots=("src",),
                commands=COMMANDS,
                sandbox_limits=SandboxLimits(),
                model=model,
                sandbox=extra.pop("sandbox", self.sandbox),
                budget=RunBudget(self.ledger, "demo-1", self.run_cap),
                trace=TraceWriter(self.directory, (SECRET,)),
                limits=self.limits,
                repair=self.repair,
                run_cap_usd=self.run_cap,
                run_id="demo-1",
                checkpoint_dir=self.directory,
                secrets=(SECRET,),
                interrupt_after=interrupt_after,
            ),
        )

    def resume(self, interrupt_after: int | None = None, **extra: Any) -> bool:
        stored = load_checkpoint(self.directory)
        model = extra.pop("model", None) or self._model(stored.state.generation_attempts)
        return self._finish(
            model,
            lambda: resume_repair(
                checkpoint=stored,
                checkpoint_dir=self.directory,
                files=extra.pop("files", FILES),
                source_roots=("src",),
                commands=COMMANDS,
                sandbox_limits=SandboxLimits(),
                model=model,
                sandbox=extra.pop("sandbox", self.sandbox),
                ledger=self.ledger,
                trace=TraceWriter(self.directory, (SECRET,), append=True),
                secrets=(SECRET,),
                interrupt_after=interrupt_after,
            ),
        )

    @property
    def stored(self) -> Checkpoint:
        return load_checkpoint(self.directory)

    def rewrite(self, change: Callable[[dict[str, Any]], None]) -> None:
        """Edit the stored checkpoint and give it a matching hash, as a careful forger would."""
        path = self.directory / CHECKPOINT_FILE
        body = json.loads(path.read_text())["checkpoint"]
        change(body)
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        path.write_text(json.dumps({"sha256": digest, "checkpoint": body}))


def essence(record: RunRecord | None) -> Any:
    assert record is not None
    return (
        record.run_id,
        record.status,
        record.detail,
        record.diff,
        [attempt.status for attempt in record.attempts],
        record.model_turns,
        record.generation_attempts,
        record.tool_calls,
        record.usage,
        record.cost_micro_usd,
        record.manifest.config_sha256,
    )


@pytest.fixture
def uninterrupted(tmp_path: Path) -> Session:
    session = Session(tmp_path / "reference")
    assert session.start() is False
    return session


# -- stopping at every boundary --------------------------------------------------------------


@pytest.mark.parametrize("boundary", range(1, BOUNDARIES + 1))
def test_a_run_stopped_at_any_checkpoint_resumes_to_the_same_result(
    tmp_path: Path, uninterrupted: Session, boundary: int
) -> None:
    session = Session(tmp_path)

    stopped = session.start(interrupt_after=boundary)
    stored = session.stored
    resumed_requests = session.requests
    assert session.resume() is False

    assert stopped is True
    assert stored.sequence == boundary and stored.run_id == "demo-1"
    assert essence(session.record) == essence(uninterrupted.record)
    assert session.patched == uninterrupted.patched
    # Nothing completed was done again: the same requests and sandbox runs in total.
    assert session.requests == uninterrupted.requests == 3
    assert resumed_requests == stored.state.generation_attempts
    assert len(session.sandbox.runs) == len(uninterrupted.sandbox.runs) == 5
    assert session.ledger.committed() == uninterrupted.ledger.committed()
    assert session.ledger.run_requests("demo-1") == (3, 0, session.record.cost_micro_usd)  # type: ignore[union-attr]
    assert session.stored.state.phase == "done"


def test_the_checkpoints_of_a_run_are_the_documented_boundaries(tmp_path: Path) -> None:
    seen: list[tuple[int, str, int, int]] = []
    session = Session(tmp_path)
    original = checkpoint_module.save_checkpoint

    def record(directory: Path, stored: Checkpoint, secrets: Any = ()) -> None:
        state = stored.state
        seen.append((stored.sequence, state.phase, state.attempt, state.generation_attempts))
        original(directory, stored, secrets)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("patchpilot.agent.repair.save_checkpoint", record)
        session.start()

    assert seen == [
        (1, "propose", 1, 0),  # before the first request
        (2, "propose", 1, 1),  # after the investigation turn, before the next request
        (3, "evaluate", 1, 2),  # a proposal was given
        (4, "feedback", 1, 2),  # it was evaluated and not accepted
        (5, "propose", 2, 2),  # the model was told why
        (6, "propose", 2, 2),  # before the next request
        (7, "evaluate", 2, 3),
        (8, "done", 2, 3),
    ]


def test_stopping_again_and_again_still_ends_with_the_same_result(
    tmp_path: Path, uninterrupted: Session
) -> None:
    session = Session(tmp_path)

    assert session.start(interrupt_after=2) is True
    assert session.resume(interrupt_after=2) is True
    assert session.resume(interrupt_after=1) is True
    assert session.resume() is False

    assert essence(session.record) == essence(uninterrupted.record)
    assert session.requests == 3 and len(session.sandbox.runs) == 5
    resumed = [
        event
        for event in read_trace(session.directory / "trace.jsonl")
        if event["type"] == "resumed"
    ]
    assert len(resumed) == 3


def test_a_finished_run_is_not_run_again(tmp_path: Path, uninterrupted: Session) -> None:
    session = Session(tmp_path)
    session.start()
    first = essence(session.record)
    nobody = ScriptedModel([])

    for _ in range(2):
        assert session.resume(model=nobody, sandbox=TalkingSandbox(())) is False
        assert essence(session.record) == first
        assert session.patched == uninterrupted.patched, "the tree is rebuilt for grading"

    assert nobody.requests == [] and session.record.status is RunStatus.CANDIDATE_VERIFIED  # type: ignore[union-attr]
    assert session.ledger.run_requests("demo-1")[0] == 3


def test_a_resumed_run_replays_from_its_one_trace(tmp_path: Path, uninterrupted: Session) -> None:
    session = Session(tmp_path)
    session.start(interrupt_after=4)
    session.resume()

    replay = Session(tmp_path / "replay")
    replay.start(model=replay_model(session.directory / "trace.jsonl"))

    assert essence(replay.record)[1:5] == essence(uninterrupted.record)[1:5]


# -- limits survive a restart ------------------------------------------------------------------


def test_restarting_does_not_give_more_proposals(tmp_path: Path) -> None:
    session = Session(
        tmp_path, [good()] * 6, STILL_FAILING * 6, repair=RepairLimits(max_attempts=2)
    )

    session.start(interrupt_after=3)
    # Two checkpoints per process: the first only repeats the state it resumed from.
    for _ in range(20):
        if not session.resume(interrupt_after=2):
            break

    record = session.record
    assert record is not None and record.status is RunStatus.UNVERIFIED
    assert "no verified patch after 2 proposals" in record.detail
    assert len(record.attempts) == 2 and session.requests == 2
    assert record.manifest.repair == RepairLimits(max_attempts=2)


BIG = Usage(input_tokens=1_000, output_tokens=5_000)


@pytest.mark.parametrize(
    ("options", "fragment"),
    [
        ({"limits": RunLimits(max_turns=1)}, "limit of 1 model turns"),
        ({"run_cap": 0.20}, "would exceed this run's cap"),
        ({"repair": RepairLimits(max_total_tokens=1_000)}, "token budget"),
    ],
)
def test_a_run_whose_budget_is_used_up_ends_on_resume_without_new_work(
    tmp_path: Path, options: dict[str, Any], fragment: str
) -> None:
    script = [final_turn(proposal(), usage=BIG), good(), good()]
    session = Session(tmp_path, script, STILL_FAILING * 3, **options)

    # Stop where the state says "next proposal": after feedback, or as soon as the run ends.
    session.start(interrupt_after=4)
    before = session.ledger.committed()
    session.resume()

    record = session.record
    assert record is not None and record.status is RunStatus.BUDGET_EXCEEDED, record
    assert fragment in record.detail
    assert session.requests == 1, "no request is made once a budget is spent"
    assert session.ledger.committed() == before == record.cost_micro_usd


def test_tool_calls_and_time_already_used_still_count(tmp_path: Path) -> None:
    look_twice = tool_turn(("list_files", {}), ("read_file", {"path": "src/pkg/core.py"}))
    session = Session(
        tmp_path,
        [look_twice, good(), look_twice, good()],
        STILL_FAILING * 2,
        limits=RunLimits(max_tool_calls=3),
    )

    session.start(interrupt_after=5)
    used = session.stored.state
    session.resume()

    record = session.record
    assert (used.tool_calls, used.tool_output_bytes > 0) == (2, True)
    assert record is not None and record.status is RunStatus.BUDGET_EXCEEDED
    assert "tool calls" in record.detail and record.tool_calls == 3
    assert record.duration_seconds >= used.elapsed_seconds > 0


def test_limits_cannot_be_raised_by_editing_the_checkpoint(tmp_path: Path) -> None:
    session = Session(
        tmp_path, [good()] * 6, STILL_FAILING * 6, repair=RepairLimits(max_attempts=2)
    )
    session.start(interrupt_after=4)

    def raise_limits(body: dict[str, Any]) -> None:
        body["repair"]["max_attempts"] = 5
        body["limits"]["max_turns"] = 100

    session.rewrite(raise_limits)

    with pytest.raises(CheckpointError, match="configuration hash"):
        session.resume()
    assert session.requests == 1


# -- checkpoints that must be refused ------------------------------------------------------


def _truncate(path: Path) -> None:
    path.write_text(path.read_text()[:200])


def _flip(path: Path) -> None:
    path.write_text(path.read_text().replace('"attempt":1', '"attempt":2', 1))


def _not_ours(path: Path) -> None:
    path.write_text(json.dumps({"hello": "world"}))


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (_truncate, "corrupt"),
        (_flip, "does not match its own hash"),
        (_not_ours, "not a PatchPilot checkpoint"),
        (Path.unlink, "has no checkpoint"),
    ],
)
def test_a_damaged_checkpoint_is_refused_before_any_model_call(
    tmp_path: Path, damage: Callable[[Path], None], message: str
) -> None:
    session = Session(tmp_path)
    session.start(interrupt_after=4)
    committed = session.ledger.committed()
    damage(session.directory / CHECKPOINT_FILE)

    with pytest.raises(CheckpointError, match=message):
        session.resume()

    assert session.requests == 2 and session.ledger.committed() == committed


def _version(body: dict[str, Any]) -> None:
    body["schema_version"] = 2


def _extra(body: dict[str, Any]) -> None:
    body["state"]["approved_by_owner"] = True


def _no_proposal(body: dict[str, Any]) -> None:
    body["state"]["phase"] = "evaluate"


def _other_task(body: dict[str, Any]) -> None:
    body["repository_tree_sha256"] = "0" * 64


def _too_many(body: dict[str, Any]) -> None:
    body["state"]["attempt"] = 4


def _traversal(body: dict[str, Any]) -> None:
    body["task_id"] = "../../etc"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_version, "schema version 2"),
        (_extra, "not valid at state.approved_by_owner"),
        (_no_proposal, "phase and its pending proposal disagree"),
        (_other_task, "files are not the ones this run started from"),
        (_too_many, "proposal count is not possible"),
        (_traversal, "not valid at task_id"),
    ],
)
def test_an_incompatible_or_inconsistent_checkpoint_is_refused(
    tmp_path: Path, change: Callable[[dict[str, Any]], None], message: str
) -> None:
    session = Session(tmp_path)
    session.start(interrupt_after=5)
    session.rewrite(change)

    with pytest.raises(CheckpointError, match=message):
        session.resume()

    assert session.requests == 2


def test_a_checkpoint_for_other_files_or_prompts_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(tmp_path)
    session.start(interrupt_after=5)

    with pytest.raises(CheckpointError, match="files are not the ones"):
        session.resume(files={**FILES, "src/pkg/core.py": b"def total(items):\n    return 0\n"})
    monkeypatch.setattr("patchpilot.agent.repair.REPAIR_SYSTEM_PROMPT", "A different prompt.")
    with pytest.raises(CheckpointError, match="prompts changed"):
        session.resume()


def test_a_checkpoint_that_is_too_large_or_not_a_regular_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(tmp_path)
    session.start(interrupt_after=5)
    path = session.directory / CHECKPOINT_FILE
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(path.read_bytes())

    monkeypatch.setattr(checkpoint_module, "MAX_CHECKPOINT_BYTES", 100)
    with pytest.raises(CheckpointError, match="larger than any PatchPilot writes"):
        load_checkpoint(session.directory)
    with pytest.raises(CheckpointError, match="the limit is 100"):
        save_checkpoint(
            session.directory,
            Checkpoint.model_validate(json.loads(elsewhere.read_text())["checkpoint"]),
        )
    monkeypatch.undo()
    path.unlink()
    path.symlink_to(elsewhere)
    with pytest.raises(CheckpointError, match="has no checkpoint"):
        load_checkpoint(session.directory)
    with pytest.raises(CheckpointError, match="not a run directory"):
        save_checkpoint(tmp_path / "missing", session_checkpoint(elsewhere))


def session_checkpoint(path: Path) -> Checkpoint:
    return Checkpoint.model_validate(json.loads(path.read_text())["checkpoint"])


# -- failures in the middle of something ---------------------------------------------------------


def test_a_stop_during_a_model_request_is_not_resumed(tmp_path: Path) -> None:
    def dies(request: ModelRequest) -> ModelTurn:
        raise ProcessDied

    session = Session(tmp_path, [LOOK, dies, good()])
    with pytest.raises(ProcessDied):
        session.start()
    reservations, still_open, committed = session.ledger.run_requests("demo-1")
    nobody = ScriptedModel([good()])

    with pytest.raises(CheckpointError, match="in flight when the run stopped"):
        session.resume(model=nobody)

    assert (reservations, still_open) == (2, 1) and committed > 160_000
    assert nobody.requests == [], "the request is not repeated"
    assert session.ledger.committed() == committed, "the open reservation stays charged in full"


def test_a_stop_between_an_answer_and_its_checkpoint_is_not_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(tmp_path)
    calls = 0
    original = checkpoint_module.save_checkpoint

    def dies_on_the_second(directory: Path, stored: Checkpoint, secrets: Any = ()) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProcessDied
        original(directory, stored, secrets)

    monkeypatch.setattr("patchpilot.agent.repair.save_checkpoint", dies_on_the_second)
    with pytest.raises(ProcessDied):
        session.start()
    monkeypatch.undo()
    nobody = ScriptedModel([good()])

    # The stored checkpoint says no request was made; the ledger knows of one.
    assert session.stored.state.generation_attempts == 0
    with pytest.raises(CheckpointError, match="stale"):
        session.resume(model=nobody)
    assert nobody.requests == []


def test_a_stop_during_sandbox_checks_repeats_only_those_checks(
    tmp_path: Path, uninterrupted: Session
) -> None:
    class DyingSandbox(TalkingSandbox):
        def run(self, *args: Any, **kwargs: Any) -> Any:
            if len(self.runs) == 1:
                raise ProcessDied
            return super().run(*args, **kwargs)

    session = Session(tmp_path)
    with pytest.raises(ProcessDied):
        session.start(sandbox=DyingSandbox(SANDBOX))
    stored = session.stored

    assert session.resume() is False

    assert (stored.state.phase, stored.state.proposal is not None) == ("evaluate", True)
    assert essence(session.record) == essence(uninterrupted.record)
    assert session.requests == 3, "the proposal is not asked for again"


def test_a_failed_write_leaves_the_previous_checkpoint_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = Session(tmp_path)
    session.start(interrupt_after=3)
    before = (session.directory / CHECKPOINT_FILE).read_bytes()
    later = session.stored.model_copy(update={"sequence": 99})

    def fails(self: Path, target: Path) -> Path:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", fails)
    with pytest.raises(CheckpointError, match="could not be written"):
        save_checkpoint(session.directory, later)
    monkeypatch.undo()

    assert (session.directory / CHECKPOINT_FILE).read_bytes() == before
    assert session.stored.sequence == 3
    assert sorted(path.name for path in session.directory.iterdir()) == [
        "checkpoint.json",
        "trace.jsonl",
    ]


# -- what a checkpoint holds ---------------------------------------------------------------------


def test_a_checkpoint_keeps_the_run_and_leaves_out_secrets(tmp_path: Path) -> None:
    session = Session(tmp_path, [LOOK, good(summary=f"The key is {SECRET}."), good()])
    session.start(interrupt_after=5)

    path = session.directory / CHECKPOINT_FILE
    stored = session.stored
    text = path.read_text()
    assert path.stat().st_mode & 0o077 == 0 and session.directory.stat().st_mode & 0o077 == 0
    assert SECRET not in text and "[redacted]" in text
    assert (stored.schema_version, stored.mode, stored.task_id) == (1, "repair", "demo")
    assert (stored.limits.provider, stored.limits.model) == ("anthropic", "claude-sonnet-5-5")
    assert stored.repair == RepairLimits() and stored.run_cap_usd == 0.75
    state = stored.state
    assert (state.phase, state.attempt, len(state.attempts)) == ("propose", 2, 1)
    assert (state.model_turns, state.generation_attempts, state.tool_calls) == (2, 2, 1)
    assert state.usage.input_tokens == 200 and state.cost_micro_usd > 0
    assert [message["role"] for message in state.messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]


def test_run_directories_are_ignored_by_git() -> None:
    ignored = (Path(__file__).resolve().parents[2] / ".gitignore").read_text().splitlines()

    assert "runs/" in ignored
