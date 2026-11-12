"""Check a patched tree in the sandbox and record the evidence.

This is the agent's own evidence, summed up as ``candidate_verified``. It is
weaker than independent grading: the regression test was written by the same
model as the fix, and the results come from a process that runs the code
under test (see docs/grading-integrity.md). Only the evaluator, with hidden
tests, decides ``resolved``.
"""

from collections.abc import Mapping, Sequence
from typing import Protocol

from patchpilot.agent.proposal import PatchProposal
from patchpilot.sandbox.models import (
    Counts,
    FrozenModel,
    PytestCommand,
    SandboxLimits,
    SandboxResult,
    SandboxStatus,
)

_LIMIT_STATUSES = (SandboxStatus.TIMEOUT, SandboxStatus.OUT_OF_MEMORY)


class SandboxRunner(Protocol):
    """Anything that can run a pytest command against a tree. In practice, the sandbox."""

    def run(
        self,
        files: Mapping[str, bytes],
        command: PytestCommand,
        limits: SandboxLimits | None = None,
        *,
        source_roots: Sequence[str] = (),
    ) -> SandboxResult: ...


class VerificationStep(FrozenModel):
    name: str
    expected: str
    status: SandboxStatus
    counts: Counts | None
    ok: bool
    duration_seconds: float


class Verification(FrozenModel):
    steps: tuple[VerificationStep, ...]
    verified: bool
    infrastructure_failure: bool
    reason: str
    image_id: str | None = None


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
    for name, expected, files, command, wanted in plan:
        result: SandboxResult = sandbox.run(files, command, limits, source_roots=source_roots)
        if result.metadata is not None:
            image_id = result.metadata.image_id
        ok = result.status is wanted and result.report_is_consistent
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
        if result.is_infrastructure_error:
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
        )
    return Verification(
        steps=tuple(steps),
        verified=True,
        infrastructure_failure=False,
        reason="the regression tests fail before the patch and all tests pass after it",
        image_id=image_id,
    )
