"""Fetch and snapshot tests against real git and local repositories.

No network and no Docker are needed. Remote repositories are built on local
disk, including hostile ones.
"""

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from patchpilot.intake.allowlist import ApprovedRepository
from patchpilot.repo.fetch import (
    FetchError,
    FetchLimits,
    FetchStatus,
    GitFetcher,
    RemoteSource,
    directory_size,
)
from patchpilot.repo.snapshot import build_snapshot
from patchpilot.sandbox.process import ProcessResult, run_bounded
from patchpilot.sandbox.tree import tree_digest
from tests.integration.gitrepo import LocalRepo

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

FILES: dict[str, bytes | str] = {
    "src/pkg/__init__.py": "VALUE = 1\n",
    "tests/test_pkg.py": "def test_ok():\n    assert True\n",
    "README.md": "# demo\n",
}


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point temporary files at a private directory so leftovers can be seen."""
    directory = tmp_path / "scratch"
    directory.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(directory))
    yield directory
    assert list(directory.iterdir()) == [], "temporary fetch files were left behind"


@pytest.fixture
def repo(tmp_path: Path) -> LocalRepo:
    return LocalRepo(tmp_path / "remote")


def _status(fetcher: GitFetcher, source: RemoteSource, sha: str, **kwargs: Any) -> FetchStatus:
    with pytest.raises(FetchError) as caught, fetcher.fetch(source, sha, **kwargs):
        pass
    return caught.value.status


def _failing_direct_fetch(calls: list[list[str]]) -> Any:
    """Return a runner that refuses the fetch-by-SHA step and runs everything else."""

    def runner(argv: list[str], **kwargs: Any) -> ProcessResult:
        calls.append(list(argv))
        if "fetch" in argv and "--depth=1" in argv:
            return ProcessResult(
                exit_code=128,
                stdout=b"",
                stderr=b"fatal: remote error: upload-pack: not our ref\n",
                stdout_truncated=False,
                stderr_truncated=False,
                timed_out=False,
                duration_seconds=0.0,
            )
        return run_bounded(argv, **kwargs)

    return runner


# -- pinned commits ---------------------------------------------------------


def test_the_pinned_commit_is_fetched_and_read_without_a_checkout(
    repo: LocalRepo, scratch: Path
) -> None:
    sha = repo.commit(FILES)
    source = RemoteSource.local_for_tests(repo.path)

    with GitFetcher().fetch(source, sha) as store:
        snapshot = build_snapshot(store, "https://github.com/example/demo")
        assert not (store.path / "hooks").exists()
        assert not any(store.path.parent.glob("**/README.md"))
        assert not any(store.path.parent.glob("**/*.py"))

    assert snapshot.commit_sha == sha
    assert dict(snapshot.files) == {path: str(text).encode() for path, text in FILES.items()}
    assert snapshot.tree_sha256 == tree_digest(snapshot.files)
    assert snapshot.skipped == ()


def test_an_older_pinned_commit_is_used_not_the_branch_tip(repo: LocalRepo, scratch: Path) -> None:
    pinned = repo.commit({"a.py": "VERSION = 1\n"})
    repo.commit({"a.py": "VERSION = 2\n", "new.py": "NEW = 1\n"})

    with GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), pinned) as store:
        snapshot = build_snapshot(store, "https://github.com/example/demo")

    assert snapshot.commit_sha == pinned
    assert dict(snapshot.files) == {"a.py": b"VERSION = 1\n"}


def test_an_unknown_commit_is_unavailable(repo: LocalRepo, scratch: Path) -> None:
    repo.commit(FILES)
    source = RemoteSource.local_for_tests(repo.path)

    assert _status(GitFetcher(), source, "0" * 40) is FetchStatus.COMMIT_UNAVAILABLE
    assert _status(GitFetcher(), source, "0" * 40, fallback_branch="main") is (
        FetchStatus.COMMIT_UNAVAILABLE
    )


@pytest.mark.parametrize("revision", ["HEAD:README.md", "HEAD^{tree}", "v1"])
def test_an_object_that_is_not_a_commit_is_refused(
    repo: LocalRepo, scratch: Path, revision: str
) -> None:
    repo.commit(FILES)
    repo.git("tag", "--annotate", "v1", "-m", "release")
    object_id = repo.object_id(revision)
    source = RemoteSource.local_for_tests(repo.path)

    with pytest.raises(FetchError) as caught, GitFetcher().fetch(source, object_id):
        pass

    assert caught.value.status is FetchStatus.COMMIT_UNAVAILABLE


# -- fallback when the server refuses a fetch by SHA ---------------------------


def test_fallback_finds_the_pinned_commit_in_the_branch_history(
    repo: LocalRepo, scratch: Path
) -> None:
    pinned = repo.commit({"a.py": "VERSION = 1\n"})
    repo.commit({"a.py": "VERSION = 2\n"})
    tip = repo.commit({"a.py": "VERSION = 3\n"})
    calls: list[list[str]] = []
    fetcher = GitFetcher(process_runner=_failing_direct_fetch(calls))

    source = RemoteSource.local_for_tests(repo.path)
    with fetcher.fetch(source, pinned, fallback_branch="main") as store:
        snapshot = build_snapshot(store, "https://github.com/example/demo")

    assert snapshot.commit_sha == pinned != tip
    assert dict(snapshot.files) == {"a.py": b"VERSION = 1\n"}
    assert any("+refs/heads/main:refs/patchpilot/fallback" in call for call in calls)


def test_fallback_never_substitutes_the_branch_tip(repo: LocalRepo, scratch: Path) -> None:
    repo.commit({"a.py": "VERSION = 1\n"})
    repo.git("checkout", "--quiet", "-b", "side")
    pinned = repo.commit({"a.py": "SIDE = 1\n"})
    repo.git("checkout", "--quiet", "main")
    repo.commit({"a.py": "VERSION = 2\n"})
    fetcher = GitFetcher(process_runner=_failing_direct_fetch([]))
    source = RemoteSource.local_for_tests(repo.path)

    # The pinned commit exists in the remote, but not on the fallback branch.
    assert _status(fetcher, source, pinned, fallback_branch="main") is (
        FetchStatus.COMMIT_UNAVAILABLE
    )


def test_fallback_depth_is_bounded(repo: LocalRepo, scratch: Path) -> None:
    pinned = repo.commit({"a.py": "VERSION = 1\n"})
    for version in range(2, 6):
        repo.commit({"a.py": f"VERSION = {version}\n"})
    fetcher = GitFetcher(
        limits=FetchLimits(fallback_depth=2), process_runner=_failing_direct_fetch([])
    )
    source = RemoteSource.local_for_tests(repo.path)

    assert _status(fetcher, source, pinned, fallback_branch="main") is (
        FetchStatus.COMMIT_UNAVAILABLE
    )


def test_without_a_fallback_branch_a_refused_fetch_is_unavailable(
    repo: LocalRepo, scratch: Path
) -> None:
    pinned = repo.commit(FILES)
    fetcher = GitFetcher(process_runner=_failing_direct_fetch([]))

    status = _status(fetcher, RemoteSource.local_for_tests(repo.path), pinned)

    assert status is FetchStatus.COMMIT_UNAVAILABLE


# -- limits, timeouts and cleanup ------------------------------------------------


@pytest.mark.parametrize("file_limit,watchdog", [(True, True), (True, False), (False, True)])
def test_a_fetch_larger_than_the_storage_limit_is_stopped(
    repo: LocalRepo, scratch: Path, file_limit: bool, watchdog: bool
) -> None:
    sha = repo.commit({"blob.bin": os.urandom(4 * 1_048_576)})
    fetcher = GitFetcher(
        limits=FetchLimits(max_store_mb=1),
        enforce_file_limit=file_limit,
        enforce_watchdog=watchdog,
    )

    status = _status(fetcher, RemoteSource.local_for_tests(repo.path), sha)

    assert status is FetchStatus.FETCH_TOO_LARGE


def test_the_per_file_limit_keeps_the_stored_pack_below_the_cap(
    repo: LocalRepo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = repo.commit({"blob.bin": os.urandom(4 * 1_048_576)})
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    sizes: list[int] = []

    def measuring(argv: list[str], **kwargs: Any) -> ProcessResult:
        result = run_bounded(argv, **kwargs)
        if "fetch" in argv:
            sizes.append(directory_size(scratch))
        return result

    fetcher = GitFetcher(
        limits=FetchLimits(max_store_mb=1), enforce_watchdog=False, process_runner=measuring
    )

    assert _status(fetcher, RemoteSource.local_for_tests(repo.path), sha) is (
        FetchStatus.FETCH_TOO_LARGE
    )
    # Bounded while it ran, not only detected afterwards: one megabyte for the
    # pack file plus git's small bookkeeping files.
    assert sizes and max(sizes) < 1_048_576 + 262_144
    assert list(scratch.iterdir()) == []


def test_a_fetch_that_exceeds_its_time_limit_is_reported_and_cleaned_up(
    repo: LocalRepo, scratch: Path
) -> None:
    sha = repo.commit(FILES)

    def stalling(argv: list[str], **kwargs: Any) -> ProcessResult:
        if "fetch" in argv:
            return run_bounded(["sleep", "30"], **{**kwargs, "timeout": 0.3})
        return run_bounded(argv, **kwargs)

    fetcher = GitFetcher(process_runner=stalling)

    assert _status(fetcher, RemoteSource.local_for_tests(repo.path), sha) is (
        FetchStatus.FETCH_TIMEOUT
    )


def test_an_unreachable_remote_is_a_fetch_failure(tmp_path: Path, scratch: Path) -> None:
    missing = RemoteSource.local_for_tests(tmp_path / "no-such-repository")

    assert _status(GitFetcher(), missing, "1" * 40) is FetchStatus.FETCH_FAILED
    assert _status(GitFetcher(), missing, "1" * 40, fallback_branch="main") is (
        FetchStatus.FETCH_FAILED
    )


def test_a_missing_git_binary_is_reported(repo: LocalRepo, scratch: Path) -> None:
    sha = repo.commit(FILES)
    fetcher = GitFetcher(git_bin="/nonexistent/git")

    assert _status(fetcher, RemoteSource.local_for_tests(repo.path), sha) is (
        FetchStatus.GIT_UNAVAILABLE
    )


def test_temporary_files_are_removed_when_the_caller_fails(repo: LocalRepo, scratch: Path) -> None:
    sha = repo.commit(FILES)

    with (
        pytest.raises(RuntimeError, match="caller failed"),
        GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), sha),
    ):
        raise RuntimeError("caller failed")


# -- inputs -------------------------------------------------------------------


@pytest.mark.parametrize(
    "sha",
    [
        "main",
        "HEAD",
        "abc1234",
        "A" * 40,
        "0" * 39,
        "0" * 41,
        "--upload-pack=touch /tmp/x",
        "0" * 40 + "\n",
        "",
    ],
)
def test_only_a_full_lower_case_sha_is_accepted(repo: LocalRepo, sha: str) -> None:
    source = RemoteSource.local_for_tests(repo.path)

    with pytest.raises(ValueError, match="full 40-character SHA"), GitFetcher().fetch(source, sha):
        pass


@pytest.mark.parametrize("branch", ["--upload-pack=x", "a..b", "main; id", "a b", "-x"])
def test_an_unsafe_fallback_branch_is_refused_before_git_runs(repo: LocalRepo, branch: str) -> None:
    source = RemoteSource.local_for_tests(repo.path)

    with (
        pytest.raises(ValueError, match="unsafe"),
        GitFetcher().fetch(source, "0" * 40, fallback_branch=branch),
    ):
        pass


def test_the_production_remote_is_built_only_from_an_allowlist_entry() -> None:
    repository = ApprovedRepository(owner="OctoCat", name="Hello-World", repository_id=1)

    source = RemoteSource.for_repository(repository)

    assert source == RemoteSource("https://github.com/octocat/hello-world.git", "https")


@pytest.mark.parametrize(
    "url,protocol",
    [
        ("https://evil.example/owner/repo.git", "https"),
        ("https://github.com.evil.example/owner/repo.git", "https"),
        ("https://user:pass@github.com/owner/repo.git", "https"),
        ("https://github.com/owner/repo", "https"),
        ("https://github.com/owner/repo.git --upload-pack=x", "https"),
        ("https://github.com/owner/../other/repo.git", "https"),
        ("http://github.com/owner/repo.git", "https"),
        ("ssh://git@github.com/owner/repo.git", "https"),
        ("git@github.com:owner/repo.git", "https"),
        ("ext::sh -c 'touch /tmp/x'", "https"),
        ("ext::sh -c 'touch /tmp/x'", "file"),
        ("file://remote-host/share/repo", "file"),
        ("file:///tmp/../etc", "file"),
        ("file:///tmp/a b", "file"),
        ("/tmp/repo", "file"),
        ("https://github.com/owner/repo.git", "file"),
    ],
)
def test_other_remote_urls_cannot_be_constructed(url: str, protocol: Any) -> None:
    with pytest.raises(ValueError, match="not an accepted form"):
        RemoteSource(url=url, protocol=protocol)


# -- nothing from the machine or the repository runs ------------------------------


def test_git_ignores_hostile_configuration_from_the_environment(
    repo: LocalRepo, scratch: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha = repo.commit(FILES)
    canary = tmp_path / "canary"
    hostile_home = tmp_path / "hostile-home"
    hostile_home.mkdir()
    touch = f"touch {canary}"
    hostile_config = hostile_home / ".gitconfig"
    hostile_config.write_text(
        f'[credential]\n\thelper = "!{touch}-credential"\n'
        f'[core]\n\tfsmonitor = "{touch}-fsmonitor"\n\tsshCommand = "{touch}-ssh"\n'
        f"\thooksPath = {hostile_home}/hooks\n"
        f'[filter "lfs"]\n\tsmudge = "{touch}-smudge"\n\tprocess = "{touch}-process"\n'
        "\trequired = true\n"
        f'[url "file://{tmp_path}/elsewhere"]\n\tinsteadOf = file://{repo.path}\n'
        f'[uploadpack]\n\tpackObjectsHook = "{touch}-packhook"\n'
    )
    hooks = hostile_home / "hooks"
    hooks.mkdir()
    for name in ("post-checkout", "pre-auto-gc", "reference-transaction", "post-merge"):
        hook = hooks / name
        hook.write_text(f"#!/bin/sh\n{touch}-hook-{name}\n")
        hook.chmod(0o755)
    template = tmp_path / "template"
    (template / "hooks").mkdir(parents=True)
    template_hook = template / "hooks" / "reference-transaction"
    template_hook.write_text(f"#!/bin/sh\n{touch}-template-hook\n")
    template_hook.chmod(0o755)
    for name, value in {
        "HOME": str(hostile_home),
        "XDG_CONFIG_HOME": str(hostile_home),
        "GIT_CONFIG_GLOBAL": str(hostile_config),
        "GIT_CONFIG_SYSTEM": str(hostile_config),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": f"{touch}-env-config",
        "GIT_ASKPASS": f"{touch}-askpass",
        "SSH_ASKPASS": f"{touch}-ssh-askpass",
        "GIT_SSH_COMMAND": f"{touch}-git-ssh",
        "GIT_PROXY_COMMAND": f"{touch}-proxy",
        "GIT_TEMPLATE_DIR": str(template),
        "GIT_EXTERNAL_DIFF": f"{touch}-diff",
        "GIT_TRACE": str(tmp_path / "canary-trace"),
        "GIT_DIR": str(tmp_path / "canary-gitdir"),
        "GIT_WORK_TREE": str(tmp_path / "canary-worktree"),
        "GIT_ALLOW_PROTOCOL": "ext:ssh:file:https",
        "HTTPS_PROXY": "http://proxy.evil.example:8080",
    }.items():
        monkeypatch.setenv(name, value)

    with GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), sha) as store:
        snapshot = build_snapshot(store, "https://github.com/example/demo")
        scopes = GitFetcher().run_git(
            store.path, store.path.parent / "home", "file", ["config", "--list", "--show-scope"]
        )

    assert len(snapshot.files) == 3
    assert sorted(path.name for path in tmp_path.iterdir() if path.name.startswith("canary")) == []
    listed = scopes.stdout.decode().splitlines()
    assert listed
    assert {line.split("\t", 1)[0] for line in listed} <= {"command", "local"}
    assert "hostile" not in scopes.stdout.decode()


def test_git_gets_a_fresh_environment_with_no_inherited_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from patchpilot.repo.fetch import git_environment

    monkeypatch.setenv("ANTHROPIC_API_KEY", "planted")
    monkeypatch.setenv("PATCHPILOT_GITHUB_TOKEN", "planted")

    environment = git_environment("/usr/bin:/bin", Path("/nonexistent-home"), "https")

    assert "planted" not in environment.values()
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert environment["GIT_ALLOW_PROTOCOL"] == "https"
    assert not any("TOKEN" in name or "KEY" in name for name in environment)


def test_git_always_runs_with_the_hardening_options() -> None:
    from patchpilot.repo.fetch import git_config

    options = git_config("https")
    settings = dict(option.split("=", 1) for option in options if option != "-c")

    assert settings["credential.helper"] == ""
    assert settings["core.hooksPath"] == "/dev/null"
    assert settings["protocol.allow"] == "never"
    assert settings["protocol.https.allow"] == "always"
    assert "protocol.file.allow" not in settings
    assert settings["http.followRedirects"] == "false"
    assert settings["fetch.recurseSubmodules"] == "false"
    assert settings["transfer.fsckObjects"] == "true"


# -- snapshot contents ----------------------------------------------------------


def test_symbolic_links_and_submodules_are_left_out_and_reported(
    repo: LocalRepo, scratch: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("planted-secret")
    repo.write({"src/app.py": "APP = 1\n"})
    repo.add_symlink("link-to-secret", str(secret))
    repo.add_symlink("src/link-to-etc", "/etc")
    repo.add_symlink("relative-escape", "../../../../etc/passwd")
    repo.git("add", "--all")
    repo.add_submodule_entry("vendor/lib", "1" * 40)
    sha = repo.commit(message="links", stage_all=False)

    with GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), sha) as store:
        snapshot = build_snapshot(store, "https://github.com/example/demo")

    assert dict(snapshot.files) == {"src/app.py": b"APP = 1\n"}
    assert b"planted-secret" not in b"".join(snapshot.files.values())
    assert {(entry.path, entry.reason) for entry in snapshot.skipped} == {
        ("link-to-secret", "symbolic_link"),
        ("src/link-to-etc", "symbolic_link"),
        ("relative-escape", "symbolic_link"),
        ("vendor/lib", "submodule"),
    }


def test_oversized_and_oddly_named_files_are_left_out_and_reported(
    repo: LocalRepo, scratch: Path
) -> None:
    repo.write(
        {
            "ok.py": "OK = 1\n",
            "big.bin": b"\0" * (1_048_576 + 1),
            "name with spaces.py": "x = 1\n",
            "naïve.py": "x = 1\n",
            "weird\nname.py": "x = 1\n",
            "--flag.py": "x = 1\n",
            "$(id).py": "x = 1\n",
        }
    )
    sha = repo.commit(message="odd files")

    with GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), sha) as store:
        snapshot = build_snapshot(store, "https://github.com/example/demo")

    assert sorted(snapshot.files) == ["--flag.py", "ok.py"]
    reasons = {entry.path: entry.reason for entry in snapshot.skipped}
    assert reasons["big.bin"] == "file_too_large"
    assert reasons["name with spaces.py"] == "unsupported_name"
    assert reasons["$(id).py"] == "unsupported_name"
    assert reasons["weird\\nname.py"] == "unsupported_name"
    assert all(entry.path.isascii() and "\n" not in entry.path for entry in snapshot.skipped)
    assert snapshot.skipped_total == 5


def test_checkout_filters_and_lfs_pointers_are_stored_as_plain_data(
    repo: LocalRepo, scratch: Path, tmp_path: Path
) -> None:
    canary = tmp_path / "filter-canary"
    pointer = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "a" * 64 + "\nsize 9\n"
    repo.write(
        {
            ".gitattributes": "*.dat filter=evil\n*.bin filter=lfs diff=lfs merge=lfs -text\n",
            "data.dat": "raw data\n",
            "model.bin": pointer,
            ".gitmodules": '[submodule "x"]\n\tpath = x\n\turl = ext::sh -c touch% /tmp/x\n',
        }
    )
    repo.git("config", "filter.evil.smudge", f"touch {canary}")
    repo.git("config", "filter.evil.clean", "cat")
    sha = repo.commit(message="filters")

    with GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), sha) as store:
        snapshot = build_snapshot(store, "https://github.com/example/demo")

    assert snapshot.files["data.dat"] == b"raw data\n"
    assert snapshot.files["model.bin"] == pointer.encode()
    assert ".gitmodules" in snapshot.files
    assert not canary.exists()


def test_a_commit_with_too_many_files_is_refused(
    repo: LocalRepo, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from patchpilot.repo import snapshot as snapshot_module

    monkeypatch.setattr(snapshot_module, "MAX_FILES", 3)
    sha = repo.commit({f"f{index}.py": "x = 1\n" for index in range(5)})

    with (
        pytest.raises(FetchError) as caught,
        GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), sha) as store,
    ):
        build_snapshot(store, "https://github.com/example/demo")

    assert caught.value.status is FetchStatus.FETCH_TOO_LARGE


def test_a_commit_whose_files_are_too_large_in_total_is_refused(
    repo: LocalRepo, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from patchpilot.repo import snapshot as snapshot_module

    monkeypatch.setattr(snapshot_module, "MAX_TOTAL_BYTES", 1000)
    sha = repo.commit({f"f{index}.py": "x" * 400 for index in range(4)})

    with (
        pytest.raises(FetchError) as caught,
        GitFetcher().fetch(RemoteSource.local_for_tests(repo.path), sha) as store,
    ):
        build_snapshot(store, "https://github.com/example/demo")

    assert caught.value.status is FetchStatus.FETCH_TOO_LARGE


def test_the_same_commit_always_gives_the_same_snapshot_digest(
    repo: LocalRepo, scratch: Path
) -> None:
    sha = repo.commit(FILES)
    source = RemoteSource.local_for_tests(repo.path)
    digests = []
    for _ in range(2):
        with GitFetcher().fetch(source, sha) as store:
            digests.append(build_snapshot(store, "https://github.com/example/demo").tree_sha256)

    assert digests[0] == digests[1]
