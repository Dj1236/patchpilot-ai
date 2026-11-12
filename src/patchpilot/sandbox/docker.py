"""Restricted Docker runner for approved pytest commands.

Lifecycle of one run:

1. Start a locked-down container that only sleeps.
2. Stream the source tree into its memory-backed workspace.
3. Execute the fixed pytest command line under a host-side timeout.
4. Read limit events and the JUnit report back out.
5. Force-remove the container, whatever happened.

Nothing from the host is mounted into the container, and no host environment
variable is passed to it. These restrictions reduce risk; they do not
guarantee isolation against hostile code, because a container shares the
kernel of the Docker host.
"""

import hashlib
import os
import platform
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Protocol

from patchpilot import __version__
from patchpilot.sandbox.junit import MAX_JUNIT_BYTES, JUnitError, parse_junit
from patchpilot.sandbox.models import (
    CaseResult,
    Counts,
    PytestCommand,
    RunMetadata,
    SandboxLimits,
    SandboxResult,
    SandboxStatus,
)
from patchpilot.sandbox.process import ProcessResult, run_bounded
from patchpilot.sandbox.tree import build_tar, tree_digest, validate_relative_path

SANDBOX_UID = 10001
SANDBOX_USER = f"{SANDBOX_UID}:{SANDBOX_UID}"
WORKSPACE_DIR = "/workspace"
TMP_DIR = "/tmp"  # noqa: S108 - a path inside the container, not on the host
JUNIT_PATH = f"{TMP_DIR}/.patchpilot/junit.xml"
CONTAINER_LABEL = "patchpilot.sandbox"
IMAGE_REPOSITORY = "patchpilot-sandbox"

# The only host variables the docker client itself is allowed to see.
_DOCKER_CLIENT_ENV = (
    "PATH",
    "HOME",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
)

_CONTROL_TIMEOUT = 60.0
_BUILD_TIMEOUT = 900.0
_CONTROL_OUTPUT_BYTES = 262_144
# Extra lifetime of the container beyond the test timeout. If the host process
# dies, the container still ends by itself after this backstop.
_BACKSTOP_SECONDS = 120
# Exit codes that come from docker itself, not from the process it started.
_DOCKER_FAILURE_CODES = frozenset({125, 126, 127})
_KILLED_EXIT_CODE = 137
# /dev/shm stays available for multiprocessing primitives, but small.
_SHM_MB = 8

_PYTEST_EXIT_STATUS = {
    0: SandboxStatus.PASSED,
    1: SandboxStatus.FAILED,
    2: SandboxStatus.COLLECTION_ERROR,
    5: SandboxStatus.NO_TESTS_COLLECTED,
}


class SandboxInfrastructureError(RuntimeError):
    """The sandbox itself failed; this says nothing about the code under test."""


class ProcessRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        max_output_bytes: int,
        env: Mapping[str, str],
        input_bytes: bytes | None = None,
        on_timeout: Callable[[], None] | None = None,
    ) -> ProcessResult: ...


@dataclass(frozen=True)
class ImageInfo:
    ref: str
    image_id: str


@dataclass(frozen=True)
class LimitEvents:
    """Limit violations recorded by the container's control group."""

    oom_kill_count: int | None
    pids_limit_hit: bool | None


