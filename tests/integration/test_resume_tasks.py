"""Stopping and resuming a repair run on a real fixture, in the real sandbox.

The model is scripted. Each call of ``run_task`` or ``resume_task`` stands
for one process; ``interrupt_after`` ends it after a checkpoint, as a
failure would.
"""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import patchpilot.cli as cli
from patchpilot.agent.baseline import RunStatus
from patchpilot.agent.checkpoint import CHECKPOINT_FILE, CheckpointError, load_checkpoint
from patchpilot.agent.repair import Interrupted
from patchpilot.agent.report import RECORD_FILE, REPORT_FILE
from patchpilot.agent.trace import TRACE_FILE, replay_model
from patchpilot.eval.harness import TaskRun, resume_task, run_task
from patchpilot.llm.budget import SpendLedger
from patchpilot.llm.fake import ScriptedModel
from patchpilot.sandbox import DockerSandbox
from patchpilot.tasks import LoadedTask, load_task
from tests.integration.test_baseline_tasks import TASKS_ROOT
from tests.integration.test_repair_tasks import wrong_then_right

pytestmark = pytest.mark.sandbox

RUN = "inj-001-cachekit-1"
# Checkpoints of the script: three before requests, then evaluate, feedback,
# propose, two before requests, evaluate, done.
AFTER_INVESTIGATION = 2
AFTER_REJECTED_PROPOSAL = 5
BEFORE_NEXT_PROPOSAL = 6
LAST = 10


@pytest.fixture
def cachekit() -> LoadedTask:
    return load_task(TASKS_ROOT / "inj-001-cachekit")


class Processes:
    """One run folder and ledger, driven by several processes in turn."""

    def __init__(self, loaded: LoadedTask, sandbox: DockerSandbox, root: Path) -> None:
        self.loaded, self.sandbox, self.root = loaded, sandbox, root
        self.ledger = SpendLedger(root / "ledger.jsonl", cap_usd=5.0)
        self.directory = root / RUN
        self.requests = 0

    def start(self, interrupt_after: int | None = None) -> TaskRun | None:
        model = ScriptedModel(wrong_then_right())
        try:
            return run_task(
                self.loaded,
                1,
                model=model,
                sandbox=self.sandbox,
                ledger=self.ledger,
                run_cap_usd=0.75,
                output_dir=self.root,
                mode="repair",
                interrupt_after=interrupt_after,
            )
        except Interrupted:
            return None
        finally:
            self.requests += len(model.requests)

    def resume(self, interrupt_after: int | None = None, model: Any = None) -> TaskRun | None:
        answered = load_checkpoint(self.directory).state.generation_attempts
        model = model or ScriptedModel(wrong_then_right()[answered:])
        try:
            return resume_task(
                self.loaded,
                self.directory,
                model=model,
                sandbox=self.sandbox,
                ledger=self.ledger,
                interrupt_after=interrupt_after,
            )
        except Interrupted:
            return None
        finally:
            self.requests += len(model.requests)


def _expected(run: TaskRun | None) -> None:
    assert run is not None
    record = run.record
    assert record.run_id == RUN and record.status is RunStatus.CANDIDATE_VERIFIED
    assert [attempt.status for attempt in record.attempts] == [
        RunStatus.UNVERIFIED,
        RunStatus.CANDIDATE_VERIFIED,
    ]
    assert (record.model_turns, record.generation_attempts, record.tool_calls) == (5, 5, 4)
    assert run.graded and run.resolved and run.grade_trustworthy
    assert "+            self._entries.move_to_end(key)" in record.diff


@pytest.mark.parametrize(
    "boundary", [AFTER_INVESTIGATION, AFTER_REJECTED_PROPOSAL, BEFORE_NEXT_PROPOSAL]
)
def test_a_stopped_run_resumes_to_a_resolved_patch_without_repeating_work(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path, boundary: int
) -> None:
    processes = Processes(cachekit, sandbox, tmp_path)

    assert processes.start(interrupt_after=boundary) is None
    stored = load_checkpoint(processes.directory)
    assert not (processes.directory / RECORD_FILE).exists(), "an unfinished run has no record"
    run = processes.resume()

    _expected(run)
    assert stored.sequence == boundary
    assert processes.requests == 5, "every answer was asked for exactly once"
    assert processes.ledger.run_requests(RUN)[:2] == (5, 0)
    assert processes.ledger.committed() == run.record.cost_micro_usd  # type: ignore[union-attr]
    report = (processes.directory / REPORT_FILE).read_text()
    assert "Independent grading (hidden tests):** resolved" in report
    assert "| 1 | `unverified` |" in report and "| 2 | `candidate_verified` |" in report


