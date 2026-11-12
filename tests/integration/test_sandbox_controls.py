"""Controls for the security probes.

A probe that can never fail proves nothing. These tests run the same probes
in containers that are deliberately weakened (network on, root user, writable
root filesystem) and check that the probes then fail. The weakened containers
are started here directly; the sandbox runner offers no way to weaken itself.
"""

import os
import re
import subprocess

import pytest

from patchpilot.sandbox import DockerSandbox
from patchpilot.sandbox.docker import docker_client_env, image_ref
from tests.integration.test_sandbox_security import (
    FILESYSTEM_PROBE,
    NETWORK_PROBE,
    PRIVILEGE_PROBE,
)

pytestmark = pytest.mark.sandbox

_SCRIPT = (
    "mkdir -p /tmp/control && cat > /tmp/control/test_probe.py && cd /tmp/control "
    "&& python -m pytest -q -p no:cacheprovider"
)


def _failures_in_weakened_container(probe: str, *weakening: str) -> int:
    argv = ["docker", "run", "--rm", "--interactive", "--memory", "256m", "--pids-limit", "64"]
    argv += [*weakening, image_ref(), "sh", "-c", _SCRIPT]
    completed = subprocess.run(  # noqa: S603
        argv,
        input=probe.encode(),
        capture_output=True,
        timeout=120,
        check=False,
        env=docker_client_env(os.environ),
    )
    match = re.search(rb"(\d+) failed", completed.stdout)
    return int(match.group(1)) if match else 0


def test_network_probe_fails_when_the_network_is_on(docker_sandbox: DockerSandbox) -> None:
    assert _failures_in_weakened_container(NETWORK_PROBE) >= 2


def test_privilege_probe_fails_for_a_root_user(docker_sandbox: DockerSandbox) -> None:
    assert _failures_in_weakened_container(PRIVILEGE_PROBE, "--user", "0:0") >= 2


def test_filesystem_probe_fails_with_a_writable_root(docker_sandbox: DockerSandbox) -> None:
    assert _failures_in_weakened_container(FILESYSTEM_PROBE, "--user", "0:0") >= 5
