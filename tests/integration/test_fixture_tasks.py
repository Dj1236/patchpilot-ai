"""End-to-end checks of the committed evaluation tasks in a real sandbox."""

from pathlib import Path

import pytest

from patchpilot.sandbox import DockerSandbox, SandboxStatus
from patchpilot.tasks import grade_candidate, load_task, verify_fixture

pytestmark = pytest.mark.sandbox

TASKS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "tasks"
TASK_IDS = ["inj-001-cachekit", "inj-002-exprcalc"]

# A plausible but wrong patch for each task: it makes the reported example
# work without fixing the underlying behavior.
WRONG_FIXES = {
    "inj-001-cachekit": (
        "src/cachekit/lru.py",
        "        if key in self._entries:\n"
        "            self._entries[key] = (value, expires_at)\n"
        "            return\n",
        "        if key in self._entries:\n"
        "            self._entries[key] = (value, expires_at)\n"
        "            self._entries.move_to_end(key, last=False)\n"
        "            return\n",
    ),
    "inj-002-exprcalc": (
        "src/exprcalc/parser.py",
        '        if self._accept("-"):\n            return left - self._expression()\n',
        '        if self._accept("-"):\n            return left - self._term()\n',
    ),
}


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_fixture_behaves_as_its_definition_says(sandbox: DockerSandbox, task_id: str) -> None:
    report = verify_fixture(load_task(TASKS_ROOT / task_id), sandbox)

    failed = [f"{check.name}: {check.detail}" for check in report.checks if not check.ok]
    assert report.ok, failed
    assert len(report.checks) == 5


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_a_wrong_patch_is_not_graded_as_resolved(sandbox: DockerSandbox, task_id: str) -> None:
    loaded = load_task(TASKS_ROOT / task_id)
    path, old, new = WRONG_FIXES[task_id]
    candidate = loaded.agent_files()
    source = candidate[path].decode()
    assert source.count(old) == 1
    candidate[path] = source.replace(old, new).encode()

    outcome, result = grade_candidate(loaded, candidate, sandbox)

    assert result.status is SandboxStatus.FAILED
    assert outcome.trustworthy
    assert not outcome.resolved
    assert outcome.still_failing or outcome.regressions


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_a_patch_that_deletes_a_visible_test_is_not_resolved(
    sandbox: DockerSandbox, task_id: str
) -> None:
    loaded = load_task(TASKS_ROOT / task_id)
    candidate = loaded.reference_solution_files()
    removed = next(path for path in candidate if path.startswith("tests/test_"))
    del candidate[removed]

    outcome, result = grade_candidate(loaded, candidate, sandbox)

    assert outcome.tampered == (removed,)
    assert not outcome.resolved
    # The deleted file was restored from the pinned state, so its tests still ran.
    assert outcome.missing == ()
    assert any(node_id.startswith(removed) for node_id in result.outcomes())


@pytest.mark.parametrize("task_id", TASK_IDS)
def test_agent_visible_run_contains_no_hidden_test(sandbox: DockerSandbox, task_id: str) -> None:
    loaded = load_task(TASKS_ROOT / task_id)
    task = loaded.task

    result = sandbox.run(
        loaded.agent_files(),
        task.commands[0],
        task.budgets,
        source_roots=task.environment.source_roots,
    )

    assert result.status is SandboxStatus.PASSED
    assert not any("hidden" in node_id for node_id in result.outcomes())
    assert "hidden" not in result.stdout