def test_repeated_stops_and_a_finished_run_do_not_duplicate_anything(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    processes = Processes(cachekit, sandbox, tmp_path)

    assert processes.start(interrupt_after=AFTER_INVESTIGATION) is None
    assert processes.resume(interrupt_after=3) is None
    assert processes.resume(interrupt_after=3) is None
    finished = processes.resume()
    nobody = ScriptedModel([])
    again = processes.resume(model=nobody)

    _expected(finished)
    _expected(again)
    assert processes.requests == 5 and nobody.requests == []
    assert load_checkpoint(processes.directory).state.phase == "done"
    assert processes.ledger.run_requests(RUN)[:2] == (5, 0)


def test_a_resumed_run_replays_and_grades_like_an_uninterrupted_one(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    whole = Processes(cachekit, sandbox, tmp_path / "whole").start()
    processes = Processes(cachekit, sandbox, tmp_path / "stopped")
    processes.start(interrupt_after=AFTER_REJECTED_PROPOSAL)
    resumed = processes.resume()

    replayed = run_task(
        cachekit,
        1,
        model=replay_model(processes.directory / TRACE_FILE),
        sandbox=sandbox,
        ledger=SpendLedger(tmp_path / "replay" / "ledger.jsonl", cap_usd=5.0),
        run_cap_usd=0.75,
        output_dir=tmp_path / "replay",
        mode="repair",
    )

    assert whole is not None and resumed is not None
    for run in (resumed, replayed):
        assert (run.record.status, run.record.diff, run.resolved) == (
            whole.record.status,
            whole.record.diff,
            whole.resolved,
        )
        assert run.record.usage == whole.record.usage
        assert run.record.cost_micro_usd == whole.record.cost_micro_usd


def test_a_checkpoint_holds_nothing_from_hidden_tests(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    processes = Processes(cachekit, sandbox, tmp_path)
    processes.start()

    stored = (processes.directory / CHECKPOINT_FILE).read_text()
    # The scripted answers name files; what is checked is what a run could learn.
    forbidden = ["reference_fix", "fail_to_pass", "pass_to_pass", "hidden"]
    for node_id in cachekit.task.evaluation.fail_to_pass:
        forbidden.append(node_id.split("::")[1])
    for content in cachekit.hidden_test_files().values():
        forbidden.extend(line for line in content.decode().splitlines() if len(line) > 40)
    for needle in forbidden:
        assert needle not in stored, needle
    assert json.loads(stored)["checkpoint"]["state"]["status"] == "candidate_verified"


def test_a_checkpoint_of_another_task_or_another_run_is_refused(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    processes = Processes(cachekit, sandbox, tmp_path / "a")
    processes.start(interrupt_after=AFTER_REJECTED_PROPOSAL)
    other = Processes(cachekit, sandbox, tmp_path / "b")
    other.start(interrupt_after=AFTER_INVESTIGATION)
    nobody = ScriptedModel([])

    with pytest.raises(CheckpointError, match="is for task inj-001-cachekit, not inj-002"):
        resume_task(
            load_task(TASKS_ROOT / "inj-002-exprcalc"),
            processes.directory,
            model=nobody,
            sandbox=sandbox,
            ledger=processes.ledger,
        )
    # A checkpoint moved next to the trace of a different run of the same task.
    shutil.copy(other.directory / CHECKPOINT_FILE, processes.directory / CHECKPOINT_FILE)
    with pytest.raises(CheckpointError, match="stale"):
        processes.resume(model=nobody)
    (processes.directory / TRACE_FILE).write_text(
        (processes.directory / TRACE_FILE).read_text().replace(RUN, "another-run", 1)
    )
    with pytest.raises(CheckpointError, match="from different runs"):
        processes.resume(model=nobody)
    assert nobody.requests == []


def test_the_commands_stop_and_resume_a_run_end_to_end(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded = Processes(cachekit, sandbox, tmp_path / "recorded")
    recorded.start()
    trace = str(recorded.directory / TRACE_FILE)
    runs = tmp_path / "runs"
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(runs))
    monkeypatch.setattr(cli, "_live_model", lambda settings: pytest.fail("a live model was used"))
    runner = CliRunner()

    stopped = runner.invoke(
        cli.app,
        ["run", "--task", "inj-001-cachekit", "--replay", trace, "--interrupt-after", "5"],
    )
    (directory,) = (path.parent for path in runs.glob(f"*/{RUN}/{CHECKPOINT_FILE}"))
    outside = runner.invoke(cli.app, ["resume", str(recorded.directory), "--replay", trace])
    resumed = runner.invoke(cli.app, ["resume", str(directory), "--replay", trace])
    again = runner.invoke(cli.app, ["resume", str(directory), "--replay", trace])

    assert stopped.exit_code == 3, stopped.output
    assert "interrupted: stopped on purpose after 5 checkpoints" in stopped.output
    assert f"resume with: patchpilot resume {directory}" in stopped.output
    assert outside.exit_code == 2 and "only from inside the runs folder" in outside.output
    for result, sequence in ((resumed, 5), (again, LAST)):
        assert result.exit_code == 0, result.output
        assert f"resumed from checkpoint {sequence} of run {RUN}" in result.output
        assert "status: candidate_verified" in result.output
        assert "hidden tests: resolved" in result.output
    assert SpendLedger(runs / "ledger.jsonl", 5.0).run_requests(RUN)[:2] == (5, 0)
