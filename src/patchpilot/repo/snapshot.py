"""Build a bounded, in-memory snapshot of one pinned commit.

A snapshot maps relative paths to file contents, the same shape the sandbox
and the task loader already use. It is built from git objects, not from files
on disk, so there is no working tree to escape from. Entries that are not
plain files are left out and listed, never followed or resolved.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from patchpilot.intake.allowlist import ApprovedRepository
from patchpilot.repo.fetch import FetchError, FetchStatus, GitFetcher, ObjectStore, RemoteSource
from patchpilot.sandbox.models import FrozenModel
from patchpilot.sandbox.tree import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    TreeError,
    tree_digest,
    validate_relative_path,
)

SkipReason = Literal[
    "symbolic_link", "submodule", "file_too_large", "unsupported_name", "not_a_file"
]
_MAX_REPORTED_SKIPS = 200
_MAX_REPORTED_PATH = 200
_SYMLINK_MODE = "120000"
_SUBMODULE_MODE = "160000"
_FILE_MODES = frozenset({"100644", "100755"})


class SkippedEntry(FrozenModel):
    """A tree entry left out of the snapshot. ``path`` is escaped for display."""

    path: str
    reason: SkipReason


@dataclass(frozen=True)
class RepositorySnapshot:
    """The files of one pinned commit of an approved repository.

    ``files`` holds raw bytes from the repository. They are untrusted data:
    they are only ever stored, searched, shown as data or run inside the
    sandbox.
    """

    url: str
    commit_sha: str
    files: Mapping[str, bytes]
    tree_sha256: str
    skipped: tuple[SkippedEntry, ...]
    skipped_total: int


def _display_path(raw_path: bytes) -> str:
    """Return a printable, bounded rendering of a path that may be hostile."""
    return ascii(raw_path.decode("utf-8", errors="replace"))[1:-1][:_MAX_REPORTED_PATH]


def build_snapshot(store: ObjectStore, url: str) -> RepositorySnapshot:
    """Read the pinned commit of ``store`` into memory.

    Sizes are known from the tree listing before any content is read, so
    oversized files are skipped without being loaded. A commit with too many
    files or too many bytes in total is refused as a whole.
    """
    accepted: list[tuple[str, str, int]] = []
    skipped: list[SkippedEntry] = []
    total = 0

    def skip(raw_path: bytes, reason: SkipReason) -> None:
        skipped.append(SkippedEntry(path=_display_path(raw_path), reason=reason))

    for entry in store.list_tree():
        if entry.mode == _SYMLINK_MODE:
            skip(entry.raw_path, "symbolic_link")
        elif entry.mode == _SUBMODULE_MODE or entry.kind == "commit":
            skip(entry.raw_path, "submodule")
        elif entry.kind != "blob" or entry.mode not in _FILE_MODES or entry.size is None:
            skip(entry.raw_path, "not_a_file")
        elif entry.size > MAX_FILE_BYTES:
            skip(entry.raw_path, "file_too_large")
        else:
            try:
                path = validate_relative_path(entry.raw_path.decode("utf-8"))
            except (UnicodeDecodeError, TreeError):
                skip(entry.raw_path, "unsupported_name")
                continue
            accepted.append((path, entry.object_id, entry.size))
            total += entry.size
            if len(accepted) > MAX_FILES:
                raise FetchError(
                    FetchStatus.FETCH_TOO_LARGE, f"the commit has more than {MAX_FILES} files"
                )
            if total > MAX_TOTAL_BYTES:
                raise FetchError(
                    FetchStatus.FETCH_TOO_LARGE,
                    f"the commit's files exceed {MAX_TOTAL_BYTES} bytes in total",
                )

    blobs = store.read_blobs(sorted({object_id for _, object_id, _ in accepted}), total)
    files: dict[str, bytes] = {}
    for path, object_id, size in accepted:
        content = blobs[object_id]
        if len(content) != size:
            raise FetchError(FetchStatus.FETCH_FAILED, f"{path} changed size while being read")
        files[path] = content

    return RepositorySnapshot(
        url=url,
        commit_sha=store.commit_sha,
        files=files,
        tree_sha256=tree_digest(files),
        skipped=tuple(skipped[:_MAX_REPORTED_SKIPS]),
        skipped_total=len(skipped),
    )


def fetch_snapshot(
    repository: ApprovedRepository,
    commit_sha: str,
    *,
    fetcher: GitFetcher | None = None,
    fallback_branch: str | None = None,
) -> RepositorySnapshot:
    """Fetch ``commit_sha`` of an approved repository and return its snapshot."""
    fetcher = fetcher or GitFetcher()
    source = RemoteSource.for_repository(repository)
    with fetcher.fetch(source, commit_sha, fallback_branch=fallback_branch) as store:
        return build_snapshot(store, repository.url)
