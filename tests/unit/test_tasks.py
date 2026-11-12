import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from patchpilot.sandbox.models import (
    CaseOutcome,
    CaseResult,
    PytestCommand,
    SandboxResult,
    SandboxStatus,
)
from patchpilot.sandbox.tree import collect_tree, tree_digest
from patchpilot.tasks import Task, TaskError, discover_tasks, grade, load_task
from patchpilot.tasks.schema import Evaluation

TASKS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "tasks"
TASK_IDS = ["inj-001-cachekit", "inj-002-exprcalc"]


def _definition() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "task_id": "demo-task",
        "kind": "injected",
        "repository": {"kind": "fixture", "path": "repo", "tree_sha256": "a" * 64},
        "issue": {
            "title": "Something is wrong",
            "body": "Details.",
            "expected_behavior": "It works.",
            "actual_behavior": "It does not.",
        },
        "commands": [{"name": "tests", "targets": ["tests"]}],
        "evaluation": {
            "hidden_tests_dir": "internal/hidden_tests",
            "reference_fix_dir": "internal/reference_fix",
            "command": {"name": "hidden", "targets": ["tests"]},
            "fail_to_pass": ["tests/test_hidden.py::test_bug"],
            "pass_to_pass": ["tests/test_mod.py::test_ok"],
        },
    }


def _with(path: str, value: Any) -> dict[str, Any]:
    data = _definition()
    target = data
    *parents, last = path.split(".")
    for key in parents:
        target = target[key]
    target[last] = value
    return data


# -- schema ---------------------------------------------------------------


def test_a_complete_definition_is_valid() -> None:
    task = Task.model_validate(_definition())

    assert task.task_id == "demo-task"
    assert task.budgets.timeout_seconds == 60


def test_a_git_repository_needs_a_full_commit_sha() -> None:
    good = {"kind": "git", "url": "https://github.com/owner/project", "commit_sha": "0" * 40}
    assert Task.model_validate(_with("repository", good)).repository.kind == "git"

    for bad_sha in ("main", "abc1234", "0" * 39, "G" * 40):
        with pytest.raises(ValidationError):
            Task.model_validate(_with("repository", {**good, "commit_sha": bad_sha}))


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/owner/project",
        "https://evil.example/owner/project",
        "https://github.com/owner/project/../other",
        "git@github.com:owner/project.git",
    ],
)
def test_only_plain_github_urls_are_accepted(url: str) -> None:
    repository = {"kind": "git", "url": url, "commit_sha": "0" * 40}

    with pytest.raises(ValidationError):
        Task.model_validate(_with("repository", repository))


