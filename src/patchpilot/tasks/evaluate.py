"""Independent grading of a candidate tree with hidden tests.

This is evaluator code. It runs after an agent has finished and never feeds
anything back to it.
"""

from collections.abc import Mapping

from patchpilot.sandbox.docker import DockerSandbox
from patchpilot.sandbox.models import CaseOutcome, FrozenModel, SandboxResult, SandboxStatus
from patchpilot.tasks.loader import LoadedTask
from patchpilot.tasks.schema import Evaluation


class Grade(FrozenModel):
    """Outcome of grading one candidate tree against a task's hidden tests."""

    resolved: bool
    trustworthy: bool
    still_failing: tuple[str, ...]
    regressions: tuple[str, ...]
    missing: tuple[str, ...]
    report_issues: tuple[str, ...] = ()
    tampered: tuple[str, ...] = ()
    ignored: tuple[str, ...] = ()
    status: SandboxStatus


class FixtureCheck(FrozenModel):
    name: str
    ok: bool
    detail: str


class FixtureReport(FrozenModel):
    """Whether a task behaves the way its definition says it should."""

    task_id: str
    checks: tuple[FixtureCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)


def grade(
    result: SandboxResult,
    evaluation: Evaluation,
    *,
    tampered: tuple[str, ...] = (),
    ignored: tuple[str, ...] = (),
) -> Grade:
    """Grade a hidden-test run.

    ``trustworthy`` is False when the run did not produce a usable report:
    a timeout, a memory limit, an infrastructure error, a report that
    contradicts pytest's exit status, or an expected test that is missing.
    Such a run is never ``resolved``, and it is not evidence that the patch
    is wrong. A candidate that tampered with tests or runner configuration is
    never ``resolved`` either, whatever its tests report.
    """
    outcomes = result.outcomes()
    expected = (*evaluation.fail_to_pass, *evaluation.pass_to_pass)
    missing = tuple(node_id for node_id in expected if node_id not in outcomes)
    ran = result.report_is_consistent
    still_failing = tuple(
        node_id
        for node_id in evaluation.fail_to_pass
        if outcomes.get(node_id) not in (None, CaseOutcome.PASSED)
    )
    regressions = tuple(
        node_id
        for node_id in evaluation.pass_to_pass
        if outcomes.get(node_id) not in (None, CaseOutcome.PASSED)
    )
    trustworthy = ran and not missing
    return Grade(
        resolved=trustworthy and not still_failing and not regressions and not tampered,
        trustworthy=trustworthy,
        still_failing=still_failing,
        regressions=regressions,
        missing=missing,
        report_issues=result.report_issues,
        tampered=tampered,
        ignored=ignored,
        status=result.status,
    )


def grade_candidate(
    loaded: LoadedTask, candidate: Mapping[str, bytes], sandbox: DockerSandbox
) -> tuple[Grade, SandboxResult]:
    """Run the hidden tests against ``candidate`` in a fresh sandbox and grade it."""
    task = loaded.task
    tree = loaded.evaluation_tree(candidate)
    result = sandbox.run(
        tree.files,
        task.evaluation.command,
        task.budgets,
        source_roots=task.environment.source_roots,
    )
    return grade(result, task.evaluation, tampered=tree.tampered, ignored=tree.ignored), result


def verify_fixture(loaded: LoadedTask, sandbox: DockerSandbox) -> FixtureReport:
    """Check that a task is a valid benchmark item.

    1. The visible tests pass on the starting state.
    2. On the starting state, every fail-to-pass test fails and every
       pass-to-pass test passes.
    3. The reference fix resolves the task.
    """
    task = loaded.task
    checks: list[FixtureCheck] = []
    starting = loaded.agent_files()

    for command in task.commands:
        visible = sandbox.run(
            starting, command, task.budgets, source_roots=task.environment.source_roots
        )
        checks.append(
            FixtureCheck(
                name=f"visible command '{command.name}' passes on the starting state",
                ok=visible.status is SandboxStatus.PASSED,
                detail=f"status={visible.status.value} counts={visible.counts}",
            )
        )

    buggy_grade, buggy = grade_candidate(loaded, starting, sandbox)
    outcomes = buggy.outcomes()
    exposing = [
        node_id
        for node_id in task.evaluation.fail_to_pass
        if outcomes.get(node_id) is CaseOutcome.FAILED
    ]
    checks.append(
        FixtureCheck(
            name="every fail-to-pass test fails on the starting state",
            ok=buggy.status is SandboxStatus.FAILED
            and len(exposing) == len(task.evaluation.fail_to_pass),
            detail=f"status={buggy.status.value} failing={exposing}",
        )
    )
    checks.append(
        FixtureCheck(
            name="every pass-to-pass test passes on the starting state",
            ok=buggy_grade.trustworthy and not buggy_grade.regressions,
            detail=f"regressions={buggy_grade.regressions} missing={buggy_grade.missing}",
        )
    )
    checks.append(
        FixtureCheck(
            name="the starting state is not graded as resolved",
            ok=not buggy_grade.resolved,
            detail=f"resolved={buggy_grade.resolved}",
        )
    )

    fixed_grade, fixed = grade_candidate(loaded, loaded.reference_solution_files(), sandbox)
    checks.append(
        FixtureCheck(
            name="the reference fix is graded as resolved",
            ok=fixed_grade.resolved and fixed.status is SandboxStatus.PASSED,
            detail=(
                f"status={fixed.status.value} still_failing={fixed_grade.still_failing} "
                f"regressions={fixed_grade.regressions} missing={fixed_grade.missing}"
            ),
        )
    )
    return FixtureReport(task_id=task.task_id, checks=tuple(checks))
