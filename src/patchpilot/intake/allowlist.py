"""The list of repositories PatchPilot is allowed to touch.

A repository is approved only when a maintainer has written an entry for it.
The check runs in code before any request leaves the machine, and an empty
or missing list approves nothing.
"""

import tomllib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, field_validator, model_validator

from patchpilot.intake.urls import IssueRef, IssueUrlError, OwnerName, RepositoryName
from patchpilot.intake.urls import check_repository_name as _check_repository_name
from patchpilot.sandbox.models import FrozenModel, PytestCommand, SandboxLimits
from patchpilot.tasks.schema import Environment, GitCommitSha

_MAX_ALLOWLIST_BYTES = 262_144


class AllowlistError(ValueError):
    """The allowlist file is missing, unreadable or invalid."""


class RepositoryNotApprovedError(PermissionError):
    """The repository is not on the allowlist."""


class ApprovedRepository(FrozenModel):
    """One approved public GitHub repository and how it may be run.

    ``repository_id`` is GitHub's numeric ID. A name can be transferred or
    re-registered by someone else; the ID cannot, so every API response is
    checked against it.
    """

    owner: OwnerName
    name: RepositoryName
    repository_id: int = Field(ge=1)
    commit_sha: GitCommitSha | None = None
    max_size_kb: int = Field(default=51_200, ge=1, le=1_048_576)
    environment: Environment = Environment()
    commands: tuple[PytestCommand, ...] = ()
    budgets: SandboxLimits = SandboxLimits()

    @field_validator("owner", "name", mode="before")
    @classmethod
    def _lower_case(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @field_validator("name")
    @classmethod
    def _plausible_name(cls, value: str) -> str:
        try:
            return _check_repository_name(value)
        except IssueUrlError as exc:
            raise ValueError(str(exc)) from exc

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        """The repository's web URL, built only from validated names."""
        return f"https://github.com/{self.owner}/{self.name}"


class Allowlist(FrozenModel):
    """Every repository PatchPilot may fetch and analyze."""

    schema_version: Literal[1] = 1
    repositories: tuple[ApprovedRepository, ...] = ()

    @model_validator(mode="after")
    def _entries_are_unique(self) -> Self:
        slugs = [repository.slug for repository in self.repositories]
        if len(set(slugs)) != len(slugs):
            raise ValueError("a repository is listed more than once")
        ids = [repository.repository_id for repository in self.repositories]
        if len(set(ids)) != len(ids):
            raise ValueError("a repository ID is listed more than once")
        return self

    def find(self, owner: str, name: str) -> ApprovedRepository | None:
        wanted = f"{owner.lower()}/{name.lower()}"
        for repository in self.repositories:
            if repository.slug == wanted:
                return repository
        return None

    def require(self, issue: IssueRef) -> ApprovedRepository:
        """Return the entry for the issue's repository or refuse."""
        repository = self.find(issue.owner, issue.repository)
        if repository is None:
            raise RepositoryNotApprovedError(f"repository {issue.slug} is not on the allowlist")
        return repository


def load_allowlist(path: Path | None) -> Allowlist:
    """Load the allowlist from a TOML file.

    ``None`` means no file was configured and yields an empty list, which
    approves nothing. A configured path that cannot be read is an error, so a
    typo never silently turns into "nothing approved" or "everything fine".
    """
    if path is None:
        return Allowlist()
    try:
        if path.is_symlink() or not path.is_file():
            raise AllowlistError(f"{path} is not a regular file")
        if path.stat().st_size > _MAX_ALLOWLIST_BYTES:
            raise AllowlistError(f"{path} is too large")
        data = tomllib.loads(path.read_bytes().decode("utf-8"))
    except OSError as exc:
        raise AllowlistError(f"cannot read {path}: {exc}") from exc
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise AllowlistError(f"{path} is not valid TOML: {exc}") from exc
    try:
        return Allowlist.model_validate(data)
    except ValidationError as exc:
        raise AllowlistError(f"{path} is not a valid allowlist: {exc}") from exc
