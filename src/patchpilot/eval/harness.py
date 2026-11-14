"""Run a repair mode over evaluation tasks and grade the results independently.

For each run the agent works only from the task's agent-visible view and its
repository files. Grading with hidden tests happens after the run has ended
and its record is final; nothing from grading is ever passed back to a model.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal

from patchpilot.agent.baseline import (
    RepairLimits,
    RunLimits,
    RunRecord,
    RunStatus,
    run_baseline,
)
from patchpilot.agent.checkpoint import CheckpointError, load_checkpoint
from patchpilot.agent.prompts import BugDescription
from patchpilot.agent.repair import resume_repair, run_repair
from patchpilot.agent.report import write_run
from patchpilot.agent.trace import TRACE_FILE, TraceWriter, read_trace, secrets_from_environment
from patchpilot.llm.base import ModelClient
from patchpilot.llm.budget import RunBudget, SpendLedger, usd
from patchpilot.sandbox.models import FrozenModel, SandboxRunner
from patchpilot.tasks.evaluate import grade_candidate
from patchpilot.tasks.loader import LoadedTask

ModelFactory = Callable[[LoadedTask, int], ModelClient]
Mode = Literal["baseline", "repair"]


class TaskRun(FrozenModel):
    task_id: str
    run_index: int
    record: RunRecord
    graded: bool
    resolved: bool
    grade_trustworthy: bool | None
    localization_hit: bool | None
    # Hidden-test detail: fail-to-pass tests now passing, pass-to-pass tests kept.
    fail_to_pass: tuple[int, int] | None = None
    pass_to_pass: tuple[int, int] | None = None


class EvalSummary(FrozenModel):
    mode: str
    runs: tuple[TaskRun, ...]

    def count(self, status: RunStatus) -> int:
        return sum(1 for run in self.runs if run.record.status is status)

    @property
    def resolved(self) -> int:
        return sum(1 for run in self.runs if run.resolved)

    @property
    def verified_but_not_resolved(self) -> int:
        """Runs the agent called verified that hidden tests did not confirm."""
        return sum(
            1
            for run in self.runs
            if run.record.status is RunStatus.CANDIDATE_VERIFIED and not run.resolved
        )

    @property
    def cost_micro_usd(self) -> int:
        return sum(run.record.cost_micro_usd for run in self.runs)


def run_task(
    loaded: LoadedTask,
    run_index: int,
    *,
    model: ModelClient,
    sandbox: SandboxRunner,
    ledger: SpendLedger,
    run_cap_usd: float,
    output_dir: Path,
    limits: RunLimits | None = None,
    mode: Mode = "baseline",
    repair: RepairLimits | None = None,
    interrupt_after: int | None = None,
) -> TaskRun:
    """Run one mode once on a task, then grade the result with hidden tests."""
    view = loaded.task.agent_view()
    files = loaded.agent_files()
    run_id = f"{view.task_id}-{run_index}"
    directory = output_dir / run_id
    secrets = secrets_from_environment()
    common: dict[str, Any] = {
        "task_id": view.task_id,
        "bug": BugDescription.from_issue(view.issue),
        "files": files,
        "source_roots": view.environment.source_roots,
        "commands": view.commands,
        "sandbox_limits": view.budgets,
        "model": model,
        "sandbox": sandbox,
        "budget": RunBudget(ledger, run_id, run_cap_usd),
        "trace": TraceWriter(directory, secrets),
        "limits": limits,
        "run_cap_usd": run_cap_usd,
        "run_id": run_id,
    }
    if mode == "repair":
        # A repair run stores its state in its own directory, so it can be resumed.
        record, patched = run_repair(
            **common,
            repair=repair,
            checkpoint_dir=directory,
            secrets=secrets,
            interrupt_after=interrupt_after,
        )
    else:
        record, patched = run_baseline(**common)
    return _grade(loaded, run_index, record, patched, directory, secrets, sandbox)


def resume_task(
    loaded: LoadedTask,
    directory: Path,
    *,
    model: ModelClient,
    sandbox: SandboxRunner,
    ledger: SpendLedger,
    interrupt_after: int | None = None,
) -> TaskRun:
    """Continue an interrupted repair run from its directory, then grade it.

    The checkpoint must belong to this task and to the trace beside it. A
    run that had already finished is not run again: its result is rebuilt
    from the checkpoint and graded.
    """
    checkpoint = load_checkpoint(directory)
    view = loaded.task.agent_view()
    if checkpoint.task_id != view.task_id:
        raise CheckpointError(
            f"the checkpoint is for task {checkpoint.task_id}, not {view.task_id}"
        )
    started = next(
        (event for event in read_trace(directory / TRACE_FILE) if event.get("type") == "start"),
        {},
    )
    identity = ("run_id", "task_id", "repository_tree_sha256", "prompt_sha256", "config_sha256")
    if any(started.get(name) != getattr(checkpoint, name) for name in identity):
        raise CheckpointError("the checkpoint and the trace beside it are from different runs")
    secrets = secrets_from_environment()
    record, patched = resume_repair(
        checkpoint=checkpoint,
        checkpoint_dir=directory,
        files=loaded.agent_files(),
        source_roots=view.environment.source_roots,
        commands=view.commands,
        sandbox_limits=view.budgets,
        model=model,
        sandbox=sandbox,
        ledger=ledger,
        trace=TraceWriter(directory, secrets, append=True),
        secrets=secrets,
        interrupt_after=interrupt_after,
    )
    index = checkpoint.run_id.rsplit("-", 1)[-1]
    return _grade(
        loaded, int(index) if index.isdigit() else 1, record, patched, directory, secrets, sandbox
    )


def _grade(
    loaded: LoadedTask,
    run_index: int,
    record: RunRecord,
    patched: dict[str, bytes] | None,
    directory: Path,
    secrets: Sequence[str],
    sandbox: SandboxRunner,
) -> TaskRun:
    """Grade a finished run with hidden tests and write its record and report."""
    view = loaded.task.agent_view()
    files = loaded.agent_files()
    # The run is over and its record is final. Only now are hidden tests used.
    resolved = False
    trustworthy: bool | None = None
    localization: bool | None = None
    fail_to_pass: tuple[int, int] | None = None
    pass_to_pass: tuple[int, int] | None = None
    if patched is not None:
        grade, _ = grade_candidate(loaded, patched, sandbox)
        resolved, trustworthy = grade.resolved, grade.trustworthy
        evaluation = loaded.task.evaluation
        broken = set(grade.still_failing) | set(grade.missing)
        targets = len(evaluation.fail_to_pass)
        kept = len(evaluation.pass_to_pass)
        fail_to_pass = (targets - len(broken & set(evaluation.fail_to_pass)), targets)
        pass_to_pass = (
            kept - len((set(grade.regressions) | broken) & set(evaluation.pass_to_pass)),
            kept,
        )
        fixed = {
            path
            for path, content in loaded.reference_fix_files().items()
            if files.get(path) != content
        }
        localization = bool(fixed & set(record.changed_files))
    write_run(record, directory, resolved if patched is not None else None, secrets)
    return TaskRun(
        task_id=view.task_id,
        run_index=run_index,
        record=record,
        graded=patched is not None,
        resolved=resolved,
        grade_trustworthy=trustworthy,
        localization_hit=localization,
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
    )


def evaluate(
    tasks: Sequence[LoadedTask],
    *,
    runs_per_task: int,
    model_factory: ModelFactory,
    sandbox: SandboxRunner,
    ledger: SpendLedger,
    run_cap_usd: float,
    output_dir: Path,
    limits: RunLimits | None = None,
    mode: Mode = "baseline",
    repair: RepairLimits | None = None,
) -> EvalSummary:
    runs = [
        run_task(
            loaded,
            index,
            model=model_factory(loaded, index),
            sandbox=sandbox,
            ledger=ledger,
            run_cap_usd=run_cap_usd,
            output_dir=output_dir,
            limits=limits,
            mode=mode,
            repair=repair,
        )
        for loaded in tasks
        for index in range(1, runs_per_task + 1)
    ]
    return EvalSummary(mode=mode, runs=tuple(runs))


def proposals(record: RunRecord) -> int:
    """Return how many proposals a run made."""
    if record.mode == "repair":
        return len(record.attempts)
    return 1 if record.proposal is not None else 0


def comparison_table(baseline: EvalSummary, repair: EvalSummary) -> str:
    """Set the two modes side by side, task by task. Counts, not percentages."""
    lines = [
        "# Baseline against repair loop",
        "",
        "Same tasks, tools, patch rules, sandbox and hidden-test grading. The repair "
        "loop differs in one thing: after a proposal that is not accepted, the model "
        "is told why and may propose again.",
        "",
        "| Task | Mode | Runs | Verified by the agent | Resolved (hidden tests) | Proposals "
        "| Turns | Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    tasks = sorted({run.task_id for run in (*baseline.runs, *repair.runs)})
    for task in tasks:
        for summary in (baseline, repair):
            runs = [run for run in summary.runs if run.task_id == task]
            if not runs:
                continue
            verified = sum(run.record.status is RunStatus.CANDIDATE_VERIFIED for run in runs)
            lines.append(
                f"| {task} | {summary.mode} | {len(runs)} | {verified} "
                f"| {sum(run.resolved for run in runs)} "
                f"| {sum(proposals(run.record) for run in runs)} "
                f"| {sum(run.record.model_turns for run in runs)} "
                f"| ${usd(sum(run.record.cost_micro_usd for run in runs)):.4f} |"
            )
    lines += [""]
    for summary in (baseline, repair):
        lines.append(
            f"- {summary.mode}: {summary.resolved} of {len(summary.runs)} resolved by hidden "
            f"tests, {summary.count(RunStatus.CANDIDATE_VERIFIED)} verified by the agent, "
            f"{summary.verified_but_not_resolved} verified but not resolved, "
            f"total cost ${usd(summary.cost_micro_usd):.4f}"
        )
    lines.append("")
    return "\n".join(lines)


def summary_table(summary: EvalSummary) -> str:
    """Return the results as Markdown. Counts, not percentages: the task set is small."""
    total = len(summary.runs)
    models = sorted(
        {
            f"{run.record.manifest.provider}/{run.record.manifest.model_requested}"
            for run in summary.runs
        }
    )
    lines = [
        f"# Evaluation: {summary.mode}",
        "",
        f"{total} runs. Results are counts; with this few tasks they show whether the "
        "pipeline works and what it costs, not how good it is.",
        "",
        "| Task | Run | Status | Resolved (hidden tests) | Right file edited | Proposals | Turns "
        "| Tool calls | Cost |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for run in summary.runs:
        record = run.record
        resolved = ("yes" if run.resolved else "no") if run.graded else "not graded"
        located = {True: "yes", False: "no", None: "n/a"}[run.localization_hit]
        lines.append(
            f"| {run.task_id} | {run.run_index} | {record.status.value} | {resolved} | {located} "
            f"| {proposals(record)} | {record.model_turns} | {record.tool_calls} "
            f"| ${usd(record.cost_micro_usd):.4f} |"
        )
    lines += [
        "",
        f"- Resolved by hidden tests: {summary.resolved} of {total}",
        f"- Verified by the agent's own evidence: "
        f"{summary.count(RunStatus.CANDIDATE_VERIFIED)} of {total}",
        f"- Verified by the agent but not resolved: {summary.verified_but_not_resolved}",
        f"- Proposals that could not be applied: {summary.count(RunStatus.INVALID_PROPOSAL)}",
        f"- Rejected by patch policy: {summary.count(RunStatus.REJECTED_POLICY)}",
        f"- Total cost: ${usd(summary.cost_micro_usd):.4f}",
        f"- Provider and model: {', '.join(models) or 'none'}. Results from different "
        "models are different experiments and must not be pooled.",
        "",
    ]
    return "\n".join(lines)
