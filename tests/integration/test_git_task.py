"""A task whose repository is fetched from git, run end to end.

The repository is a local git repository built from the cachekit fixture, so
no network is involved. The fetched snapshot goes through the same loader
checks, sandbox and grader as a fixture task.
"""

import shutil
from pathlib import Path

import pytest

from patchpilot.repo.fetch import GitFetcher, RemoteSource
from patchpilot.repo.snapshot import RepositorySnapshot, build_snapshot
from patchpilot.repo.tools import ReadFileResult, RepositoryTools
from patchpilot.sandbox import DockerSandbox, SandboxStatus
from patchpilot.tasks import (
    LoadedTask,
    TaskError,
    attach_snapshot,
    grade_candidate,
    load_task,
    verify_fixture,
)
from tests.integration.gitrepo import LocalRepo

TASKS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "tasks"
FIXTURE = TASKS_ROOT / "inj-001-cachekit"
URL = "https://github.com/example/cachekit"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git_task(tmp_path: Path) -> tuple[LoadedTask, LocalRepo, str]:
    """Turn the cachekit fixture into a task pinned to a commit of a local repository."""
    repo = LocalRepo(tmp_path / "remote")
    for source in sorted((FIXTURE / "repo").rglob("*")):
        if source.is_file() and "__pycache__" not in source.parts:
            repo.write({source.relative_to(FIXTURE / "repo").as_posix(): source.read_bytes()})
    sha = repo.commit(message="starting state")

    task_dir = tmp_path / "tasks" / "inj-001-cachekit"
    shutil.copytree(FIXTURE / "internal", task_dir / "internal")
    definition = (FIXTURE / "task.toml").read_text()
    start = definition.index("\n[repository]\n") + 1
    end = definition.index("\n[issue]\n") + 1
    pinned = f'[repository]\nkind = "git"\nurl = "{URL}"\ncommit_sha = "{sha}"\n\n'
    (task_dir / "task.toml").write_text(definition[:start] + pinned + definition[end:])
    return load_task(task_dir), repo, sha


def _snapshot(repo: LocalRepo, sha: str, url: str = URL) -> RepositorySnapshot:
    with GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), sha) as store:
        return build_snapshot(store, url)


def test_a_git_task_has_no_files_until_its_commit_is_fetched(tmp_path: Path) -> None:
    loaded, _, _ = _git_task(tmp_path)

    assert loaded.task.repository.kind == "git"
    with pytest.raises(TaskError, match="must be fetched first"):
        loaded.agent_files()


def test_a_snapshot_of_the_pinned_commit_is_accepted(tmp_path: Path) -> None:
    loaded, repo, sha = _git_task(tmp_path)

    attached = attach_snapshot(loaded, _snapshot(repo, sha))

    assert sorted(attached.agent_files()) == sorted(
        path.relative_to(FIXTURE / "repo").as_posix()
        for path in (FIXTURE / "repo").rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    assert not any("hidden" in path for path in attached.agent_files())


def test_a_snapshot_of_another_commit_or_repository_is_refused(tmp_path: Path) -> None:
    loaded, repo, sha = _git_task(tmp_path)
    newer = repo.commit({"src/cachekit/extra.py": "EXTRA = 1\n"})

    with pytest.raises(TaskError, match="but the task pins"):
        attach_snapshot(loaded, _snapshot(repo, newer))
    with pytest.raises(TaskError, match="different repository"):
        attach_snapshot(loaded, _snapshot(repo, sha, url="https://github.com/example/other"))


def test_a_fixture_task_does_not_take_a_snapshot(tmp_path: Path) -> None:
    _, repo, sha = _git_task(tmp_path)

    with pytest.raises(TaskError, match="only tasks with a git repository"):
        attach_snapshot(load_task(FIXTURE), _snapshot(repo, sha))


def test_the_tools_read_the_fetched_snapshot(tmp_path: Path) -> None:
    _, repo, sha = _git_task(tmp_path)
    tools = RepositoryTools(_snapshot(repo, sha).files)

    result = tools.call("read_file", {"path": "src/cachekit/__init__.py"})

    assert isinstance(result, ReadFileResult)
    assert "LRUCache" in result.content.reveal()


@pytest.mark.sandbox
def test_a_fetched_snapshot_runs_and_is_graded_in_the_sandbox(
    sandbox: DockerSandbox, tmp_path: Path
) -> None:
    loaded, repo, sha = _git_task(tmp_path)
    attached = attach_snapshot(loaded, _snapshot(repo, sha))

    report = verify_fixture(attached, sandbox)
    outcome, result = grade_candidate(attached, attached.reference_solution_files(), sandbox)

    assert report.ok, [check.detail for check in report.checks if not check.ok]
    assert result.status is SandboxStatus.PASSED
    assert outcome.resolved
    assert result.metadata is not None
    assert len(result.metadata.source_tree_sha256) == 64
