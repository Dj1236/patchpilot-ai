"""Validated format for evaluation tasks.

A task has two audiences. ``Task`` is the full, internal definition used by
the evaluator. ``AgentTask`` is the part an agent may see: it has no field
for hidden tests, reference fixes or expected outcomes, so that data cannot
leak by accident. Always hand an agent ``Task.agent_view()``, never ``Task``.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from patchpilot.sandbox.models import FrozenModel, PytestCommand, PytestTarget, SandboxLimits

INTERNAL_DIR = "internal"

TaskId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")]
RelativeDir = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitCommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
GitHubUrl = Annotated[
    str,
    StringConstraints(pattern=r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"),
]
Text = Annotated[str, StringConstraints(min_length=1, max_length=8000, strip_whitespace=True)]
Title = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]


def _is_inside(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory + "/")


class FixtureRepository(FrozenModel):
    """A repository stored inside the task directory, pinned by content digest."""

    kind: Literal["fixture"]
    path: RelativeDir
    tree_sha256: Sha256


class GitRepository(FrozenModel):
    """An approved GitHub repository pinned to one full commit SHA."""

    kind: Literal["git"]
    url: GitHubUrl
    commit_sha: GitCommitSha


Repository = Annotated[FixtureRepository | GitRepository, Field(discriminator="kind")]


class Issue(FrozenModel):
    """Structured bug description, written the way a user would report it."""

    title: Title
    body: Text
    expected_behavior: Text
    actual_behavior: Text
    reproduction: Text | None = None


class Environment(FrozenModel):
    """How the repository is run inside the sandbox."""

    source_roots: tuple[RelativeDir, ...] = ()


class Evaluation(FrozenModel):
    """Internal evaluation data. Never shown to an agent.

    Expected behavior of a valid task:

    - On the starting state, every ``fail_to_pass`` test fails and every
      ``pass_to_pass`` test passes.
    - With the reference fix applied, all of them pass.

    A candidate patch resolves the task when all ``fail_to_pass`` tests pass
    and no ``pass_to_pass`` test breaks.
    """

    hidden_tests_dir: RelativeDir
    reference_fix_dir: RelativeDir
    command: PytestCommand
    fail_to_pass: tuple[PytestTarget, ...] = Field(min_length=1)
    pass_to_pass: tuple[PytestTarget, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> Self:
        for name in ("hidden_tests_dir", "reference_fix_dir"):
            value: str = getattr(self, name)
            if not _is_inside(value, INTERNAL_DIR) or value == INTERNAL_DIR:
                raise ValueError(f"{name} must be a directory inside '{INTERNAL_DIR}/'")
        if self.hidden_tests_dir == self.reference_fix_dir:
            raise ValueError("hidden tests and the reference fix need separate directories")
        for name in ("fail_to_pass", "pass_to_pass"):
            ids: tuple[str, ...] = getattr(self, name)
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} contains duplicates")
            if any("::" not in node_id for node_id in ids):
                raise ValueError(f"{name} must list individual tests as 'path::name'")
        overlap = set(self.fail_to_pass) & set(self.pass_to_pass)
        if overlap:
            raise ValueError(
                f"tests listed as both fail-to-pass and pass-to-pass: {sorted(overlap)}"
            )
        return self


class AgentTask(FrozenModel):
    """Everything about a task that an agent is allowed to see."""

    task_id: TaskId
    repository: Repository
    issue: Issue
    environment: Environment
    commands: tuple[PytestCommand, ...]
    budgets: SandboxLimits


class Task(FrozenModel):
    """Full, internal definition of one evaluation task."""

    schema_version: Literal[1]
    task_id: TaskId
    kind: Literal["injected", "real"]
    repository: Repository
    issue: Issue
    environment: Environment = Environment()
    commands: tuple[PytestCommand, ...] = Field(min_length=1, max_length=10)
    budgets: SandboxLimits = SandboxLimits()
    evaluation: Evaluation

    @model_validator(mode="after")
    def _check(self) -> Self:
        names = [command.name for command in self.commands]
        if len(set(names)) != len(names):
            raise ValueError("command names must be unique")
        if isinstance(self.repository, FixtureRepository) and _is_inside(
            self.repository.path, INTERNAL_DIR
        ):
            raise ValueError(f"the repository must not live inside '{INTERNAL_DIR}/'")
        return self

    def agent_view(self) -> AgentTask:
        """Return the agent-visible part of this task."""
        return AgentTask(
            task_id=self.task_id,
            repository=self.repository,
            issue=self.issue,
            environment=self.environment,
            commands=self.commands,
            budgets=self.budgets,
        )
