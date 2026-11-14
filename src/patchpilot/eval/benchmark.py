"""The scripted, offline benchmark: baseline against repair on the same tasks.

The "model" here is a script of hand-written answers (``evals/scripted``),
so these results show what the pipeline does with known kinds of answers.
**They are not model results** and say nothing about how well a real model
repairs code. Every output is labelled as scripted.

Each task is run with each scenario in both modes, with the real sandbox and
the real hidden-test grading, and the same limits. The output is a JSON
file for machines and a Markdown table for people.
"""

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from patchpilot.agent.baseline import RunStatus
from patchpilot.eval.harness import Mode, proposals, run_task
from patchpilot.llm.budget import SpendLedger
from patchpilot.llm.fake import Scripted, ScriptedModel, final_turn, tool_turn
from patchpilot.sandbox.models import PytestCommand, SandboxLimits, SandboxResult, SandboxRunner
from patchpilot.tasks.loader import LoadedTask

LABEL = (
    "SCRIPTED / OFFLINE. The model is a script of hand-written answers. These results "
    "exercise the pipeline; they are not the success rate of any language model."
)
MODES: tuple[Mode, ...] = ("baseline", "repair")


def _proposal(edits: list[dict[str, str]], tests: list[dict[str, str]], summary: str) -> Any:
    return final_turn(
        {
            "summary": summary,
            "root_cause": "Scripted answer.",
            "relevant_files": sorted({edit["path"] for edit in edits}),
            "edits": edits,
            "regression_tests": tests,
        }
    )


def scenarios(loaded: LoadedTask, scripted_root: Path) -> dict[str, list[Scripted]]:
    """Return the scripted answers of every scenario for one task."""
    data = json.loads((scripted_root / f"{loaded.task.task_id}.json").read_text())
    files = loaded.agent_files()
    right = [
        {"path": path, "expected_original": files[path].decode(), "replacement": fixed.decode()}
        for path, fixed in sorted(loaded.reference_fix_files().items())
        if files.get(path) != fixed
    ]
    test = [data["regression_test"]]
    good = _proposal(right, test, "Scripted correct fix.")
    wrong = _proposal([data["wrong"]], test, "Scripted wrong fix.")
    conftest = {"path": "tests/conftest.py", "content": "x = 1\n"}
    sneaky = _proposal(right, [*test, conftest], "Scripted fix that also adds a conftest.py.")
    probe = tool_turn(
        ("list_files", {"prefix": "internal"}),
        ("read_file", {"path": "../internal/hidden_tests/README"}),
        ("search_code", {"query": "fail_to_pass"}),
    )
    return {
        "right-first": [good],
        "wrong-then-right": [wrong, good],
        "policy-then-right": [sneaky, good],
        "probe-hidden-then-right": [probe, good],
        "no-usable-answer": [final_turn("I am not sure.")] * 5,
    }


class CountingSandbox:
    """Passes runs through and counts them, grading included."""

    def __init__(self, inner: SandboxRunner) -> None:
        self.inner, self.runs = inner, 0

    def run(
        self,
        files: Mapping[str, bytes],
        command: PytestCommand,
        limits: SandboxLimits | None = None,
        *,
        source_roots: Sequence[str] = (),
    ) -> SandboxResult:
        self.runs += 1
        return self.inner.run(files, command, limits, source_roots=source_roots)


