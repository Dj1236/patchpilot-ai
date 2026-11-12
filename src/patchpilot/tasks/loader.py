"""Load evaluation tasks from disk and build their file trees.

Task directory layout::

    <task-id>/
        task.toml        full task definition (internal)
        repo/            agent-visible starting state
        internal/        never copied into an agent workspace
            hidden_tests/    regression tests, laid out as they overlay the repo
            reference_fix/   corrected files, laid out as they overlay the repo

``agent_files`` returns only the repository. The hidden tests and the
reference fix are reachable only through ``evaluation_files``, which is for
the evaluator.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from patchpilot.sandbox.tree import TreeError, collect_tree, overlay, tree_digest
from patchpilot.tasks.schema import INTERNAL_DIR, FixtureRepository, Task

TASK_FILE = "task.toml"
_MAX_TASK_FILE_BYTES = 65_536


class TaskError(ValueError):
    """A task definition is invalid or does not match its files."""


@dataclass(frozen=True)
class LoadedTask:
    """A validated task together with the directory it was loaded from."""

    task: Task
    root: Path

    def _repository_dir(self) -> Path:
        repository = self.task.repository
        if not isinstance(repository, FixtureRepository):
            raise TaskError(
                f"task {self.task.task_id}: fetching git repositories arrives with Milestone 4"
            )
        return self.root / repository.path

    def agent_files(self) -> dict[str, bytes]:
        """Return the starting repository state, the only files an agent may see."""
        return _collect(self._repository_dir(), self.task.task_id)

    def hidden_test_files(self) -> dict[str, bytes]:
        return _collect(self.root / self.task.evaluation.hidden_tests_dir, self.task.task_id)

    def reference_fix_files(self) -> dict[str, bytes]:
        return _collect(self.root / self.task.evaluation.reference_fix_dir, self.task.task_id)

    def evaluation_files(self, candidate: Mapping[str, bytes]) -> dict[str, bytes]:
        """Return ``candidate`` with the hidden tests added, for the evaluator only.

        Hidden tests are always new files. A candidate that already has a file
        at one of their paths is rejected, so a patch can never replace them.
        """
        try:
            return overlay(candidate, self.hidden_test_files(), allow_overwrite=False)
        except TreeError as exc:
            raise TaskError(f"task {self.task.task_id}: {exc}") from exc

    def reference_solution_files(self) -> dict[str, bytes]:
        """Return the repository with the reference fix applied, for the evaluator only."""
        return overlay(self.agent_files(), self.reference_fix_files(), allow_overwrite=True)


def _collect(directory: Path, task_id: str) -> dict[str, bytes]:
    try:
        return collect_tree(directory)
    except TreeError as exc:
        raise TaskError(f"task {task_id}: {exc}") from exc


def _check_files(loaded: LoadedTask) -> None:
    task = loaded.task
    repository = loaded.agent_files()
    if not repository:
        raise TaskError(f"task {task.task_id}: the repository is empty")
    if any(path == INTERNAL_DIR or path.startswith(INTERNAL_DIR + "/") for path in repository):
        raise TaskError(f"task {task.task_id}: the repository contains an '{INTERNAL_DIR}' folder")

    assert isinstance(task.repository, FixtureRepository)  # noqa: S101 - narrowed by agent_files
    actual = tree_digest(repository)
    if actual != task.repository.tree_sha256:
        raise TaskError(
            f"task {task.task_id}: repository content does not match its pin "
            f"(expected {task.repository.tree_sha256}, found {actual})"
        )

    hidden = loaded.hidden_test_files()
    if not hidden:
        raise TaskError(f"task {task.task_id}: there are no hidden tests")
    clash = sorted(set(hidden) & set(repository))
    if clash:
        raise TaskError(f"task {task.task_id}: hidden tests would overwrite {clash}")

    fix = loaded.reference_fix_files()
    if not fix:
        raise TaskError(f"task {task.task_id}: the reference fix is empty")
    added = sorted(set(fix) - set(repository))
    if added:
        raise TaskError(f"task {task.task_id}: the reference fix adds files {added}")
    if all(fix[path] == repository[path] for path in fix):
        raise TaskError(f"task {task.task_id}: the reference fix changes nothing")
    if set(fix) & set(hidden):
        raise TaskError(f"task {task.task_id}: the reference fix overlaps the hidden tests")

    hidden_modules = {path for path in hidden if path.endswith(".py")}
    for node_id in task.evaluation.fail_to_pass:
        if node_id.split("::", 1)[0] not in hidden_modules:
            raise TaskError(
                f"task {task.task_id}: fail-to-pass test {node_id} is not in the hidden tests"
            )
    known = set(repository) | hidden_modules
    for node_id in task.evaluation.pass_to_pass:
        if node_id.split("::", 1)[0] not in known:
            raise TaskError(f"task {task.task_id}: pass-to-pass test {node_id} does not exist")


def load_task(task_dir: Path) -> LoadedTask:
    """Load and fully validate the task stored in ``task_dir``."""
    task_file = task_dir / TASK_FILE
    if task_file.is_symlink() or not task_file.is_file():
        raise TaskError(f"{task_file} is missing")
    raw = task_file.read_bytes()
    if len(raw) > _MAX_TASK_FILE_BYTES:
        raise TaskError(f"{task_file} is too large")
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise TaskError(f"{task_file} is not valid TOML: {exc}") from exc
    try:
        task = Task.model_validate(data)
    except ValidationError as exc:
        raise TaskError(f"{task_file} is not a valid task: {exc}") from exc
    if task.task_id != task_dir.name:
        raise TaskError(
            f"task_id {task.task_id!r} does not match its directory name {task_dir.name!r}"
        )
    loaded = LoadedTask(task=task, root=task_dir)
    _check_files(loaded)
    return loaded


def discover_tasks(tasks_root: Path) -> list[LoadedTask]:
    """Load every task directory under ``tasks_root``, sorted by task ID."""
    if not tasks_root.is_dir():
        raise TaskError(f"{tasks_root} is not a directory")
    directories = sorted(path for path in tasks_root.iterdir() if (path / TASK_FILE).is_file())
    return [load_task(directory) for directory in directories]
