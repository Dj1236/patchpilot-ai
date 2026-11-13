"""The repair loop end to end on the real fixtures, in the real sandbox.

The model is scripted, so nothing here says how well a real model repairs
code. These tests show what the loop does with a script that gets the fix
wrong first and right second, and where the loop cannot help.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from patchpilot.agent.baseline import RepairLimits, RunStatus
from patchpilot.agent.report import REPORT_FILE
from patchpilot.agent.trace import TRACE_FILE, read_trace, replay_model
from patchpilot.eval.harness import (
    Mode,
    TaskRun,
    comparison_table,
    evaluate,
    proposals,
    run_task,
)
from patchpilot.llm.budget import SpendLedger
from patchpilot.llm.fake import Scripted, ScriptedModel, final_turn, tool_turn
from patchpilot.sandbox import DockerSandbox
from patchpilot.tasks import LoadedTask, load_task
from tests.integration.test_baseline_tasks import CACHE_BUG, EXPLORE, TASKS_ROOT, cache_proposal

pytestmark = pytest.mark.sandbox

# A change in the right place that does not fix the bug.
WRONG = CACHE_BUG.replace(
    "            return\n", "            self.hits += 0\n            return\n"
)
WRONG_EDITS = [
    {"path": "src/cachekit/lru.py", "expected_original": CACHE_BUG, "replacement": WRONG}
]
LOOK_AGAIN = tool_turn(("read_file", {"path": "src/cachekit/lru.py"}))


def wrong_then_right() -> list[Scripted]:
    return [
        *EXPLORE,
        final_turn(cache_proposal(edits=WRONG_EDITS, summary="A first guess.")),
        LOOK_AGAIN,
        final_turn(cache_proposal()),
    ]


@pytest.fixture
def cachekit() -> LoadedTask:
    return load_task(TASKS_ROOT / "inj-001-cachekit")


@pytest.fixture
def exprcalc() -> LoadedTask:
    return load_task(TASKS_ROOT / "inj-002-exprcalc")


def partial_fix(loaded: LoadedTask) -> dict[str, Any]:
    """A fix for subtraction only, with a matching test. Division stays wrong."""
    source = loaded.agent_files()["src/exprcalc/parser.py"].decode()
    start = source.index("    def _expression(self) -> float:")
    end = source.index("    def _term(self) -> float:")
    replacement = (
        "    def _expression(self) -> float:\n"
        "        value = self._term()\n"
        "        while True:\n"
        '            if self._accept("+"):\n'
        "                value += self._term()\n"
        '            elif self._accept("-"):\n'
        "                value -= self._term()\n"
        "            else:\n"
        "                return value\n\n"
    )
    return {
        "summary": "Apply subtraction left to right.",
        "root_cause": "The expression rule recursed to the right.",
        "relevant_files": ["src/exprcalc/parser.py"],
        "edits": [
            {
                "path": "src/exprcalc/parser.py",
                "expected_original": source[start:end],
                "replacement": replacement,
            }
        ],
        "regression_tests": [
            {
                "path": "tests/test_subtraction_chain.py",
                "content": "from exprcalc import evaluate\n\n\ndef test_chain():\n"
                "    assert evaluate('10 - 4 - 3') == 3.0\n",
            }
        ],
    }


def _run(
    loaded: LoadedTask,
    model: ScriptedModel,
    sandbox: DockerSandbox,
    directory: Path,
    mode: Mode = "repair",
    repair: RepairLimits | None = None,
) -> tuple[TaskRun, Path]:
    run = run_task(
        loaded,
        1,
        model=model,
        sandbox=sandbox,
        ledger=SpendLedger(directory / "ledger.jsonl", cap_usd=5.0),
        run_cap_usd=0.75,
        output_dir=directory,
        mode=mode,
        repair=repair,
    )
    return run, directory / f"{loaded.task.task_id}-1"


def test_feedback_from_real_tests_turns_a_wrong_patch_into_a_resolved_one(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    baseline, _ = _run(
        cachekit, ScriptedModel(wrong_then_right()), sandbox, tmp_path / "baseline", "baseline"
    )
    model = ScriptedModel(wrong_then_right())
    repaired, directory = _run(cachekit, model, sandbox, tmp_path / "repair")

    # The baseline stops at the first proposal: not verified, not resolved.
    assert baseline.record.status is RunStatus.UNVERIFIED
    assert baseline.graded and not baseline.resolved and proposals(baseline.record) == 1

    record = repaired.record
    assert record.status is RunStatus.CANDIDATE_VERIFIED and record.mode == "repair"
    assert [attempt.status for attempt in record.attempts] == [
        RunStatus.UNVERIFIED,
        RunStatus.CANDIDATE_VERIFIED,
    ]
    assert repaired.graded and repaired.resolved and repaired.grade_trustworthy
    assert repaired.localization_hit is True
    assert "+            self._entries.move_to_end(key)" in record.diff
    assert "self.hits += 0" not in record.diff, "the first proposal's edits were discarded"

    # What the model was told came from its own regression test, run for real.
    told = model.requests[3].messages[-1]["content"]
    assert "Proposal 1 was not accepted: the checks in the sandbox did not hold." in told
    assert "<untrusted_test_output>" in told
    assert "tests/test_rewrite_recency.py::test_rewritten_key_survives_eviction" in told
    assert "assert 'a' in" in told
    report = (directory / REPORT_FILE).read_text()
    assert "## Proposals" in report and "| 1 | `unverified` |" in report
    assert "Independent grading (hidden tests):** resolved" in report


def test_nothing_from_hidden_tests_reaches_the_model_in_a_repair_run(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    model = ScriptedModel(wrong_then_right())

    run, _ = _run(cachekit, model, sandbox, tmp_path)

    given = [model.requests[0].system]
    for request in model.requests:
        given.extend(
            json.dumps(message) for message in request.messages if message["role"] == "user"
        )
    provided = "\n".join(given)
    forbidden = ["reference_fix", "fail_to_pass", "pass_to_pass", "hidden", "resolved"]
    for node_id in cachekit.task.evaluation.fail_to_pass:
        forbidden.append(node_id.split("::")[1])
    for content in cachekit.hidden_test_files().values():
        forbidden.extend(line for line in content.decode().splitlines() if len(line) > 40)
    for needle in forbidden:
        assert needle not in provided, needle
    assert run.resolved


def test_the_loop_cannot_repair_what_its_own_checks_accept(
    sandbox: DockerSandbox, exprcalc: LoadedTask, tmp_path: Path
) -> None:
    """A partial fix with a matching test is verified at once, so no feedback follows."""
    model = ScriptedModel([final_turn(partial_fix(exprcalc)), final_turn("never asked")])

    run, _ = _run(exprcalc, model, sandbox, tmp_path)

    assert run.record.status is RunStatus.CANDIDATE_VERIFIED
    assert len(run.record.attempts) == 1 and len(model.requests) == 1
    assert run.graded and not run.resolved, "hidden tests still catch the division bug"


def test_a_run_that_uses_up_its_proposals_is_graded_on_the_last_one(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path
) -> None:
    wrong = final_turn(cache_proposal(edits=WRONG_EDITS))

    run, _ = _run(
        cachekit,
        ScriptedModel([wrong, wrong, wrong]),
        sandbox,
        tmp_path,
        repair=RepairLimits(max_attempts=2),
    )

    assert run.record.status is RunStatus.UNVERIFIED
    assert "no verified patch after 2 proposals" in run.record.detail
    assert len(run.record.attempts) == 2 and run.graded and not run.resolved


def test_a_repair_run_replays_from_its_trace_through_the_command(
    sandbox: DockerSandbox, cachekit: LoadedTask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import patchpilot.cli as cli

    original, directory = _run(
        cachekit, ScriptedModel(wrong_then_right()), sandbox, tmp_path / "recorded"
    )
    trace = directory / TRACE_FILE
    again, _ = _run(cachekit, replay_model(trace), sandbox, tmp_path / "again")
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(cli, "_live_model", lambda settings: pytest.fail("a live model was used"))

    # No --mode is given: a trace is replayed in the mode it was recorded in.
    result = CliRunner().invoke(
        cli.app, ["run", "--task", "inj-001-cachekit", "--replay", str(trace)]
    )

    assert (again.record.status, again.record.diff, again.resolved) == (
        original.record.status,
        original.record.diff,
        original.resolved,
    )
    assert [attempt.status for attempt in again.record.attempts] == [
        attempt.status for attempt in original.record.attempts
    ]
    assert sum(event["type"] == "transition" for event in read_trace(trace)) == 5
    assert result.exit_code == 0, result.output
    assert "status: candidate_verified" in result.output
    assert "hidden tests: resolved" in result.output


def test_the_two_modes_are_compared_on_the_same_tasks(
    sandbox: DockerSandbox, cachekit: LoadedTask, exprcalc: LoadedTask, tmp_path: Path
) -> None:
    scripts: dict[str, Callable[[], list[Scripted]]] = {
        "inj-001-cachekit": wrong_then_right,
        "inj-002-exprcalc": lambda: [final_turn(partial_fix(exprcalc))],
    }

    def summary(mode: Mode) -> Any:
        return evaluate(
            [cachekit, exprcalc],
            runs_per_task=1,
            model_factory=lambda loaded, index: ScriptedModel(scripts[loaded.task.task_id]()),
            sandbox=sandbox,
            ledger=SpendLedger(tmp_path / mode / "ledger.jsonl", cap_usd=5.0),
            run_cap_usd=0.75,
            output_dir=tmp_path / mode,
            mode=mode,
        )

    baseline, repair = summary("baseline"), summary("repair")
    table = comparison_table(baseline, repair)

    assert (baseline.mode, repair.mode) == ("baseline", "repair")
    assert (baseline.resolved, repair.resolved) == (0, 1)
    assert baseline.verified_but_not_resolved == repair.verified_but_not_resolved == 1
    assert "| inj-001-cachekit | baseline | 1 | 0 | 0 | 1 |" in table
    assert "| inj-001-cachekit | repair | 1 | 1 | 1 | 2 |" in table
    assert "| inj-002-exprcalc | repair | 1 | 1 | 0 | 1 |" in table
    assert "- baseline: 0 of 2 resolved by hidden tests" in table
    assert "- repair: 1 of 2 resolved by hidden tests" in table
    print("\n" + table)
