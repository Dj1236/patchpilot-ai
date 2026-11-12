"""Fetch one pinned commit of an approved repository, without a checkout.

The commit is fetched into a temporary bare git object store and read from
there object by object. No working tree is ever created on the host, so git
runs no hook, checkout filter, LFS download or submodule update, and no file
from the repository touches the host filesystem by name.

git runs with a fresh environment and an explicit configuration: the system
and user configuration are ignored, credential helpers and prompts are off,
and only one transport protocol is allowed.

Storage is bounded while a fetch runs by two mechanisms: the operating
system's per-file size limit on the git process, and a watchdog that measures
the temporary directory and kills git's process group when it grows past the
limit. Neither is an absolute disk quota. The watchdog polls, so the limit
can be overshot by whatever git writes within one polling interval, and the
per-file limit caps each file, not their sum.
"""

import re
import shutil
import signal
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field

from patchpilot.intake.allowlist import ApprovedRepository
from patchpilot.intake.github import is_safe_branch_name
from patchpilot.intake.urls import OWNER_PATTERN, REPOSITORY_PATTERN
from patchpilot.sandbox.models import FrozenModel
from patchpilot.sandbox.process import ProcessResult, run_bounded

_SHA = re.compile(r"[0-9a-f]{40}")
_GITHUB_REMOTE = re.compile(rf"https://github\.com/{OWNER_PATTERN}/{REPOSITORY_PATTERN}\.git")
_LOCAL_REMOTE = re.compile(r"file:///[A-Za-z0-9._/+-]+")
_CONTROL_OUTPUT_BYTES = 65_536
_CONTROL_TIMEOUT = 30.0
_FALLBACK_REF = "refs/patchpilot/fallback"
# Words git prints when the remote could not be reached at all, as opposed to
# being reached and not providing the commit.
_UNREACHABLE = (
    b"could not resolve host",
    b"unable to access",
    b"failed to connect",
    b"connection refused",
    b"connection timed out",
    b"does not appear to be a git repository",
    b"repository not found",
    b"ssl",
)
_TOO_LARGE = (b"file too large", b"file size limit")


class FetchStatus(StrEnum):
    COMMIT_UNAVAILABLE = "commit_unavailable"
    FETCH_TIMEOUT = "fetch_timeout"
    FETCH_TOO_LARGE = "fetch_too_large"
    FETCH_FAILED = "fetch_failed"
    GIT_UNAVAILABLE = "git_unavailable"
    CLEANUP_FAILED = "cleanup_failed"


