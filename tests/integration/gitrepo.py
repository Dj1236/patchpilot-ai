"""Build small local git repositories for fetch tests.

These helpers run git directly, with an isolated configuration. They are test
scaffolding: PatchPilot's own code never creates commits or working trees.
"""

import os
import subprocess
from pathlib import Path

_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.invalid",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
    "LC_ALL": "C",
}


class LocalRepo:
    """A throwaway repository with a working tree, used as a fetch source."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.git("init", "--quiet", "--initial-branch=main")

    def git(self, *args: str, stdin: bytes | None = None) -> str:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(self.path), *args],  # noqa: S607
            input=stdin,
            capture_output=True,
            check=True,
            env={**_ENV, "HOME": str(self.path.parent)},
        )
        return completed.stdout.decode().strip()

    def write(self, files: dict[str, bytes | str]) -> None:
        for relative, content in files.items():
            target = self.path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode() if isinstance(content, str) else content)

    def commit(
        self,
        files: dict[str, bytes | str] | None = None,
        message: str = "change",
        *,
        stage_all: bool = True,
    ) -> str:
        """Write ``files``, commit, and return the new commit's SHA.

        ``stage_all=False`` commits the index as it is, which keeps entries
        such as submodule pointers that have no file in the working tree.
        """
        self.write(files or {})
        if stage_all:
            self.git("add", "--all")
        self.git("commit", "--quiet", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")

    def add_symlink(self, relative: str, target: str) -> None:
        link = self.path / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)

    def add_submodule_entry(self, relative: str, commit_sha: str) -> None:
        """Record a submodule pointer in the index without cloning anything."""
        self.git("update-index", "--add", "--cacheinfo", f"160000,{commit_sha},{relative}")

    def object_id(self, revision: str) -> str:
        return self.git("rev-parse", revision)
