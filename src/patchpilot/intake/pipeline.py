"""From an issue URL to a bug report and a pinned repository snapshot.

The order of the steps is the security property: the URL is validated and
the repository is checked against the allowlist before anything is sent over
the network, and the commit is pinned before anything is fetched.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from patchpilot.intake.allowlist import Allowlist, ApprovedRepository
from patchpilot.intake.github import BugReport, GitHubClient
from patchpilot.intake.urls import parse_issue_url
from patchpilot.repo.snapshot import RepositorySnapshot, fetch_snapshot

Snapshotter = Callable[[ApprovedRepository, str, str | None], RepositorySnapshot]


@dataclass(frozen=True)
class IntakeResult:
    repository: ApprovedRepository
    bug_report: BugReport
    snapshot: RepositorySnapshot
    commit_sha: str
    pinned_by: Literal["allowlist", "default_branch"]


def _fetch(
    repository: ApprovedRepository, commit_sha: str, branch: str | None
) -> RepositorySnapshot:
    return fetch_snapshot(repository, commit_sha, fallback_branch=branch)


def run_intake(
    issue_url: str,
    allowlist: Allowlist,
    client: GitHubClient,
    *,
    snapshotter: Snapshotter = _fetch,
) -> IntakeResult:
    """Validate, authorize, read the issue, pin a commit and snapshot it.

    Raises ``IssueUrlError`` or ``RepositoryNotApprovedError`` before any
    request is made, ``GitHubError`` for API problems and ``FetchError`` for
    fetch problems. Nothing in the issue's text influences any step.
    """
    issue = parse_issue_url(issue_url)
    repository = allowlist.require(issue)

    info = client.get_repository(repository)
    bug_report = client.get_issue(repository, issue.number)

    pinned_by: Literal["allowlist", "default_branch"]
    if repository.commit_sha is not None:
        commit_sha = repository.commit_sha
        pinned_by = "allowlist"
    else:
        commit_sha = client.resolve_branch(repository, info.default_branch)
        pinned_by = "default_branch"

    snapshot = snapshotter(repository, commit_sha, info.default_branch)
    if snapshot.commit_sha != commit_sha:
        raise RuntimeError("the snapshot is not of the pinned commit")
    return IntakeResult(
        repository=repository,
        bug_report=bug_report,
        snapshot=snapshot,
        commit_sha=commit_sha,
        pinned_by=pinned_by,
    )