@pytest.mark.parametrize(
    "path,value",
    [
        ("task_id", "Bad ID"),
        ("task_id", "../escape"),
        ("schema_version", 2),
        ("kind", "synthetic"),
        ("repository.path", "../repo"),
        ("repository.path", "/abs/repo"),
        ("repository.path", "internal/repo"),
        ("repository.tree_sha256", "short"),
        ("issue.title", ""),
        ("commands", []),
        ("commands", [{"name": "tests", "targets": ["--collect-only"]}]),
        ("commands", [{"name": "a", "targets": ["tests"]}, {"name": "a", "targets": ["tests"]}]),
        ("evaluation.hidden_tests_dir", "repo/tests"),
        ("evaluation.hidden_tests_dir", "internal"),
        ("evaluation.reference_fix_dir", "internal/hidden_tests"),
        ("evaluation.fail_to_pass", []),
        ("evaluation.fail_to_pass", ["tests"]),
        ("evaluation.pass_to_pass", ["tests/test_hidden.py::test_bug"]),
        ("budgets", {"timeout_seconds": 100000}),
    ],
)
def test_invalid_definitions_are_rejected(path: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        Task.model_validate(_with(path, value))


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Task.model_validate({**_definition(), "shell_command": "make test"})


def test_agent_view_has_no_place_for_evaluation_data() -> None:
    task = Task.model_validate(_definition())

    view = task.agent_view()
    serialized = json.dumps(view.model_dump(mode="json"))

    assert "evaluation" not in type(view).model_fields
    for secret in ("hidden", "reference_fix", "fail_to_pass", "pass_to_pass", "test_bug"):
        assert secret not in serialized
    assert view.issue == task.issue
    assert view.commands == task.commands


# -- committed fixtures ----------------------------------------------------


def test_both_fixtures_are_discovered() -> None:
    assert [loaded.task.task_id for loaded in discover_tasks(TASKS_ROOT)] == TASK_IDS


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_fixture_matches_its_pin_and_keeps_internal_data_apart(task_id: str) -> None:
    loaded = load_task(TASKS_ROOT / task_id)
    agent_files = loaded.agent_files()

    assert loaded.task.repository.kind == "fixture"
    assert tree_digest(agent_files) == loaded.task.repository.tree_sha256
    assert not any("hidden" in path or "internal" in path for path in agent_files)
    for hidden_path, content in loaded.hidden_test_files().items():
        assert hidden_path not in agent_files
        assert content not in agent_files.values()
    agent_text = b"\n".join(agent_files.values())
    for node_id in loaded.task.evaluation.fail_to_pass:
        assert node_id.split("::")[1].encode() not in agent_text


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_reference_fix_changes_source_but_never_tests(task_id: str) -> None:
    loaded = load_task(TASKS_ROOT / task_id)
    starting = loaded.agent_files()
    fixed = loaded.reference_solution_files()

    changed = sorted(path for path in fixed if fixed[path] != starting.get(path))
    assert changed
    assert all(path.startswith("src/") for path in changed)
    assert set(fixed) == set(starting)


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_a_candidate_cannot_replace_the_hidden_tests(task_id: str) -> None:
    loaded = load_task(TASKS_ROOT / task_id)
    hidden_path = next(iter(loaded.hidden_test_files()))
    candidate = {**loaded.agent_files(), hidden_path: b"def test_bug():\n    pass\n"}

    with pytest.raises(TaskError, match="overwrite"):
        loaded.evaluation_files(candidate)


# -- loader checks on a tampered copy ---------------------------------------


@pytest.fixture
def task_copy(tmp_path: Path) -> Path:
    destination = tmp_path / "inj-001-cachekit"
    shutil.copytree(TASKS_ROOT / "inj-001-cachekit", destination)
    return destination


def test_a_changed_repository_no_longer_matches_its_pin(task_copy: Path) -> None:
    (task_copy / "repo" / "src" / "cachekit" / "lru.py").write_text("# tampered\n")

    with pytest.raises(TaskError, match="does not match its pin"):
        load_task(task_copy)


def test_directory_name_must_match_the_task_id(task_copy: Path) -> None:
    renamed = task_copy.with_name("other-name")
    task_copy.rename(renamed)

    with pytest.raises(TaskError, match="does not match its directory"):
        load_task(renamed)


def test_missing_hidden_tests_are_rejected(task_copy: Path) -> None:
    shutil.rmtree(task_copy / "internal" / "hidden_tests" / "tests")

    with pytest.raises(TaskError, match="no hidden tests"):
        load_task(task_copy)


def test_a_reference_fix_that_adds_files_is_rejected(task_copy: Path) -> None:
    (task_copy / "internal" / "reference_fix" / "src" / "cachekit" / "extra.py").write_text("")

    with pytest.raises(TaskError, match="adds files"):
        load_task(task_copy)


def test_a_reference_fix_that_changes_nothing_is_rejected(task_copy: Path) -> None:
    shutil.copy(
        task_copy / "repo" / "src" / "cachekit" / "lru.py",
        task_copy / "internal" / "reference_fix" / "src" / "cachekit" / "lru.py",
    )

    with pytest.raises(TaskError, match="changes nothing"):
        load_task(task_copy)


def test_a_symlink_inside_the_repository_is_rejected(task_copy: Path) -> None:
    (task_copy / "repo" / "leak.py").symlink_to(task_copy / "internal" / "hidden_tests")

    with pytest.raises(TaskError, match="symbolic link"):
        load_task(task_copy)


def test_hidden_tests_placed_inside_the_repository_are_rejected(task_copy: Path) -> None:
    hidden = task_copy / "internal" / "hidden_tests" / "tests" / "test_hidden_recency.py"
    shutil.copy(hidden, task_copy / "repo" / "tests" / "test_hidden_recency.py")
    task_file = task_copy / "task.toml"
    pinned = tree_digest(collect_tree(task_copy / "repo"))
    original = load_task(TASKS_ROOT / "inj-001-cachekit").task.repository
    assert original.kind == "fixture"
    task_file.write_text(task_file.read_text().replace(original.tree_sha256, pinned))

    with pytest.raises(TaskError, match="overwrite"):
        load_task(task_copy)


def test_invalid_toml_is_reported_as_a_task_error(task_copy: Path) -> None:
    (task_copy / "task.toml").write_text("task_id = [")

    with pytest.raises(TaskError, match="not valid TOML"):
        load_task(task_copy)


# -- grading ---------------------------------------------------------------

EVALUATION = Evaluation.model_validate(_definition()["evaluation"])
BUG = "tests/test_hidden.py::test_bug"
OK = "tests/test_mod.py::test_ok"


def _result(status: SandboxStatus, outcomes: dict[str, CaseOutcome]) -> SandboxResult:
    cases = tuple(
        CaseResult(
            node_id=node_id,
            classname="",
            name=node_id,
            outcome=outcome,
            duration_seconds=0.0,
        )
        for node_id, outcome in outcomes.items()
    )
    return SandboxResult(
        status=status,
        command=PytestCommand(name="hidden", targets=("tests",)),
        container_argv=(),
        exit_code=0,
        counts=None,
        cases=cases,
        started_at=datetime.now(UTC),
        duration_seconds=0.0,
    )


def test_resolved_needs_every_listed_test_to_pass() -> None:
    result = _result(SandboxStatus.PASSED, {BUG: CaseOutcome.PASSED, OK: CaseOutcome.PASSED})

    assert grade(result, EVALUATION).resolved


def test_an_unfixed_bug_is_not_resolved() -> None:
    result = _result(SandboxStatus.FAILED, {BUG: CaseOutcome.FAILED, OK: CaseOutcome.PASSED})

    outcome = grade(result, EVALUATION)

    assert not outcome.resolved
    assert outcome.trustworthy
    assert outcome.still_failing == (BUG,)
    assert outcome.regressions == ()


def test_a_fix_that_breaks_existing_behavior_is_a_regression() -> None:
    result = _result(SandboxStatus.FAILED, {BUG: CaseOutcome.PASSED, OK: CaseOutcome.FAILED})

    outcome = grade(result, EVALUATION)

    assert not outcome.resolved
    assert outcome.regressions == (OK,)


def test_deleting_or_skipping_a_test_does_not_count_as_passing() -> None:
    missing = grade(_result(SandboxStatus.PASSED, {BUG: CaseOutcome.PASSED}), EVALUATION)
    skipped = grade(
        _result(SandboxStatus.PASSED, {BUG: CaseOutcome.SKIPPED, OK: CaseOutcome.PASSED}),
        EVALUATION,
    )

    assert not missing.resolved and missing.missing == (OK,)
    assert not skipped.resolved and skipped.still_failing == (BUG,)


@pytest.mark.parametrize(
    "status",
    [
        SandboxStatus.TIMEOUT,
        SandboxStatus.OUT_OF_MEMORY,
        SandboxStatus.INFRASTRUCTURE_ERROR,
        SandboxStatus.COLLECTION_ERROR,
    ],
)
def test_an_unusable_run_is_never_resolved_and_is_marked_untrustworthy(
    status: SandboxStatus,
) -> None:
    result = _result(status, {BUG: CaseOutcome.PASSED, OK: CaseOutcome.PASSED})

    outcome = grade(result, EVALUATION)

    assert not outcome.resolved
    assert not outcome.trustworthy
