import pytest
from pydantic import ValidationError

from patchpilot.sandbox.models import PytestCommand, SandboxLimits


@pytest.mark.parametrize(
    "target",
    [
        "tests",
        "tests/test_cache.py",
        "tests/test_cache.py::test_eviction",
        "tests/test_cache.py::test_case[1-2]",
    ],
)
def test_plain_relative_targets_are_accepted(target: str) -> None:
    assert PytestCommand(name="tests", targets=(target,)).targets == (target,)


@pytest.mark.parametrize(
    "target",
    [
        "-p",  # a flag
        "--collect-only",
        "/etc/passwd",  # absolute
        "../outside",  # parent traversal
        "tests/../../outside",
        "tests/./x.py",
        "tests//x.py",
        "tests; rm -rf /",  # shell metacharacters
        "tests $(id)",
        "",
    ],
)
def test_flags_absolute_paths_and_traversal_are_rejected(target: str) -> None:
    with pytest.raises(ValidationError):
        PytestCommand(name="tests", targets=(target,))


def test_a_command_needs_at_least_one_target() -> None:
    with pytest.raises(ValidationError):
        PytestCommand(name="tests", targets=())


def test_default_limits_are_valid_and_bounded() -> None:
    limits = SandboxLimits()

    assert limits.timeout_seconds == 60
    assert limits.memory_mb == 512
    assert limits.pids == 128


def test_memory_must_cover_the_memory_backed_scratch_space() -> None:
    with pytest.raises(ValidationError, match="memory-backed"):
        SandboxLimits(memory_mb=128, workspace_mb=64, tmp_mb=64)


@pytest.mark.parametrize(
    "field,value",
    [("timeout_seconds", 0), ("timeout_seconds", 601), ("pids", 4), ("cpus", 16.0)],
)
def test_limits_outside_the_allowed_range_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        SandboxLimits.model_validate({field: value})


def test_unknown_limit_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        SandboxLimits.model_validate({"network": True})