class FetchError(Exception):
    """A fetch was refused or failed. Carries a machine-readable status."""

    def __init__(self, status: FetchStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


class FetchLimits(FrozenModel):
    timeout_seconds: int = Field(default=120, ge=1, le=900)
    max_store_mb: int = Field(default=200, ge=1, le=4096)
    fallback_depth: int = Field(default=200, ge=1, le=5000)
    max_listing_bytes: int = Field(default=8 * 1_048_576, ge=1024, le=64 * 1_048_576)


@dataclass(frozen=True)
class RemoteSource:
    """Where to fetch from. Production code only builds this from an allowlist entry."""

    url: str
    protocol: Literal["https", "file"]

    def __post_init__(self) -> None:
        pattern = _GITHUB_REMOTE if self.protocol == "https" else _LOCAL_REMOTE
        if pattern.fullmatch(self.url) is None or ".." in self.url:
            raise ValueError("the remote URL is not an accepted form")

    @classmethod
    def for_repository(cls, repository: ApprovedRepository) -> "RemoteSource":
        """Build the HTTPS remote for an approved GitHub repository."""
        return cls(url=f"{repository.url}.git", protocol="https")

    @classmethod
    def local_for_tests(cls, path: Path) -> "RemoteSource":
        """Build a remote for a repository on local disk. For tests only."""
        return cls(url=path.resolve().as_uri(), protocol="file")


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    kind: str
    object_id: str
    size: int | None
    raw_path: bytes


def directory_size(root: Path) -> int:
    """Return the total size in bytes of the files under ``root``."""
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.lstat()
            except OSError:
                continue
            if entry.is_dir() and not entry.is_symlink():
                stack.append(entry)
            else:
                total += info.st_size
    return total


def git_config(protocol: str) -> list[str]:
    """Return the ``-c`` options every git command runs with."""
    settings = {
        # No credentials, from any source.
        "credential.helper": "",
        "core.askPass": "",
        # Nothing from the repository or the machine may run.
        "core.hooksPath": "/dev/null",
        "core.fsmonitor": "false",
        "core.sshCommand": "false",
        "core.pager": "cat",
        "core.attributesFile": "/dev/null",
        "core.symlinks": "false",
        "core.protectHFS": "true",
        "core.protectNTFS": "true",
        # One transport only, no redirects to another host.
        "protocol.allow": "never",
        f"protocol.{protocol}.allow": "always",
        "http.followRedirects": "false",
        "http.lowSpeedLimit": "1000",
        "http.lowSpeedTime": "30",
        # Check what arrives, keep it as one pack, fetch nothing extra.
        "fetch.fsckObjects": "true",
        "transfer.fsckObjects": "true",
        "fetch.unpackLimit": "1",
        "fetch.recurseSubmodules": "false",
        "submodule.recurse": "false",
        "fetch.prune": "false",
        "gc.auto": "0",
        "maintenance.auto": "false",
        "advice.detachedHead": "false",
    }
    options: list[str] = []
    for key, value in settings.items():
        options += ["-c", f"{key}={value}"]
    return options


def git_environment(path: str, home: Path, protocol: str) -> dict[str, str]:
    """Return the complete environment for git. Nothing is inherited implicitly."""
    return {
        "PATH": path,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": protocol,
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }


class ObjectStore:
    """Read access to the one pinned commit inside a fetched bare repository."""

    def __init__(
        self, fetcher: "GitFetcher", path: Path, home: Path, protocol: str, commit_sha: str
    ) -> None:
        self._fetcher = fetcher
        self._home = home
        self._protocol = protocol
        self.path = path
        self.commit_sha = commit_sha

    def list_tree(self) -> list[TreeEntry]:
        """List every entry of the pinned commit's tree, with sizes, without content."""
        limits = self._fetcher.limits
        result = self._fetcher.run_git(
            self.path,
            self._home,
            self._protocol,
            ["ls-tree", "-r", "-z", "-l", "--full-tree", "--end-of-options", self.commit_sha],
            max_output_bytes=limits.max_listing_bytes,
        )
        if result.stdout_truncated:
            raise FetchError(FetchStatus.FETCH_TOO_LARGE, "the file listing exceeds its limit")
        if result.timed_out or result.exit_code != 0:
            raise FetchError(FetchStatus.FETCH_FAILED, "the commit's tree could not be listed")
        entries: list[TreeEntry] = []
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            meta, separator, raw_path = record.partition(b"\t")
            fields = meta.split()
            if not separator or len(fields) != 4:
                raise FetchError(FetchStatus.FETCH_FAILED, "unexpected output from git ls-tree")
            mode, kind, object_id, size = (field.decode("ascii", "replace") for field in fields)
            entries.append(
                TreeEntry(
                    mode=mode,
                    kind=kind,
                    object_id=object_id,
                    size=int(size) if size.isdigit() else None,
                    raw_path=raw_path,
                )
            )
        return entries

    def read_blobs(self, object_ids: Sequence[str], total_bytes: int) -> dict[str, bytes]:
        """Read file contents by object ID. ``total_bytes`` bounds the output."""
        if not object_ids:
            return {}
        if any(_SHA.fullmatch(object_id) is None for object_id in object_ids):
            raise ValueError("object IDs must be full SHA-1 hashes")
        budget = total_bytes + 128 * len(object_ids) + 1024
        result = self._fetcher.run_git(
            self.path,
            self._home,
            self._protocol,
            ["cat-file", "--batch"],
            max_output_bytes=budget,
            input_bytes=("\n".join(object_ids) + "\n").encode("ascii"),
        )
        if result.timed_out or result.exit_code != 0 or result.stdout_truncated:
            raise FetchError(FetchStatus.FETCH_FAILED, "file contents could not be read")
        blobs: dict[str, bytes] = {}
        data = result.stdout
        position = 0
        for object_id in object_ids:
            line_end = data.find(b"\n", position)
            header = data[position:line_end].split() if line_end >= 0 else []
            if len(header) != 3 or header[0] != object_id.encode() or header[1] != b"blob":
                raise FetchError(FetchStatus.FETCH_FAILED, f"object {object_id} is not a file")
            size = int(header[2])
            start = line_end + 1
            content = data[start : start + size]
            if len(content) != size:
                raise FetchError(FetchStatus.FETCH_FAILED, "file contents were cut short")
            blobs[object_id] = content
            position = start + size + 1
        return blobs


class GitFetcher:
    """Fetch pinned commits into temporary bare repositories."""

    def __init__(
        self,
        *,
        git_bin: str = "git",
        limits: FetchLimits | None = None,
        path: str | None = None,
        process_runner: Callable[..., ProcessResult] = run_bounded,
        enforce_file_limit: bool = True,
        enforce_watchdog: bool = True,
    ) -> None:
        self.limits = limits or FetchLimits()
        self._git_bin = git_bin
        self._path = path if path is not None else "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"
        self._run = process_runner
        self._enforce_file_limit = enforce_file_limit
        self._enforce_watchdog = enforce_watchdog

    # -- running git -------------------------------------------------------

    def run_git(
        self,
        store: Path,
        home: Path,
        protocol: str,
        args: Sequence[str],
        *,
        max_output_bytes: int = _CONTROL_OUTPUT_BYTES,
        timeout: float = _CONTROL_TIMEOUT,
        input_bytes: bytes | None = None,
        guard: Path | None = None,
    ) -> ProcessResult:
        """Run one git command against ``store`` with the hardened setup.

        ``guard`` names the directory whose size is limited while the command
        runs; it is set for fetches and left out for local reads.
        """
        limit = self.limits.max_store_mb * 1_048_576
        argv = [self._git_bin, *git_config(protocol), "--git-dir", str(store), *args]
        try:
            return self._run(
                argv,
                timeout=timeout,
                max_output_bytes=max_output_bytes,
                env=git_environment(self._path, home, protocol),
                input_bytes=input_bytes,
                new_process_group=True,
                max_file_bytes=limit if guard is not None and self._enforce_file_limit else None,
                abort_when=(
                    (lambda: directory_size(guard) > limit)
                    if guard is not None and self._enforce_watchdog
                    else None
                ),
            )
        except OSError as exc:
            raise FetchError(FetchStatus.GIT_UNAVAILABLE, f"cannot run git: {exc}") from exc

    def _fetch(
        self, store: Path, home: Path, protocol: str, work: Path, args: Sequence[str]
    ) -> ProcessResult:
        result = self.run_git(
            store,
            home,
            protocol,
            ["fetch", "--quiet", "--no-tags", "--no-recurse-submodules", *args],
            timeout=float(self.limits.timeout_seconds),
            guard=work,
        )
        limit = self.limits.max_store_mb * 1_048_576
        stderr = result.stderr.lower()
        if result.timed_out:
            raise FetchError(FetchStatus.FETCH_TIMEOUT, "the fetch exceeded its time limit")
        if (
            result.aborted
            or result.exit_code == -signal.SIGXFSZ
            or any(phrase in stderr for phrase in _TOO_LARGE)
            or directory_size(work) > limit
        ):
            raise FetchError(
                FetchStatus.FETCH_TOO_LARGE,
                f"the fetch exceeded its storage limit of {self.limits.max_store_mb} MB",
            )
        return result

    def _object_type(self, store: Path, home: Path, protocol: str, commit_sha: str) -> str | None:
        result = self.run_git(store, home, protocol, ["cat-file", "-t", commit_sha])
        if result.timed_out or result.exit_code != 0:
            return None
        return result.stdout.decode("ascii", "replace").strip()

    def _has_pinned_commit(self, store: Path, home: Path, protocol: str, commit_sha: str) -> bool:
        """Return True only if the object store holds ``commit_sha`` and it is a commit."""
        if self._object_type(store, home, protocol, commit_sha) != "commit":
            return False
        resolved = self.run_git(
            store,
            home,
            protocol,
            ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{commit_sha}^{{commit}}"],
        )
        return resolved.exit_code == 0 and resolved.stdout.decode("ascii", "replace").strip() == (
            commit_sha
        )

    # -- public API --------------------------------------------------------

    @contextmanager
    def fetch(
        self,
        source: RemoteSource,
        commit_sha: str,
        *,
        fallback_branch: str | None = None,
    ) -> Iterator[ObjectStore]:
        """Yield an object store holding exactly ``commit_sha`` from ``source``.

        The commit is first requested by its SHA. If that does not produce it
        and ``fallback_branch`` is given, the branch is fetched to a bounded
        depth instead. In both cases the pinned SHA must then be present and
        be a commit; otherwise the fetch fails with ``commit_unavailable``.
        The tip of a fetched branch is never used in its place. The temporary
        store is removed when the block ends.
        """
        if _SHA.fullmatch(commit_sha) is None:
            raise ValueError("a commit must be pinned by its full 40-character SHA")
        if fallback_branch is not None and not is_safe_branch_name(fallback_branch):
            raise ValueError("the fallback branch name is unsafe")

        protocol = source.protocol
        work = Path(tempfile.mkdtemp(prefix="patchpilot-fetch-"))
        try:
            work.chmod(0o700)
            home = work / "home"
            store = work / "store"
            home.mkdir()
            init = self.run_git(
                store, home, protocol, ["init", "--bare", "--quiet", "--template=", str(store)]
            )
            if init.timed_out or init.exit_code != 0:
                raise FetchError(FetchStatus.GIT_UNAVAILABLE, "git could not create a repository")

            direct = self._fetch(
                store,
                home,
                protocol,
                work,
                ["--no-write-fetch-head", "--depth=1", "--end-of-options", source.url, commit_sha],
            )
            if not self._has_pinned_commit(store, home, protocol, commit_sha):
                unreachable = any(phrase in direct.stderr.lower() for phrase in _UNREACHABLE)
                if direct.exit_code != 0 and unreachable:
                    raise FetchError(FetchStatus.FETCH_FAILED, "the remote could not be reached")
                if fallback_branch is not None:
                    fallback = self._fetch(
                        store,
                        home,
                        protocol,
                        work,
                        [
                            "--no-write-fetch-head",
                            f"--depth={self.limits.fallback_depth}",
                            "--end-of-options",
                            source.url,
                            f"+refs/heads/{fallback_branch}:{_FALLBACK_REF}",
                        ],
                    )
                    if fallback.exit_code != 0:
                        raise FetchError(
                            FetchStatus.FETCH_FAILED, "the fallback branch could not be fetched"
                        )
                if not self._has_pinned_commit(store, home, protocol, commit_sha):
                    found = self._object_type(store, home, protocol, commit_sha)
                    detail = (
                        f"the pinned object is a {found}, not a commit"
                        if found
                        else "the remote did not provide the pinned commit"
                    )
                    raise FetchError(FetchStatus.COMMIT_UNAVAILABLE, f"{commit_sha}: {detail}")

            yield ObjectStore(self, store, home, protocol, commit_sha)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            if work.exists():
                raise FetchError(
                    FetchStatus.CLEANUP_FAILED, "temporary fetch files could not be removed"
                )