def docker_client_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Return the minimal environment given to the docker client process."""
    return {name: environ[name] for name in _DOCKER_CLIENT_ENV if name in environ}


def image_definition() -> dict[str, bytes]:
    """Return the files that define the sandbox image."""
    package = resources.files("patchpilot.sandbox") / "image"
    return {name: (package / name).read_bytes() for name in ("Dockerfile", "requirements.lock")}


def image_ref() -> str:
    """Return an image tag derived from the image definition.

    Changing the Dockerfile or the lock file changes the tag, so a stale image
    is never reused silently.
    """
    digest = hashlib.sha256()
    for name, data in sorted(image_definition().items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return f"{IMAGE_REPOSITORY}:{digest.hexdigest()[:12]}"


def build_run_argv(name: str, image: str, limits: SandboxLimits) -> list[str]:
    """Return the ``docker run`` command that starts the idle sandbox container."""
    scratch = f"rw,nosuid,nodev,noexec,uid={SANDBOX_UID},gid={SANDBOX_UID},mode=0700"
    return [
        "run",
        "--detach",
        "--name",
        name,
        "--label",
        f"{CONTAINER_LABEL}=1",
        "--pull",
        "never",
        "--init",
        "--network",
        "none",
        "--user",
        SANDBOX_USER,
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--read-only",
        "--tmpfs",
        f"{WORKSPACE_DIR}:{scratch},size={limits.workspace_mb}m",
        "--tmpfs",
        f"{TMP_DIR}:{scratch},size={limits.tmp_mb}m",
        "--memory",
        f"{limits.memory_mb}m",
        "--memory-swap",
        f"{limits.memory_mb}m",
        "--cpus",
        f"{limits.cpus:g}",
        "--pids-limit",
        str(limits.pids),
        "--ulimit",
        "core=0",
        "--ulimit",
        "nofile=1024:1024",
        "--ipc",
        "private",
        "--shm-size",
        f"{_SHM_MB}m",
        "--hostname",
        "sandbox",
        "--workdir",
        WORKSPACE_DIR,
        "--stop-signal",
        "KILL",
        image,
        "sleep",
        str(limits.timeout_seconds + _BACKSTOP_SECONDS),
    ]


def build_load_argv(name: str) -> list[str]:
    """Return the ``docker exec`` command that unpacks the workspace archive."""
    extract = ["tar", "-x", "-f", "-", "-C", WORKSPACE_DIR]
    return ["exec", "--interactive", "--user", SANDBOX_USER, name, *extract]


def build_pytest_argv(command: PytestCommand) -> list[str]:
    """Return the fixed pytest command line run inside the container."""
    return [
        "python",
        "-m",
        "pytest",
        "-p",
        "no:cacheprovider",
        "-o",
        "junit_family=xunit2",
        f"--junitxml={JUNIT_PATH}",
        "--color=no",
        "-q",
        *command.targets,
    ]


def build_exec_argv(name: str, command: PytestCommand, source_roots: Sequence[str]) -> list[str]:
    """Return the ``docker exec`` command that runs the tests."""
    argv = ["exec", "--user", SANDBOX_USER, "--workdir", WORKSPACE_DIR]
    if source_roots:
        pythonpath = ":".join(
            f"{WORKSPACE_DIR}/{validate_relative_path(root)}" for root in source_roots
        )
        argv += ["--env", f"PYTHONPATH={pythonpath}"]
    return [*argv, name, *build_pytest_argv(command)]


def classify(
    exit_code: int | None, *, timed_out: bool, oom_kill_count: int | None
) -> SandboxStatus:
    """Turn the raw facts of a finished run into a status."""
    if timed_out:
        return SandboxStatus.TIMEOUT
    if oom_kill_count:
        return SandboxStatus.OUT_OF_MEMORY
    if exit_code is None or exit_code in _DOCKER_FAILURE_CODES:
        return SandboxStatus.INFRASTRUCTURE_ERROR
    if exit_code == _KILLED_EXIT_CODE:
        # Killed without a recorded out-of-memory event or timeout: the
        # sandbox cannot say why, so it does not blame the code under test.
        return SandboxStatus.INFRASTRUCTURE_ERROR
    return _PYTEST_EXIT_STATUS.get(exit_code, SandboxStatus.RUNNER_ERROR)


def parse_limit_events(text: str) -> LimitEvents:
    """Parse the output of the control-group event probe."""
    memory, _, pids = text.partition("---")

    def field(block: str, key: str) -> int | None:
        for line in block.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == key and parts[1].isdigit():
                return int(parts[1])
        return None

    pids_max = field(pids, "max")
    return LimitEvents(
        oom_kill_count=field(memory, "oom_kill"),
        pids_limit_hit=None if pids_max is None else pids_max > 0,
    )


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


class DockerSandbox:
    """Run approved pytest commands inside a restricted container."""

    def __init__(
        self,
        *,
        docker_bin: str = "docker",
        process_runner: ProcessRunner = run_bounded,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._docker_bin = docker_bin
        self._run_process = process_runner
        self._env = docker_client_env(os.environ if environ is None else environ)
        self._engine: tuple[str, str, str, str] | None = None

    # -- docker plumbing -------------------------------------------------

    def _docker(
        self,
        args: Sequence[str],
        *,
        timeout: float = _CONTROL_TIMEOUT,
        max_output_bytes: int = _CONTROL_OUTPUT_BYTES,
        input_bytes: bytes | None = None,
        on_timeout: Callable[[], None] | None = None,
    ) -> ProcessResult:
        try:
            return self._run_process(
                [self._docker_bin, *args],
                timeout=timeout,
                max_output_bytes=max_output_bytes,
                env=self._env,
                input_bytes=input_bytes,
                on_timeout=on_timeout,
            )
        except OSError as exc:
            raise SandboxInfrastructureError(f"cannot run the docker client: {exc}") from exc

    def _control(
        self,
        args: Sequence[str],
        what: str,
        *,
        timeout: float = _CONTROL_TIMEOUT,
        input_bytes: bytes | None = None,
    ) -> str:
        """Run a docker command that must succeed, returning its stdout."""
        result = self._docker(args, timeout=timeout, input_bytes=input_bytes)
        if result.timed_out:
            raise SandboxInfrastructureError(f"{what} timed out")
        if result.exit_code != 0:
            detail = _text(result.stderr).strip()[-500:]
            raise SandboxInfrastructureError(f"{what} failed: {detail}")
        return _text(result.stdout)

    # -- availability, image and metadata --------------------------------

    def is_available(self) -> bool:
        """Return True when a Docker engine can be reached."""
        try:
            self._engine_info()
        except SandboxInfrastructureError:
            return False
        return True

    def _engine_info(self) -> tuple[str, str, str, str]:
        if self._engine is None:
            version = self._control(
                ["version", "--format", "{{.Server.Version}}|{{.Server.Os}}/{{.Server.Arch}}"],
                "reading the Docker engine version",
            ).strip()
            info = self._control(
                ["info", "--format", "{{.OperatingSystem}}|{{.CgroupVersion}}"],
                "reading Docker engine information",
            ).strip()
            engine_version, _, engine_platform = version.partition("|")
            engine_os, _, cgroup_version = info.partition("|")
            self._engine = (engine_version, engine_platform, engine_os, cgroup_version)
        return self._engine

    def _image_id(self, ref: str) -> str | None:
        result = self._docker(["image", "inspect", "--format", "{{.Id}}", ref])
        if result.timed_out or result.exit_code != 0:
            return None
        return _text(result.stdout).strip() or None

    def ensure_image(self) -> ImageInfo:
        """Return the sandbox image, building it from PatchPilot's definition if missing.

        The build context contains only PatchPilot's Dockerfile and lock file,
        never code from a repository under analysis.
        """
        ref = image_ref()
        image_id = self._image_id(ref)
        if image_id is None:
            with tempfile.TemporaryDirectory(prefix="patchpilot-image-") as context:
                for name, data in image_definition().items():
                    (Path(context) / name).write_bytes(data)
                self._control(
                    ["build", "--tag", ref, "--label", f"{CONTAINER_LABEL}.image=1", context],
                    "building the sandbox image",
                    timeout=_BUILD_TIMEOUT,
                )
            image_id = self._image_id(ref)
            if image_id is None:
                raise SandboxInfrastructureError("the sandbox image is missing after its build")
        return ImageInfo(ref=ref, image_id=image_id)

    # -- container lifecycle ---------------------------------------------

    def _remove(self, name: str) -> None:
        self._docker(["rm", "--force", "--volumes", name])

    def _kill(self, name: str) -> None:
        self._docker(["kill", name])

    def list_containers(self) -> list[str]:
        """Return the names of all sandbox containers that still exist."""
        output = self._control(
            ["ps", "--all", "--filter", f"label={CONTAINER_LABEL}", "--format", "{{.Names}}"],
            "listing sandbox containers",
        )
        return [line for line in output.splitlines() if line]

    def remove_leftovers(self) -> list[str]:
        """Force-remove sandbox containers left behind by a crashed run."""
        names = self.list_containers()
        for name in names:
            self._remove(name)
        return names

    def _limit_events(self, name: str) -> LimitEvents:
        probe = "cat /sys/fs/cgroup/memory.events; echo ---; cat /sys/fs/cgroup/pids.events"
        result = self._docker(["exec", "--user", SANDBOX_USER, name, "sh", "-c", probe])
        if not result.timed_out and result.exit_code == 0:
            return parse_limit_events(_text(result.stdout))
        # The container is gone or cannot start another process. Ask the
        # engine whether it recorded an out-of-memory kill.
        state = self._docker(["inspect", "--format", "{{.State.OOMKilled}}", name])
        if not state.timed_out and state.exit_code == 0:
            killed = _text(state.stdout).strip() == "true"
            return LimitEvents(oom_kill_count=1 if killed else None, pids_limit_hit=None)
        return LimitEvents(oom_kill_count=None, pids_limit_hit=None)

    def _junit(self, name: str) -> tuple[str | None, Counts | None, tuple[CaseResult, ...]]:
        result = self._docker(
            ["exec", "--user", SANDBOX_USER, name, "cat", JUNIT_PATH],
            max_output_bytes=MAX_JUNIT_BYTES + 1,
        )
        if result.timed_out or result.exit_code != 0 or not result.stdout:
            return None, None, ()
        if result.stdout_truncated or len(result.stdout) > MAX_JUNIT_BYTES:
            return None, None, ()
        xml_text = _text(result.stdout)
        try:
            counts, cases = parse_junit(xml_text)
        except JUnitError:
            return None, None, ()
        return xml_text, counts, cases

    def run(
        self,
        files: Mapping[str, bytes],
        command: PytestCommand,
        limits: SandboxLimits | None = None,
        *,
        source_roots: Sequence[str] = (),
    ) -> SandboxResult:
        """Run ``command`` against ``files`` and return a structured result.

        Sandbox failures are returned as ``INFRASTRUCTURE_ERROR`` results
        instead of being raised, so callers always get the same shape back.
        """
        limits = limits or SandboxLimits()
        archive = build_tar(files)
        started_at = datetime.now(UTC)
        started = time.monotonic()
        name = f"patchpilot-{uuid.uuid4().hex[:16]}"
        container_argv = tuple(build_pytest_argv(command))
        metadata: RunMetadata | None = None

        def failed(error: str) -> SandboxResult:
            return SandboxResult(
                status=SandboxStatus.INFRASTRUCTURE_ERROR,
                command=command,
                container_argv=container_argv,
                exit_code=None,
                counts=None,
                started_at=started_at,
                duration_seconds=time.monotonic() - started,
                error=error,
                metadata=metadata,
            )

        try:
            engine_version, engine_platform, engine_os, cgroup_version = self._engine_info()
            image = self.ensure_image()
            metadata = RunMetadata(
                runner_version=__version__,
                image_ref=image.ref,
                image_id=image.image_id,
                engine_version=engine_version,
                engine_platform=engine_platform,
                engine_os=engine_os,
                cgroup_version=cgroup_version,
                host_platform=f"{platform.system()} {platform.machine()}",
                source_tree_sha256=tree_digest(files),
                source_roots=tuple(source_roots),
                limits=limits,
            )
            self._control(build_run_argv(name, image.ref, limits), "starting the sandbox")
            self._control(
                build_load_argv(name),
                "loading the workspace into the sandbox",
                input_bytes=archive,
            )
            execution = self._docker(
                build_exec_argv(name, command, source_roots),
                timeout=float(limits.timeout_seconds),
                max_output_bytes=limits.max_output_bytes,
                on_timeout=lambda: self._kill(name),
            )
            events = LimitEvents(oom_kill_count=None, pids_limit_hit=None)
            junit_xml: str | None = None
            counts: Counts | None = None
            cases: tuple[CaseResult, ...] = ()
            if not execution.timed_out:
                events = self._limit_events(name)
                junit_xml, counts, cases = self._junit(name)
        except SandboxInfrastructureError as exc:
            return failed(str(exc))
        finally:
            self._remove(name)

        status = classify(
            execution.exit_code,
            timed_out=execution.timed_out,
            oom_kill_count=events.oom_kill_count,
        )
        error: str | None = None
        if status is SandboxStatus.INFRASTRUCTURE_ERROR:
            error = f"docker exec ended with exit code {execution.exit_code}"
        return SandboxResult(
            status=status,
            command=command,
            container_argv=container_argv,
            exit_code=execution.exit_code,
            counts=counts,
            cases=cases,
            junit_xml=junit_xml,
            stdout=_text(execution.stdout),
            stderr=_text(execution.stderr),
            stdout_truncated=execution.stdout_truncated,
            stderr_truncated=execution.stderr_truncated,
            started_at=started_at,
            duration_seconds=time.monotonic() - started,
            oom_kill_count=events.oom_kill_count,
            pids_limit_hit=events.pids_limit_hit,
            error=error,
            metadata=metadata,
        )
