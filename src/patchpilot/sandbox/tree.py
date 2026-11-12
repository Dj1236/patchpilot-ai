"""Safe packaging of a source tree for the sandbox.

A source tree is read on the host into an in-memory mapping of relative path
to bytes. Nothing is executed or imported. Symbolic links and special files
are rejected instead of followed, so a tree can never pull in content from
outside its own root.
"""

import hashlib
import io
import os
import re
import stat
import tarfile
from collections.abc import Mapping
from pathlib import Path

IGNORED_NAMES = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".DS_Store", ".git"}
)

MAX_FILES = 2000
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 16 * 1_048_576
_MAX_PATH_LENGTH = 255

_PART_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class TreeError(ValueError):
    """The source tree is unsafe or too large to package."""


def validate_relative_path(path: str) -> str:
    """Return ``path`` if it is a plain relative POSIX path, else raise."""
    if not path or len(path) > _MAX_PATH_LENGTH:
        raise TreeError(f"path {path!r} is empty or too long")
    if path.startswith("/"):
        raise TreeError(f"path {path!r} is absolute")
    for part in path.split("/"):
        if part in ("", ".", ".."):
            raise TreeError(f"path {path!r} contains an unsafe segment")
        if not _PART_PATTERN.fullmatch(part):
            raise TreeError(f"path {path!r} contains unsupported characters")
    return path


def _read_regular_file(path: Path) -> bytes:
    """Read a regular file without ever following a symbolic link."""
    try:
        # O_NONBLOCK keeps a named pipe from blocking the open; the file type
        # is checked on the descriptor straight after.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise TreeError(f"cannot open {path} without following links: {exc}") from exc
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise TreeError(f"{path} is not a regular file")
        if info.st_size > MAX_FILE_BYTES:
            raise TreeError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise TreeError(f"{path} is larger than {MAX_FILE_BYTES} bytes")
    return data


def collect_tree(root: Path) -> dict[str, bytes]:
    """Read every file under ``root`` into memory, keyed by relative path."""
    if root.is_symlink() or not root.is_dir():
        raise TreeError(f"{root} is not a real directory")

    def unreadable(error: OSError) -> None:
        # os.walk skips directories it cannot list unless told otherwise. A
        # silently incomplete tree would be packaged and pinned as if whole.
        raise TreeError(f"cannot read directory {error.filename}: {error.strerror}")

    files: dict[str, bytes] = {}
    total = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False, onerror=unreadable):
        base = Path(current)
        dirnames[:] = sorted(name for name in dirnames if name not in IGNORED_NAMES)
        for name in dirnames:
            if (base / name).is_symlink():
                raise TreeError(f"{base / name} is a symbolic link")
        for name in sorted(filenames):
            if name in IGNORED_NAMES:
                continue
            path = base / name
            if path.is_symlink():
                raise TreeError(f"{path} is a symbolic link")
            relative = validate_relative_path(path.relative_to(root).as_posix())
            data = _read_regular_file(path)
            total += len(data)
            if len(files) >= MAX_FILES:
                raise TreeError(f"{root} contains more than {MAX_FILES} files")
            if total > MAX_TOTAL_BYTES:
                raise TreeError(f"{root} is larger than {MAX_TOTAL_BYTES} bytes")
            files[relative] = data
    return files


def overlay(
    base: Mapping[str, bytes], layer: Mapping[str, bytes], *, allow_overwrite: bool
) -> dict[str, bytes]:
    """Return ``base`` with ``layer`` placed on top."""
    merged = dict(base)
    for path, data in layer.items():
        validate_relative_path(path)
        if path in merged and not allow_overwrite:
            raise TreeError(f"{path} would overwrite an existing file")
        merged[path] = data
    return merged


def tree_digest(files: Mapping[str, bytes]) -> str:
    """Return a SHA-256 digest that pins the exact paths and contents."""
    digest = hashlib.sha256()
    for path in sorted(files):
        encoded = path.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(files[path]).to_bytes(8, "big"))
        digest.update(files[path])
    return digest.hexdigest()


def build_tar(files: Mapping[str, bytes]) -> bytes:
    """Build a deterministic tar archive containing only regular files."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(files):
            validate_relative_path(path)
            info = tarfile.TarInfo(name=path)
            info.size = len(files[path])
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(files[path]))
    return buffer.getvalue()
