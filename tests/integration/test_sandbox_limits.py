"""Resource-limit, timeout and cleanup tests against a real container.

Every exhaustion attempt here is bounded twice: by the container's own limits
(256 MB of memory, 64 processes, no swap) and by a fixed loop count inside the
probe. Nothing is exhausted on the host.
"""

import os
import subprocess
import time

import pytest

from patchpilot.sandbox import DockerSandbox, PytestCommand, SandboxLimits, SandboxStatus
from patchpilot.sandbox.docker import CONTAINER_LABEL, docker_client_env, image_ref
from tests.integration.helpers import PROBE, SMALL, assert_probe_passed, run_probe

pytestmark = pytest.mark.sandbox

MEMORY_HOG = """
def test_allocate_far_more_than_the_limit():
    blocks = []
    for _ in range(64):  # at most 1 GB requested; the limit is 256 MB
        blocks.append(b"x" * (16 * 1024 * 1024))
"""

PROCESS_PROBE = """
import subprocess


def test_process_count_is_capped():
    children = []
    refused = False
    try:
        for _ in range(200):
            children.append(subprocess.Popen(["sleep", "30"]))
    except OSError:
        refused = True
    finally:
        for child in children:
            child.kill()
            child.wait()
    assert refused
    assert len(children) < 64
"""

ORPHAN_PROBE = """
import subprocess


def test_leave_background_processes_running():
    for _ in range(20):
        subprocess.Popen(["sleep", "600"], start_new_session=True)
"""

STUBBORN_SLEEPER = """
import signal
import time


def test_never_finishes():
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, signal.SIG_IGN)
    time.sleep(600)
"""

OUTPUT_FLOOD = """
import sys


def test_print_twenty_megabytes(capfd):
    with capfd.disabled():
        line = "x" * 1023 + "\\n"
        for _ in range(20 * 1024):
            sys.stdout.write(line)
"""


def test_memory_exhaustion_is_contained_and_reported(sandbox: DockerSandbox) -> None:
    result = run_probe(sandbox, MEMORY_HOG)

    assert result.status is SandboxStatus.OUT_OF_MEMORY
    assert result.oom_kill_count is not None and result.oom_kill_count >= 1
    assert result.exit_code == 137
    assert not result.is_infrastructure_error


def test_process_exhaustion_is_contained_and_reported(sandbox: DockerSandbox) -> None:
    result = run_probe(sandbox, PROCESS_PROBE)

    assert_probe_passed(result, expected_tests=1)
    assert result.pids_limit_hit is True


def test_background_processes_die_with_the_container(sandbox: DockerSandbox) -> None:
    result = run_probe(sandbox, ORPHAN_PROBE)

    assert_probe_passed(result, expected_tests=1)
    assert sandbox.list_containers() == []


def test_timeout_stops_a_test_that_ignores_signals(sandbox: DockerSandbox) -> None:
    limits = SMALL.model_copy(update={"timeout_seconds": 3})
    started = time.monotonic()

    result = run_probe(sandbox, STUBBORN_SLEEPER, limits)

    assert result.status is SandboxStatus.TIMEOUT
    assert result.exit_code is None
    assert result.counts is None
    assert time.monotonic() - started < 30
    assert sandbox.list_containers() == []


def test_output_flood_is_truncated_on_the_host(sandbox: DockerSandbox) -> None:
    result = run_probe(sandbox, OUTPUT_FLOOD)

    assert result.status is SandboxStatus.PASSED
    assert result.stdout_truncated
    assert len(result.stdout.encode()) <= SMALL.max_output_bytes


def test_a_workspace_larger_than_its_limit_is_an_infrastructure_error(
    sandbox: DockerSandbox,
) -> None:
    limits = SMALL.model_copy(update={"workspace_mb": 1})
    files = {f"data/blob{index}.bin": b"\0" * 900_000 for index in range(3)}
    files["tests/test_ok.py"] = b"def test_ok():\n    pass\n"

    result = sandbox.run(files, PROBE, limits)

    assert result.status is SandboxStatus.INFRASTRUCTURE_ERROR
    assert result.error is not None and "loading the workspace" in result.error
    assert result.counts is None
    assert sandbox.list_containers() == []


