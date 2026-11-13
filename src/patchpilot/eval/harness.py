"""Run a repair mode over evaluation tasks and grade the results independently.

For each run the agent works only from the task's agent-visible view and its
repository files. Grading with hidden tests happens after the run has ended
and its record is final; nothing from grading is ever passed back to a model.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

from patchpilot.agent.baseline import RunLimits, RunRecord, RunStatus, run_baseline
from patchpilot.agent.prompts import BugDescription
from patchpilot.agent.report import write_run
from patchpilot.agent.trace import TraceWriter, secrets_from_environment
from patchpilot.llm.base import ModelClient
from patchpilot.llm.budget import RunBudget, SpendLedger, usd
from patchpilot.sandbox.models import FrozenModel, SandboxRunner
from patchpilot.tasks.evaluate import grade_candidate
from patchpilot.tasks.loader import LoadedTask

ModelFactory = Callable[[LoadedTask, int], ModelClient]


class TaskRun(FrozenModel):
    task_id: str
    run_index: int
    record: RunRecord
    graded: bool
    resolved: bool
    grade_trustworthy: bool | None
    localization_hit: bool | None


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
) -> TaskRun:
    """Run the baseline once on a task, then grade the result with hidden tests."""
    view = loaded.task.agent_view()
    files = loaded.agent_files()
    run_id = f"{view.task_id}-{run_index}"
    directory = output_dir / run_id
    secrets = secrets_from_environment()
    record, patched = run_baseline(
        task_id=view.task_id,
        bug=BugDescription.from_issue(view.issue),
        files=files,
        source_roots=view.environment.source_roots,
        commands=view.commands,
        sandbox_limits=view.budgets,
        model=model,
        sandbox=sandbox,
        budget=RunBudget(ledger, run_id, run_cap_usd),
        trace=TraceWriter(directory, secrets),
        limits=limits,
        run_cap_usd=run_cap_usd,
        run_id=run_id,
    )

    # The run is over and its record is final. Only now are hidden tests used.
    resolved = False
    trustworthy: bool | None = None
    localization: bool | None = None
    if patched is not None:
        grade, _ = grade_candidate(loaded, patched, sandbox)
        resolved, trustworthy = grade.resolved, grade.trustworthy
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
        )
        for loaded in tasks
        for index in range(1, runs_per_task + 1)
    ]
    return EvalSummary(mode="baseline", runs=tuple(runs))


def summary_table(summary: EvalSummary) -> str:
    """Return the results as Markdown. Counts, not percentages: the task set is small."""
    total = len(summary.runs)
    lines = [
        f"# Evaluation: {summary.mode}",
        "",
        f"{total} runs. Results are counts; with this few tasks they show whether the "
        "pipeline works and what it costs, not how good it is.",
        "",
        "| Task | Run | Status | Resolved (hidden tests) | Right file edited | Turns | Tool calls "
        "| Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for run in summary.runs:
        record = run.record
        resolved = ("yes" if run.resolved else "no") if run.graded else "not graded"
        located = {True: "yes", False: "no", None: "n/a"}[run.localization_hit]
        lines.append(
            f"| {run.task_id} | {run.run_index} | {record.status.value} | {resolved} | {located} "
            f"| {record.model_turns} | {record.tool_calls} | ${usd(record.cost_micro_usd):.4f} |"
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
        "",
    ]
    return "\n".join(lines)
