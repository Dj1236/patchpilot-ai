"""Attempts to manipulate grading, run against the real sandbox.

Each test hands the evaluator a candidate that leaves the bug in place and
tries to be graded as resolved anyway. All of them must fail to do so, except
the one marked as a known limitation at the end.
"""

from pathlib import Path

import pytest

from patchpilot.sandbox import DockerSandbox, SandboxStatus
from patchpilot.tasks import LoadedTask, grade_candidate, load_task
from tests.integration.helpers import PROBE, SMALL

pytestmark = pytest.mark.sandbox

TASKS_ROOT = Path(__file__).resolve().parents[2] / "evals" / "tasks"
PACKAGE_INIT = "src/cachekit/__init__.py"

FORCE_PASS_CONFTEST = b"""
import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    report.outcome = "passed"
    report.longrepr = None
"""

SHADOW_RUNNER = b"""
import sys

print("shadow runner ran")
sys.exit(0)
"""

# Rewrites the report at interpreter exit and forces a zero exit status.
EXIT_HANDLER_FORGERY = b"""

import atexit
import os
import re


def _forge():
    path = "/tmp/.patchpilot/junit.xml"
    with open(path) as handle:
        text = handle.read()
    text = re.sub(r"<failure.*?</failure>", "", text, flags=re.S)
    text = re.sub(r'failures="\\d+"', 'failures="0"', text)
    with open(path, "w") as handle:
        handle.write(text)
    os._exit(0)


atexit.register(_forge)
"""

# Ends the process with "success" before any test can run.
EARLY_EXIT = b"\n\nimport os\n\nos._exit(0)\n"

# Patches pytest inside the test process so that every test reports success.
IN_PROCESS_FORGERY = b"""

from _pytest import reports as _reports

_original = _reports.TestReport.from_item_and_call.__func__


def _forged(cls, item, call):
    report = _original(cls, item, call)
    report.outcome = "passed"
    report.longrepr = None
    return report


_reports.TestReport.from_item_and_call = classmethod(_forged)
"""


@pytest.fixture
def task() -> LoadedTask:
    return load_task(TASKS_ROOT / "inj-001-cachekit")


def test_the_unmodified_buggy_code_is_the_baseline(
    sandbox: DockerSandbox, task: LoadedTask
) -> None:
    outcome, result = grade_candidate(task, task.agent_files(), sandbox)

    assert result.status is SandboxStatus.FAILED
    assert outcome.trustworthy and not outcome.resolved
    assert len(outcome.still_failing) == 3


@pytest.mark.parametrize("path", ["conftest.py", "tests/conftest.py", "src/conftest.py"])
def test_a_conftest_that_forces_passes_is_dropped_and_flagged(
    sandbox: DockerSandbox, task: LoadedTask, path: str
) -> None:
    candidate = {**task.agent_files(), path: FORCE_PASS_CONFTEST}

    outcome, result = grade_candidate(task, candidate, sandbox)

    assert outcome.tampered == (path,)
    assert not outcome.resolved
    assert result.status is SandboxStatus.FAILED
    assert len(outcome.still_failing) == 3


@pytest.mark.parametrize(
    "path,content",
    [
        ("pytest.ini", b"[pytest]\naddopts = --deselect tests/test_hidden_recency.py\n"),
        ("tox.ini", b"[pytest]\naddopts = -k test_stores\n"),
        ("setup.cfg", b"[tool:pytest]\naddopts = -k test_stores\n"),
        ("pyproject.toml", b'[tool.pytest.ini_options]\naddopts = "-k test_stores"\n'),
    ],
)
def test_runner_configuration_in_a_candidate_is_dropped_and_flagged(
    sandbox: DockerSandbox, task: LoadedTask, path: str, content: bytes
) -> None:
    outcome, _ = grade_candidate(task, {**task.agent_files(), path: content}, sandbox)

    assert outcome.tampered == (path,)
    assert not outcome.resolved
    assert outcome.missing == ()
    assert len(outcome.still_failing) == 3


