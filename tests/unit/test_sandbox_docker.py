"""Unit tests for the Docker runner that need no Docker engine.

These check how commands are assembled and how results are classified. That
the restrictions actually hold is verified by the integration tests, which run
real probes inside a container.
"""

import itertools
from collections.abc import Callable, Mapping, Sequence

import pytest

from patchpilot.sandbox.docker import (
    TMP_DIR,
    WORKSPACE_DIR,
    DockerSandbox,
    build_exec_argv,
    build_pytest_argv,
    build_run_argv,
    classify,
    docker_client_env,
    image_definition,
    image_ref,
    parse_limit_events,
)
from patchpilot.sandbox.models import PytestCommand, SandboxLimits, SandboxStatus
from patchpilot.sandbox.process import ProcessResult
from patchpilot.sandbox.tree import TreeError

COMMAND = PytestCommand(name="tests", targets=("tests",))
FILES = {"tests/test_a.py": b"def test_a():\n    pass\n"}


def _pairs(argv: Sequence[str]) -> set[tuple[str, str]]:
    return set(itertools.pairwise(argv))


def test_run_command_applies_every_restriction() -> None:
    limits = SandboxLimits(memory_mb=256, cpus=0.5, pids=64, workspace_mb=32, tmp_mb=16)

    argv = build_run_argv("box", "image:tag", limits)
    pairs = _pairs(argv)

    assert ("--network", "none") in pairs
    assert ("--user", "10001:10001") in pairs
    assert ("--cap-drop", "ALL") in pairs
    assert ("--security-opt", "no-new-privileges:true") in pairs
    assert "--read-only" in argv
    assert ("--memory", "256m") in pairs
    assert ("--memory-swap", "256m") in pairs
    assert ("--cpus", "0.5") in pairs
    assert ("--pids-limit", "64") in pairs
    assert ("--pull", "never") in pairs
    assert ("--shm-size", "8m") in pairs
    assert ("--ipc", "private") in pairs
    scratch = {b.split(":")[0]: b for a, b in pairs if a == "--tmpfs"}
    assert sorted(scratch) == [TMP_DIR, WORKSPACE_DIR]
    assert "size=32m" in scratch[WORKSPACE_DIR]
    assert "size=16m" in scratch[TMP_DIR]
    assert all("noexec" in options and "nosuid" in options for options in scratch.values())


def test_run_command_never_mounts_or_forwards_anything_from_the_host() -> None:
    argv = build_run_argv("box", "image:tag", SandboxLimits())

    forbidden = {"--volume", "-v", "--mount", "--env", "-e", "--env-file", "--privileged"}
    assert forbidden.isdisjoint(argv)
    assert "--cap-add" not in argv
    assert not any("docker.sock" in part for part in argv)


def test_the_container_only_sleeps_and_ends_by_itself() -> None:
    argv = build_run_argv("box", "image:tag", SandboxLimits(timeout_seconds=30))

    assert argv[-3:] == ["image:tag", "sleep", "150"]


def test_pytest_command_line_is_fixed_apart_from_targets() -> None:
    command = PytestCommand(name="one", targets=("tests/test_a.py::test_a",))

    assert build_pytest_argv(command) == [
        "python",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit2",
        "--junitxml=/tmp/.patchpilot/junit.xml",
        "--color=no",
        "-q",
        "tests/test_a.py::test_a",
    ]


def test_source_roots_become_a_workspace_pythonpath() -> None:
    argv = build_exec_argv("box", COMMAND, ["src", "lib/py"])

    assert ("--env", "PYTHONPATH=/workspace/src:/workspace/lib/py") in _pairs(argv)
    assert ("--user", "10001:10001") in _pairs(argv)


def test_a_source_root_cannot_escape_the_workspace() -> None:
    with pytest.raises(TreeError):
        build_exec_argv("box", COMMAND, ["../outside"])


def test_docker_client_sees_only_allowlisted_variables() -> None:
    environ = {
        "PATH": "/usr/bin",
        "HOME": "/home/me",
        "DOCKER_HOST": "unix:///x.sock",
        "ANTHROPIC_API_KEY": "secret",
        "PATCHPILOT_GITHUB_TOKEN": "secret",
        "AWS_SECRET_ACCESS_KEY": "secret",
    }

    assert docker_client_env(environ) == {
        "PATH": "/usr/bin",
        "HOME": "/home/me",
        "DOCKER_HOST": "unix:///x.sock",
    }


def test_image_definition_is_pinned_and_hash_checked() -> None:
    definition = image_definition()
    dockerfile = definition["Dockerfile"].decode()
    lock = definition["requirements.lock"].decode()

    assert "FROM python@sha256:" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "--only-binary=:all:" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "pytest==" in lock
    assert "--hash=sha256:" in lock
    assert image_ref().startswith("patchpilot-sandbox:")


