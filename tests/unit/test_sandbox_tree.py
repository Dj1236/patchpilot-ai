import io
import os
import tarfile
from pathlib import Path

import pytest

from patchpilot.sandbox import tree
from patchpilot.sandbox.tree import (
    TreeError,
    build_tar,
    collect_tree,
    overlay,
    tree_digest,
    validate_relative_path,
)


def _write(root: Path, relative: str, content: str = "x = 1\n") -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_collects_regular_files_by_relative_path(tmp_path: Path) -> None:
    _write(tmp_path, "src/pkg/mod.py", "a = 1\n")
    _write(tmp_path, "tests/test_mod.py", "b = 2\n")

    assert collect_tree(tmp_path) == {
        "src/pkg/mod.py": b"a = 1\n",
        "tests/test_mod.py": b"b = 2\n",
    }


def test_cache_directories_are_left_out(tmp_path: Path) -> None:
    _write(tmp_path, "mod.py")
    _write(tmp_path, "__pycache__/mod.cpython-312.pyc")
    _write(tmp_path, ".pytest_cache/v/cache/lastfailed")

    assert list(collect_tree(tmp_path)) == ["mod.py"]


def test_a_symlink_to_a_file_outside_the_tree_is_rejected(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("planted-secret")
    root = tmp_path / "repo"
    _write(root, "mod.py")
    (root / "leak.txt").symlink_to(secret)

    with pytest.raises(TreeError, match="symbolic link"):
        collect_tree(root)


def test_a_symlinked_directory_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _write(outside, "data.py")
    root = tmp_path / "repo"
    _write(root, "mod.py")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(TreeError, match="symbolic link"):
        collect_tree(root)


def test_a_symlinked_root_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _write(real, "mod.py")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(TreeError, match="not a real directory"):
        collect_tree(link)


def test_a_named_pipe_is_rejected_without_blocking(tmp_path: Path) -> None:
    _write(tmp_path, "mod.py")
    os.mkfifo(tmp_path / "pipe")

    with pytest.raises(TreeError):
        collect_tree(tmp_path)


def test_an_oversized_file_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tree, "MAX_FILE_BYTES", 8)
    _write(tmp_path, "big.py", "x" * 9)

    with pytest.raises(TreeError, match="larger than"):
        collect_tree(tmp_path)


def test_too_many_files_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tree, "MAX_FILES", 2)
    for index in range(3):
        _write(tmp_path, f"m{index}.py")

    with pytest.raises(TreeError, match="more than"):
        collect_tree(tmp_path)


@pytest.mark.parametrize(
    "path",
    ["/abs/path.py", "../up.py", "a/../b.py", "a//b.py", "./a.py", "", "a b.py", "a\nb.py"],
)
def test_unsafe_relative_paths_are_rejected(path: str) -> None:
    with pytest.raises(TreeError):
        validate_relative_path(path)


def test_overlay_can_replace_files_only_when_allowed() -> None:
    base = {"a.py": b"old"}

    assert overlay(base, {"a.py": b"new"}, allow_overwrite=True) == {"a.py": b"new"}
    with pytest.raises(TreeError, match="overwrite"):
        overlay(base, {"a.py": b"new"}, allow_overwrite=False)
    assert base == {"a.py": b"old"}


def test_overlay_rejects_paths_that_escape_the_tree() -> None:
    with pytest.raises(TreeError):
        overlay({}, {"../escape.py": b""}, allow_overwrite=True)


def test_digest_depends_on_paths_and_contents_but_not_on_order() -> None:
    first = tree_digest({"a.py": b"1", "b.py": b"2"})

    assert first == tree_digest({"b.py": b"2", "a.py": b"1"})
    assert first != tree_digest({"a.py": b"1", "b.py": b"3"})
    assert first != tree_digest({"a.py": b"1", "c.py": b"2"})
    # Moving a byte between the path and the content must change the digest.
    assert tree_digest({"ab": b"c"}) != tree_digest({"a": b"bc"})


def test_tar_is_deterministic_and_holds_only_plain_files() -> None:
    files = {"tests/test_a.py": b"pass\n", "src/a.py": b"A = 1\n"}

    archive = build_tar(files)

    assert archive == build_tar(dict(reversed(files.items())))
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = tar.getmembers()
    assert [member.name for member in members] == ["src/a.py", "tests/test_a.py"]
    assert all(member.isreg() and member.mode == 0o644 for member in members)
    assert all(member.uid == 0 and member.mtime == 0 for member in members)


def test_tar_refuses_an_escaping_path() -> None:
    with pytest.raises(TreeError):
        build_tar({"../../etc/cron.d/job": b""})