@pytest.mark.parametrize(
    "path,content",
    [
        ("pytest.ini", b"[pytest]\naddopts = -k nothing_matches_this\n"),
        ("pyproject.toml", b'[tool.pytest.ini_options]\naddopts = "-k nothing_matches_this"\n'),
        ("tox.ini", b"[pytest]\naddopts = -p no:junitxml\n"),
    ],
)
def test_the_runner_ignores_configuration_files_in_any_workspace(
    sandbox: DockerSandbox, path: str, content: bytes
) -> None:
    files = {"tests/test_probe.py": b"def test_bad():\n    assert 1 == 2\n", path: content}

    result = sandbox.run(files, PROBE, SMALL)

    assert result.status is SandboxStatus.FAILED
    assert result.report_is_consistent
    assert result.outcomes() == {"tests/test_probe.py::test_bad": "failed"}


@pytest.mark.parametrize("shadow", ["src/pytest.py", "pytest.py", "src/_pytest/__init__.py"])
def test_a_module_named_like_the_runner_cannot_replace_it(
    sandbox: DockerSandbox, task: LoadedTask, shadow: str
) -> None:
    outcome, result = grade_candidate(task, {**task.agent_files(), shadow: SHADOW_RUNNER}, sandbox)

    assert "shadow runner ran" not in result.stdout
    assert result.status is SandboxStatus.FAILED
    assert not outcome.resolved
    assert len(outcome.still_failing) == 3


def test_weakening_an_existing_test_is_undone_and_flagged(
    sandbox: DockerSandbox, task: LoadedTask
) -> None:
    candidate = task.reference_solution_files()
    candidate["tests/test_lru.py"] = b"def test_stores_and_returns_values():\n    pass\n"

    outcome, result = grade_candidate(task, candidate, sandbox)

    assert outcome.tampered == ("tests/test_lru.py",)
    assert not outcome.resolved
    # The pinned tests ran instead of the weakened file, and all of them pass
    # because the source fix itself is correct.
    assert result.status is SandboxStatus.PASSED
    assert outcome.missing == () and outcome.regressions == ()


def test_a_regression_test_added_by_the_candidate_is_left_out_of_grading(
    sandbox: DockerSandbox, task: LoadedTask
) -> None:
    candidate = task.reference_solution_files()
    candidate["tests/test_agent_regression.py"] = b"def test_agent():\n    assert False\n"

    outcome, result = grade_candidate(task, candidate, sandbox)

    assert outcome.ignored == ("tests/test_agent_regression.py",)
    assert outcome.tampered == ()
    assert outcome.resolved
    assert "tests/test_agent_regression.py::test_agent" not in result.outcomes()


def test_an_exit_handler_cannot_rewrite_the_report(
    sandbox: DockerSandbox, task: LoadedTask
) -> None:
    candidate = task.agent_files()
    candidate[PACKAGE_INIT] += EXIT_HANDLER_FORGERY

    outcome, result = grade_candidate(task, candidate, sandbox)

    assert result.status is SandboxStatus.FAILED
    assert result.exit_code == 1
    assert not outcome.resolved
    assert len(outcome.still_failing) == 3


def test_exiting_with_success_before_the_tests_run_is_untrustworthy(
    sandbox: DockerSandbox, task: LoadedTask
) -> None:
    candidate = task.agent_files()
    candidate[PACKAGE_INIT] += EARLY_EXIT

    outcome, result = grade_candidate(task, candidate, sandbox)

    assert result.exit_code == 0
    assert result.report_issues == ("no JUnit report was produced",)
    assert not outcome.trustworthy
    assert not outcome.resolved


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known V1 limitation: code under test runs in the same process as pytest "
        "and can forge the report and the exit status together. See docs/sandbox.md."
    ),
)
def test_in_process_forgery_of_report_and_exit_status_is_detected(
    sandbox: DockerSandbox, task: LoadedTask
) -> None:
    candidate = task.agent_files()
    candidate[PACKAGE_INIT] += IN_PROCESS_FORGERY

    outcome, _ = grade_candidate(task, candidate, sandbox)

    assert not outcome.resolved
