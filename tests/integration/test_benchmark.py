"""The scripted benchmark on real fixtures, in the real sandbox. No model is called."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import patchpilot.cli as cli
from patchpilot.eval.benchmark import LABEL, run_benchmark, scenarios
from patchpilot.sandbox import DockerSandbox
from patchpilot.tasks import load_task
from tests.integration.test_baseline_tasks import TASKS_ROOT

pytestmark = pytest.mark.sandbox
SCRIPTED = TASKS_ROOT.parent / "scripted"


def test_every_task_has_scripted_answers_and_the_right_one_is_its_reference_fix() -> None:
    for task_dir in sorted(TASKS_ROOT.iterdir()):
        loaded = load_task(task_dir)
        made = scenarios(loaded, SCRIPTED)
        assert set(made) == {
            "right-first",
            "wrong-then-right",
            "policy-then-right",
            "probe-hidden-then-right",
            "no-usable-answer",
        }
        visible = {
            line.strip()
            for content in loaded.agent_files().values()
            for line in content.decode().splitlines()
        }
        only_hidden = {
            line.strip()
            for content in loaded.hidden_test_files().values()
            for line in content.decode().splitlines()
            if len(line.strip()) > 25 and line.strip() not in visible
        }
        data = json.loads((SCRIPTED / f"{loaded.task.task_id}.json").read_text())
        written = data["regression_test"]["content"] + data["wrong"]["replacement"]
        for line in only_hidden:
            assert line not in written, f"a scripted answer copies a hidden test: {line}"


def test_the_benchmark_compares_both_modes_and_labels_itself(
    sandbox: DockerSandbox, tmp_path: Path
) -> None:
    tasks = [load_task(TASKS_ROOT / "inj-004-pager")]

    result = run_benchmark(
        tasks, sandbox, tmp_path, scripted_root=SCRIPTED, only=["wrong-then-right"]
    )

    stored = json.loads((tmp_path / "benchmark.json").read_text())
    table = (tmp_path / "benchmark.md").read_text()
    baseline, repair = stored["rows"]
    assert stored == json.loads(json.dumps(result)) and stored["label"] == LABEL
    assert LABEL in table and "not the success rate of any language model" in table
    assert (baseline["mode"], baseline["status"], baseline["resolved"]) == (
        "baseline",
        "unverified",
        False,
    )
    assert baseline["pass_to_pass"] == [4, 7], "the wrong fix breaks exact multiples"
    assert (repair["status"], repair["resolved"], repair["proposals"]) == (
        "candidate_verified",
        True,
        2,
    )
    assert repair["fail_to_pass"] == [2, 2] and repair["pass_to_pass"] == [7, 7]
    assert repair["sandbox_runs_including_grading"] == 6
    assert stored["summary"]["repair"]["resolved_by_hidden_tests"] == 1
    assert not (TASKS_ROOT.parent.parent / "runs" / "benchmark.json").exists()


def test_the_command_runs_only_the_scripted_benchmark(
    sandbox: DockerSandbox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATCHPILOT_RUNS_DIR", str(tmp_path / "runs"))
    runner = CliRunner()

    refused = runner.invoke(cli.app, ["benchmark"])
    ran = runner.invoke(
        cli.app,
        ["benchmark", "--scripted", "--scenario", "right-first", "--output", str(tmp_path / "out")],
    )
    run_dir = tmp_path / "runs" / "shown"
    run_dir.mkdir(parents=True)
    source = tmp_path / "out" / "inj-001-cachekit" / "right-first" / "repair" / "inj-001-cachekit-1"
    for name in ("record.json", "checkpoint.json"):
        (run_dir / name).write_bytes((source / name).read_bytes())
    shown = runner.invoke(cli.app, ["show", str(run_dir)])

    assert refused.exit_code == 2 and "--scripted" in refused.output
    assert ran.exit_code == 0, ran.output
    assert "SCRIPTED / OFFLINE" in ran.output and "baseline: 4 of 4 resolved" in ran.output
    assert shown.exit_code == 0, shown.output
    for fragment in (
        "mode repair",
        "status: candidate_verified",
        "1. candidate_verified",
        "hidden tests: resolved",
        "phase done",
        "pull request proposal: none",
        "submission: none",
    ):
        assert fragment in shown.output, fragment