@pytest.mark.parametrize(
    "exit_code,timed_out,oom,expected",
    [
        (0, False, 0, SandboxStatus.PASSED),
        (1, False, 0, SandboxStatus.FAILED),
        (2, False, 0, SandboxStatus.COLLECTION_ERROR),
        (5, False, 0, SandboxStatus.NO_TESTS_COLLECTED),
        (3, False, 0, SandboxStatus.RUNNER_ERROR),
        (4, False, None, SandboxStatus.RUNNER_ERROR),
        (None, True, None, SandboxStatus.TIMEOUT),
        (137, False, 1, SandboxStatus.OUT_OF_MEMORY),
        (1, False, 2, SandboxStatus.OUT_OF_MEMORY),
        (137, False, 0, SandboxStatus.INFRASTRUCTURE_ERROR),
        (125, False, 0, SandboxStatus.INFRASTRUCTURE_ERROR),
        (126, False, None, SandboxStatus.INFRASTRUCTURE_ERROR),
        (None, False, None, SandboxStatus.INFRASTRUCTURE_ERROR),
    ],
)
def test_classification(
    exit_code: int | None, timed_out: bool, oom: int | None, expected: SandboxStatus
) -> None:
    assert classify(exit_code, timed_out=timed_out, oom_kill_count=oom) is expected


def test_limit_events_are_read_from_both_control_group_files() -> None:
    text = "low 0\nhigh 0\nmax 7\noom 1\noom_kill 2\n---\nmax 3\n"

    events = parse_limit_events(text)

    assert events.oom_kill_count == 2
    assert events.pids_limit_hit is True


def test_missing_limit_events_are_unknown_not_zero() -> None:
    events = parse_limit_events("")

    assert events.oom_kill_count is None
    assert events.pids_limit_hit is None


class FakeDocker:
    """Scripted stand-in for the docker client."""

    def __init__(self, fail_on: str | None = None, raise_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self._fail_on = fail_on
        self._raise_on = raise_on

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        max_output_bytes: int,
        env: Mapping[str, str],
        input_bytes: bytes | None = None,
        on_timeout: Callable[[], None] | None = None,
    ) -> ProcessResult:
        args = list(argv[1:])
        self.calls.append(args)
        self.envs.append(dict(env))
        if self._raise_on is not None and args[0] == self._raise_on:
            raise OSError("docker: command not found")
        exit_code, stdout = 0, b""
        if args[0] == self._fail_on:
            exit_code = 1
        elif args[0] == "version":
            stdout = b"29.0.0|linux/arm64\n"
        elif args[0] == "info":
            stdout = b"Test OS|2\n"
        elif args[:2] == ["image", "inspect"]:
            stdout = b"sha256:abc\n"
        elif args[0] == "exec" and "sh" in args:
            stdout = b"oom_kill 0\n---\nmax 0\n"
        elif args[0] == "exec" and "cat" in args:
            stdout = b'<testsuite><testcase classname="tests.test_a" name="test_a"/></testsuite>'
        return ProcessResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=b"boom" if exit_code else b"",
            stdout_truncated=False,
            stderr_truncated=False,
            timed_out=False,
            duration_seconds=0.0,
        )

    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


def test_a_successful_run_reports_results_and_removes_the_container() -> None:
    fake = FakeDocker()

    result = DockerSandbox(process_runner=fake, environ={"PATH": "/bin"}).run(FILES, COMMAND)

    assert result.status is SandboxStatus.PASSED
    assert result.counts is not None and result.counts.passed == 1
    assert result.metadata is not None and result.metadata.image_id == "sha256:abc"
    assert fake.calls[-1][:3] == ["rm", "--force", "--volumes"]


@pytest.mark.parametrize("failing_step", ["run", "exec"])
def test_the_container_is_removed_when_a_step_fails(failing_step: str) -> None:
    fake = FakeDocker(fail_on=failing_step)

    result = DockerSandbox(process_runner=fake, environ={"PATH": "/bin"}).run(FILES, COMMAND)

    assert result.status is SandboxStatus.INFRASTRUCTURE_ERROR
    assert result.error is not None and "boom" in result.error
    assert result.counts is None
    assert fake.verbs()[-1] == "rm"


def test_a_missing_docker_client_is_an_infrastructure_error() -> None:
    fake = FakeDocker(raise_on="version")
    sandbox = DockerSandbox(process_runner=fake, environ={"PATH": "/bin"})

    assert sandbox.is_available() is False


def test_secrets_in_the_host_environment_never_reach_the_docker_client() -> None:
    fake = FakeDocker()
    environ = {"PATH": "/bin", "ANTHROPIC_API_KEY": "planted-secret"}

    DockerSandbox(process_runner=fake, environ=environ).run(FILES, COMMAND)

    assert all("ANTHROPIC_API_KEY" not in env for env in fake.envs)
    assert not any("planted-secret" in part for call in fake.calls for part in call)


def test_an_unsafe_tree_is_refused_before_any_container_starts() -> None:
    fake = FakeDocker()

    with pytest.raises(TreeError):
        DockerSandbox(process_runner=fake).run({"../escape.py": b""}, COMMAND)

    assert fake.calls == []
