"""Check a patched tree in the sandbox and record the evidence.

This is the agent's own evidence, summed up as ``candidate_verified``. It is
weaker than independent grading: the regression test was written by the same
model as the fix, and the results come from a process that runs the code
under test (see docs/grading-integrity.md). Only the evaluator, with hidden
tests, decides ``resolved``.
"""

import re
from collections.abc import Mapping, Sequence

from patchpilot.agent.proposal import PatchProposal
from patchpilot.intake.text import UntrustedText
from patchpilot.sandbox.models import (
    CaseOutcome,
    Counts,
    FrozenModel,
    PytestCommand,
    SandboxLimits,
    SandboxResult,
    SandboxRunner,
    SandboxStatus,
)

_LIMIT_STATUSES = (SandboxStatus.TIMEOUT, SandboxStatus.OUT_OF_MEMORY)
_UNSUCCESSFUL = (CaseOutcome.FAILED, CaseOutcome.ERROR)
_MAX_FAILED_CASES = 5
_MAX_NODE_CHARS = 200
_MAX_FAILURE_CHARS = 800
# Object addresses differ on every run. Left in, they would make the feedback
# of a repair run, and so its replay, different each time.
_ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{4,}\b")


class VerificationStep(FrozenModel):
    name: str
    expected: str
    status: SandboxStatus
    counts: Counts | None
    ok: bool
    duration_seconds: float


class FailedCase(FrozenModel):
    """One failing test of the step that did not hold.

    These come only from tests the agent can see: the repository's own tests
    and the regression tests it wrote. Hidden tests are never run here. The
    message is test output, written by the code under test: untrusted.
    """

    node_id: str
    message: UntrustedText


class Verification(FrozenModel):
    steps: tuple[VerificationStep, ...]
    verified: bool
    infrastructure_failure: bool
    reason: str
    image_id: str | None = None
    # A bounded sample, for feedback to the model in a repair run.
    failures: tuple[FailedCase, ...] = ()


def verify_patch(
    original: Mapping[str, bytes],
    patched: Mapping[str, bytes],
    proposal: PatchProposal,
    *,
    commands: Sequence[PytestCommand],
    limits: SandboxLimits,
    source_roots: Sequence[str],
    sandbox: SandboxRunner,
) -> Verification:
    """Run the three checks, stopping at the first one that does not hold.

    1. The new regression tests fail on the original code.
    2. The same tests pass on the patched code.
    3. The existing test commands pass on the patched code.

    All code, including the model-written tests, runs only in the sandbox.
    """
    new_tests = {test.path: test.content.encode("utf-8") for test in proposal.regression_tests}
    regression = PytestCommand(name="regression", targets=tuple(sorted(new_tests)))
    plan: list[tuple[str, str, Mapping[str, bytes], PytestCommand, SandboxStatus]] = [
        (
            "regression tests on the original code",
            "at least one test fails",
            {**original, **new_tests},
            regression,
            SandboxStatus.FAILED,
        ),
        (
            "regression tests on the patched code",
            "all tests pass",
            patched,
            regression,
            SandboxStatus.PASSED,
        ),
    ]
    plan.extend(
        (
            f"existing tests '{command.name}' on the patched code",
            "all tests pass",
            patched,
            command,
            SandboxStatus.PASSED,
        )
        for command in commands
    )

    steps: list[VerificationStep] = []
    image_id: str | None = None
    failing_before: set[str] = set()
    for index, (name, expected, files, command, wanted) in enumerate(plan):
        result: SandboxResult = sandbox.run(files, command, limits, source_roots=source_roots)
        if result.metadata is not None:
            image_id = result.metadata.image_id
        ok = result.status is wanted and result.report_is_consistent
        problem: str | None = None
        outcomes = result.outcomes()
        if ok and index == 0:
            failing_before = {
                node for node, outcome in outcomes.items() if outcome in _UNSUCCESSFUL
            }
            if not failing_before:
                ok, problem = False, f"{name}: no individual test was reported as failing"
        elif ok and index == 1:
            # The very tests that failed before must now pass. A test that is
            # skipped, renamed or missing after the patch proves nothing.
            unproven = sorted(
                node for node in failing_before if outcomes.get(node) is not CaseOutcome.PASSED
            )
            if unproven:
                ok, problem = False, f"{name}: a test that failed before did not pass after"
        steps.append(
            VerificationStep(
                name=name,
                expected=expected,
                status=result.status,
                counts=result.counts,
                ok=ok,
                duration_seconds=result.duration_seconds,
            )
        )
        if ok:
            continue
        if problem is not None:
            reason = problem
        elif result.is_infrastructure_error:
            reason = f"the sandbox failed during: {name}"
        elif result.status in _LIMIT_STATUSES:
            reason = f"{name}: stopped by a limit ({result.status.value})"
        elif not result.report_is_consistent and result.status is wanted:
            reason = f"{name}: the test report was inconsistent"
        else:
            reason = f"{name}: expected that {expected}, but the result was {result.status.value}"
        return Verification(
            steps=tuple(steps),
            verified=False,
            infrastructure_failure=result.is_infrastructure_error,
            reason=reason,
            image_id=image_id,
            failures=tuple(
                FailedCase(
                    node_id=case.node_id[:_MAX_NODE_CHARS],
                    message=UntrustedText(
                        untrusted_value=_ADDRESS.sub("0x...", case.message)[:_MAX_FAILURE_CHARS]
                    ),
                )
                for case in result.cases
                if case.outcome in _UNSUCCESSFUL
            )[:_MAX_FAILED_CASES]
            if wanted is SandboxStatus.PASSED
            else (),
        )
    return Verification(
        steps=tuple(steps),
        verified=True,
        infrastructure_failure=False,
        reason="the regression tests fail before the patch and all tests pass after it",
        image_id=image_id,
    )