def run_benchmark(
    tasks: Sequence[LoadedTask],
    sandbox: SandboxRunner,
    output_dir: Path,
    *,
    scripted_root: Path,
    only: Sequence[str] = (),
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run every task, scenario and mode, write the results, and return them."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger = SpendLedger(output_dir / "ledger.jsonl", cap_usd=5.0)
    rows: list[dict[str, Any]] = []
    for loaded in tasks:
        for name, script in scenarios(loaded, scripted_root).items():
            if only and name not in only:
                continue
            for mode in MODES:
                counter = CountingSandbox(sandbox)
                started = clock()
                run = run_task(
                    loaded,
                    1,
                    model=ScriptedModel(list(script)),
                    sandbox=counter,
                    ledger=ledger,
                    run_cap_usd=0.75,
                    output_dir=output_dir / loaded.task.task_id / name / mode,
                    mode=mode,
                )
                record = run.record
                rows.append(
                    {
                        "task": loaded.task.task_id,
                        "scenario": name,
                        "mode": mode,
                        "status": record.status.value,
                        "verified_by_agent": record.status is RunStatus.CANDIDATE_VERIFIED,
                        "graded": run.graded,
                        "resolved": run.resolved,
                        "fail_to_pass": run.fail_to_pass,
                        "pass_to_pass": run.pass_to_pass,
                        "proposals": proposals(record),
                        "model_turns": record.model_turns,
                        "tool_calls": record.tool_calls,
                        "sandbox_runs_including_grading": counter.runs,
                        "scripted_tokens": record.usage.input_tokens + record.usage.output_tokens,
                        "ledger_micro_usd": record.cost_micro_usd,
                        "seconds": round(clock() - started, 2),
                        "detail": record.detail[:200],
                    }
                )
    result = {
        "label": LABEL,
        "generated_at": datetime.now(UTC).isoformat(),
        "tasks": [loaded.task.task_id for loaded in tasks],
        "rows": rows,
        "summary": {mode: _summary([row for row in rows if row["mode"] == mode]) for mode in MODES},
    }
    (output_dir / "benchmark.json").write_text(json.dumps(result, indent=2) + "\n")
    (output_dir / "benchmark.md").write_text(markdown(result))
    return result


def _summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    statuses = [row["status"] for row in rows]
    return {
        "runs": len(rows),
        "verified_by_agent": sum(row["verified_by_agent"] for row in rows),
        "resolved_by_hidden_tests": sum(row["resolved"] for row in rows),
        "verified_but_not_resolved": sum(
            row["verified_by_agent"] and not row["resolved"] for row in rows
        ),
        "proposals": sum(row["proposals"] for row in rows),
        "model_turns": sum(row["model_turns"] for row in rows),
        "tool_calls": sum(row["tool_calls"] for row in rows),
        "sandbox_runs_including_grading": sum(
            row["sandbox_runs_including_grading"] for row in rows
        ),
        "seconds": round(sum(row["seconds"] for row in rows), 1),
        "statuses": {status: statuses.count(status) for status in sorted(set(statuses))},
    }


def _pair(value: Any) -> str:
    return "n/a" if value is None else f"{value[0]}/{value[1]}"


def markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Benchmark: baseline against repair",
        "",
        f"**{result['label']}**",
        "",
        f"Tasks: {', '.join(result['tasks'])}. Generated {result['generated_at']}.",
        "",
        "| Task | Scenario | Mode | Status | Resolved | Fail-to-pass | Pass-to-pass "
        "| Proposals | Turns | Tools | Sandbox runs | Seconds |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['task']} | {row['scenario']} | {row['mode']} | {row['status']} "
            f"| {'yes' if row['resolved'] else 'no'} | {_pair(row['fail_to_pass'])} "
            f"| {_pair(row['pass_to_pass'])} | {row['proposals']} | {row['model_turns']} "
            f"| {row['tool_calls']} | {row['sandbox_runs_including_grading']} | {row['seconds']} |"
        )
    lines += ["", "## Totals", ""]
    for mode, summary in result["summary"].items():
        lines.append(
            f"- **{mode}:** {summary['resolved_by_hidden_tests']} of {summary['runs']} resolved "
            f"by hidden tests, {summary['verified_by_agent']} verified by the agent, "
            f"{summary['verified_but_not_resolved']} verified but not resolved, "
            f"{summary['proposals']} proposals, {summary['sandbox_runs_including_grading']} "
            f"sandbox runs, {summary['seconds']} s. Statuses: {summary['statuses']}."
        )
    lines += [
        "",
        "Token counts and ledger amounts in the JSON come from the script's placeholder "
        "usage figures and mean nothing. Sandbox runs include hidden-test grading.",
        "",
    ]
    return "\n".join(lines)
