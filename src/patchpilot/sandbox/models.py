"""Data models for sandboxed test execution."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# A pytest target is a relative path, optionally followed by ``::name`` parts.
# It must start with a letter, digit or underscore, so it can never be read as
# a command-line flag or an absolute path.
_TARGET_PATTERN = r"^[A-Za-z0-9_][A-Za-z0-9_./-]*(::[A-Za-z0-9_]+(\[[^\]\s]{0,100}\])?)*$"

PytestTarget = Annotated[str, StringConstraints(pattern=_TARGET_PATTERN, max_length=300)]
CommandName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]

# Memory kept free for the interpreter on top of the two scratch filesystems,
# which are charged to the same memory limit.
_MEMORY_HEADROOM_MB = 64


class FrozenModel(BaseModel):
    """Immutable model that rejects unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class SandboxLimits(FrozenModel):
    """Resource and time budgets for one sandboxed test run."""

    timeout_seconds: int = Field(default=60, ge=1, le=600)
    memory_mb: int = Field(default=512, ge=128, le=2048)
    cpus: float = Field(default=1.0, ge=0.25, le=4.0)
    pids: int = Field(default=128, ge=16, le=512)
    workspace_mb: int = Field(default=64, ge=1, le=512)
    tmp_mb: int = Field(default=64, ge=1, le=512)
    max_output_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)

    @model_validator(mode="after")
    def _scratch_space_fits_in_memory(self) -> Self:
        needed = self.workspace_mb + self.tmp_mb + _MEMORY_HEADROOM_MB
        if needed > self.memory_mb:
            raise ValueError(
                f"memory_mb={self.memory_mb} is too small: the workspace and tmp "
                f"filesystems are memory-backed and need {needed} MB including headroom"
            )
        return self


class PytestCommand(FrozenModel):
    """An approved pytest invocation.

    Only targets are configurable. The interpreter, pytest flags and output
    locations are fixed by the runner, so a command can never carry a shell
    string or an arbitrary flag.
    """

    name: CommandName
    targets: tuple[PytestTarget, ...] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _targets_stay_inside_the_workspace(self) -> Self:
        for target in self.targets:
            path = target.split("::", 1)[0]
            if any(part in ("..", ".", "") for part in path.split("/")):
                raise ValueError(f"target {target!r} is not a plain relative path")
        return self


class SandboxStatus(StrEnum):
    """Outcome of a sandboxed run.

    ``PASSED`` to ``RUNNER_ERROR`` describe what pytest reported. ``TIMEOUT``
    and ``OUT_OF_MEMORY`` are limit violations by the code under test.
    ``INFRASTRUCTURE_ERROR`` means the sandbox itself failed and says nothing
    about the code under test.
    """

    PASSED = "passed"
    FAILED = "failed"
    COLLECTION_ERROR = "collection_error"
    NO_TESTS_COLLECTED = "no_tests_collected"
    RUNNER_ERROR = "runner_error"
    TIMEOUT = "timeout"
    OUT_OF_MEMORY = "out_of_memory"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class CaseOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class CaseResult(FrozenModel):
    """One test case read from the JUnit report."""

    node_id: str
    classname: str
    name: str
    outcome: CaseOutcome
    duration_seconds: float
    message: str = ""


class Counts(FrozenModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)


class RunMetadata(FrozenModel):
    """What is needed to reproduce or audit a run."""

    runner_version: str
    image_ref: str
    image_id: str
    engine_version: str
    engine_platform: str
    engine_os: str
    cgroup_version: str
    host_platform: str
    source_tree_sha256: str
    source_roots: tuple[str, ...]
    limits: SandboxLimits


class SandboxResult(FrozenModel):
    """Structured result of one sandboxed pytest command."""

    status: SandboxStatus
    command: PytestCommand
    container_argv: tuple[str, ...]
    exit_code: int | None
    counts: Counts | None
    cases: tuple[CaseResult, ...] = ()
    junit_xml: str | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    started_at: datetime
    duration_seconds: float
    oom_kill_count: int | None = None
    pids_limit_hit: bool | None = None
    error: str | None = None
    report_issues: tuple[str, ...] = ()
    cleanup_failed: bool = False
    metadata: RunMetadata | None = None

    @property
    def is_infrastructure_error(self) -> bool:
        return self.status is SandboxStatus.INFRASTRUCTURE_ERROR

    @property
    def report_is_consistent(self) -> bool:
        """True when pytest finished and its report agrees with its exit status.

        This catches a missing, malformed, duplicated or contradictory report.
        It cannot catch code under test that forges both the report and the
        exit status from inside the test process.
        """
        return (
            self.status in (SandboxStatus.PASSED, SandboxStatus.FAILED) and not self.report_issues
        )

    def outcomes(self) -> dict[str, CaseOutcome]:
        """Map each reported test node ID to its outcome."""
        return {case.node_id: case.outcome for case in self.cases}
