"""What a V1 patch may and may not do. Checked in code, before anything runs.

These rules stop a patch from changing how it will be judged. They use the
same definition of a protected path as the evaluator, which applies the rule
again independently when it grades.
"""

from collections.abc import Mapping

from pydantic import Field

from patchpilot.agent.proposal import PatchProposal
from patchpilot.patch.apply import changed_line_count, unified_diff
from patchpilot.sandbox.models import FrozenModel
from patchpilot.tasks.integrity import is_protected, is_runner_config, is_test_path


class PolicyLimits(FrozenModel):
    max_files_changed: int = Field(default=5, ge=1, le=50)
    max_changed_lines: int = Field(default=300, ge=1, le=5000)


def check_policy(
    original: Mapping[str, bytes],
    proposal: PatchProposal,
    patched: Mapping[str, bytes],
    limits: PolicyLimits | None = None,
) -> tuple[str, ...]:
    """Return every rule the proposal breaks. An empty result means it may run."""
    limits = limits or PolicyLimits()
    violations: list[str] = []

    for edit in proposal.edits:
        if is_protected(edit.path):
            violations.append(
                f"{edit.path}: existing tests, test configuration and CI files may not be edited"
            )

    for test in proposal.regression_tests:
        name = test.path.rsplit("/", 1)[-1]
        if is_runner_config(test.path):
            violations.append(f"{test.path}: new runner configuration files are not allowed")
        elif not (is_test_path(test.path) and name.startswith("test_") and name.endswith(".py")):
            violations.append(f"{test.path}: a new file must be a test file named test_*.py")
        if test.path in original:
            violations.append(f"{test.path}: a regression test must be a new file")

    removed = sorted(set(original) - set(patched))
    if removed:
        violations.append(f"files may not be deleted: {removed}")

    changed = sorted(path for path in patched if patched[path] != original.get(path))
    if len(changed) > limits.max_files_changed:
        violations.append(
            f"{len(changed)} files changed, above the limit of {limits.max_files_changed}"
        )
    lines = changed_line_count(unified_diff(original, patched))
    if lines > limits.max_changed_lines:
        violations.append(f"{lines} lines changed, above the limit of {limits.max_changed_lines}")
    return tuple(violations)