def test_containers_from_a_crashed_run_can_be_swept(sandbox: DockerSandbox) -> None:
    name = "patchpilot-leftover-test"
    argv = ["docker", "run", "--detach", "--name", name, "--label", f"{CONTAINER_LABEL}=1"]
    argv += ["--network", "none", image_ref(), "sleep", "300"]
    subprocess.run(  # noqa: S603
        argv, check=True, capture_output=True, env=docker_client_env(os.environ)
    )

    assert sandbox.list_containers() == [name]
    assert sandbox.remove_leftovers() == [name]
    assert sandbox.list_containers() == []


def test_a_failing_test_is_a_test_failure_not_an_infrastructure_error(
    sandbox: DockerSandbox,
) -> None:
    result = run_probe(sandbox, "def test_bad():\n    assert 1 == 2\n")

    assert result.status is SandboxStatus.FAILED
    assert result.exit_code == 1
    assert result.counts is not None and result.counts.failed == 1
    assert result.outcomes() == {"tests/test_probe.py::test_bad": "failed"}
    assert result.junit_xml is not None and "<testcase" in result.junit_xml
    assert result.error is None


def test_a_broken_test_module_is_a_collection_error(sandbox: DockerSandbox) -> None:
    result = run_probe(sandbox, "def broken(:\n")

    assert result.status is SandboxStatus.COLLECTION_ERROR
    assert result.exit_code == 2


def test_a_missing_target_is_a_runner_error(sandbox: DockerSandbox) -> None:
    command = PytestCommand(name="missing", targets=("tests/test_absent.py",))

    result = sandbox.run({"tests/test_ok.py": b"def test_ok():\n    pass\n"}, command, SMALL)

    assert result.status is SandboxStatus.RUNNER_ERROR
    assert result.exit_code == 4


def test_no_collected_tests_is_reported_as_such(sandbox: DockerSandbox) -> None:
    result = run_probe(sandbox, "VALUE = 1\n")

    assert result.status is SandboxStatus.NO_TESTS_COLLECTED


def test_source_roots_are_importable_and_metadata_is_recorded(sandbox: DockerSandbox) -> None:
    files = {
        "src/shapes/__init__.py": b"def area(w, h):\n    return w * h\n",
        "tests/test_shapes.py": b"from shapes import area\n\n\ndef test_area():\n"
        b"    assert area(2, 3) == 6\n",
    }

    result = sandbox.run(files, PROBE, SMALL, source_roots=["src"])

    assert result.status is SandboxStatus.PASSED
    assert result.duration_seconds > 0
    metadata = result.metadata
    assert metadata is not None
    assert metadata.image_ref == image_ref()
    assert metadata.image_id.startswith("sha256:")
    assert metadata.engine_platform.startswith("linux/")
    assert metadata.source_roots == ("src",)
    assert len(metadata.source_tree_sha256) == 64
    assert metadata.limits == SMALL
    assert "/opt/patchpilot/run_tests.py" in result.container_argv
    assert result.report_is_consistent
    assert result.cleanup_failed is False


def test_two_runs_of_the_same_tree_agree(sandbox: DockerSandbox) -> None:
    code = "def test_a():\n    assert True\n\n\ndef test_b():\n    assert False\n"

    first = run_probe(sandbox, code)
    second = run_probe(sandbox, code)

    assert first.outcomes() == second.outcomes()
    assert first.status is second.status is SandboxStatus.FAILED
    assert first.metadata is not None and second.metadata is not None
    assert first.metadata.source_tree_sha256 == second.metadata.source_tree_sha256
    assert first.metadata.image_id == second.metadata.image_id


def test_default_limits_are_at_least_as_generous_as_the_test_limits() -> None:
    assert SandboxLimits().memory_mb >= SMALL.memory_mb
