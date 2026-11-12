"""Security tests that attack the sandbox from the inside.

Each probe is a pytest module executed in a real container. The probes try
the forbidden action and pass only when it is refused, so these tests verify
behavior, not the presence of configuration flags.
"""

import secrets
from pathlib import Path

import pytest
from pydantic import ValidationError

from patchpilot.sandbox import DockerSandbox, PytestCommand
from patchpilot.sandbox.tree import TreeError, collect_tree
from tests.integration.helpers import PROBE, SMALL, assert_probe_passed, run_probe

pytestmark = pytest.mark.sandbox

NETWORK_PROBE = """
import os
import socket

import pytest


def test_cannot_connect_to_a_public_address():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
        client.settimeout(3)
        with pytest.raises(OSError):
            client.connect(("1.1.1.1", 53))


def test_cannot_send_udp_to_a_public_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        with pytest.raises(OSError):
            client.sendto(b"x", ("8.8.8.8", 53))


def test_cannot_resolve_a_host_name():
    with pytest.raises(OSError):
        socket.getaddrinfo("example.com", 443)


def test_only_the_loopback_interface_exists():
    assert os.listdir("/sys/class/net") == ["lo"]


def test_loopback_works_so_the_probes_above_are_meaningful():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.socket() as client:
            client.settimeout(3)
            client.connect(server.getsockname())
"""

PRIVILEGE_PROBE = """
import os
import stat

import pytest


def _status():
    with open("/proc/self/status") as handle:
        return dict(line.rstrip("\\n").split(":\\t", 1) for line in handle if ":\\t" in line)


def test_runs_as_the_unprivileged_sandbox_user():
    assert os.getuid() == os.geteuid() == 10001
    assert os.getgid() == os.getegid() == 10001
    assert os.getgroups() in ([], [10001])


def test_holds_no_capabilities_at_all():
    status = _status()
    for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        assert int(status[name], 16) == 0, name


def test_cannot_gain_privileges():
    status = _status()
    assert status["NoNewPrivs"] == "1"
    assert status["Seccomp"] == "2"
    with pytest.raises(PermissionError):
        os.setuid(0)


def test_no_docker_socket_is_reachable():
    for path in ("/var/run/docker.sock", "/run/docker.sock"):
        assert not os.path.lexists(path)


def test_image_has_no_setuid_or_setgid_programs():
    found = []
    for current, dirnames, filenames in os.walk("/"):
        dirnames[:] = [d for d in dirnames if os.path.join(current, d) not in ("/proc", "/sys")]
        for name in filenames:
            path = os.path.join(current, name)
            try:
                mode = os.lstat(path).st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode) and mode & (stat.S_ISUID | stat.S_ISGID):
                found.append(path)
    assert found == []
"""

FILESYSTEM_PROBE = """
import errno
import os
import subprocess

import pytest

SKIP = ("/proc", "/sys")


def _writable_directories():
    writable = []
    for current, dirnames, _ in os.walk("/"):
        dirnames[:] = [d for d in dirnames if os.path.join(current, d) not in SKIP]
        probe = os.path.join(current, ".patchpilot-write-probe")
        try:
            with open(probe, "w"):
                pass
        except OSError:
            continue
        os.unlink(probe)
        writable.append(current)
    return writable


def test_only_the_scratch_locations_are_writable():
    # Found by trying to create a file in every directory of the container.
    roots = {"/workspace", "/tmp", "/dev/shm", "/dev/mqueue"}
    for path in _writable_directories():
        assert any(path == root or path.startswith(root + "/") for root in roots), path


@pytest.mark.parametrize(
    "path",
    [
        "/escape.txt",
        "/etc/escape.txt",
        "/etc/passwd",
        "/usr/local/lib/python3.12/site-packages/escape.py",
        "/opt/patchpilot/requirements.lock",
        "/home/escape.txt",
        "/var/tmp/escape.txt",
        "/workspace/../escape.txt",
        "/tmp/../root/escape.txt",
    ],
)
def test_cannot_write_outside_the_scratch_locations(path):
    with pytest.raises(OSError):
        with open(path, "a"):
            pass


def test_a_symlink_cannot_be_used_to_write_outside():
    os.symlink("/etc/passwd", "/tmp/link-to-passwd")
    with pytest.raises(OSError):
        with open("/tmp/link-to-passwd", "a"):
            pass
    os.symlink("/usr/local", "/workspace/link-to-usr")
    with pytest.raises(OSError):
        with open("/workspace/link-to-usr/escape.txt", "w"):
            pass


@pytest.mark.parametrize("directory", ["/tmp", "/workspace"])
def test_scratch_space_is_size_capped(directory):
    chunk = b"x" * (1024 * 1024)
    written = 0
    with pytest.raises(OSError) as caught:
        with open(os.path.join(directory, "fill.bin"), "wb") as handle:
            for _ in range(64):
                handle.write(chunk)
                handle.flush()
                written += 1
    os.unlink(os.path.join(directory, "fill.bin"))
    assert caught.value.errno == errno.ENOSPC
    assert written <= 17


@pytest.mark.parametrize("directory", ["/tmp", "/workspace", "/dev/shm"])
def test_programs_cannot_be_executed_from_scratch_space(directory):
    path = os.path.join(directory, "payload.sh")
    with open(path, "w") as handle:
        handle.write("#!/bin/sh\\necho ran\\n")
    os.chmod(path, 0o755)
    with pytest.raises(PermissionError):
        subprocess.run([path], check=False)
    os.unlink(path)


def test_no_host_filesystem_is_mounted():
    shared = {"virtiofs", "9p", "fuse", "fuse.sshfs", "nfs", "nfs4", "cifs", "vboxsf"}
    with open("/proc/self/mountinfo") as handle:
        for line in handle:
            fields = line.split()
            mount_point = fields[4]
            options = fields[5].split(",")
            fstype = fields[fields.index("-") + 1]
            assert fstype not in shared, line
            if fstype in ("ext4", "xfs", "btrfs", "overlay"):
                assert "ro" in options, line
            assert not mount_point.startswith(("/Users", "/home/", "/host", "/mnt/")), line
    for path in ("/Users", "/host_mnt", "/mnt/lima-colima"):
        assert not os.path.exists(path)
"""

SECRET_PROBE = """
import json
import os

# Stored reversed, so the literal secret exists nowhere inside the container
# unless the sandbox leaked it.
CANARY = "__REVERSED_CANARY__"[::-1]
HOST_PATH = "__HOST_PATH__"
SKIP = ("/proc", "/sys", "/dev")


def test_environment_holds_no_secret():
    print("ENVIRONMENT", json.dumps(sorted(os.environ)))
    assert CANARY not in json.dumps(dict(os.environ))
    for name in os.environ:
        if name == "GPG_KEY":  # public release-signing fingerprint set by the base image
            continue
        assert not any(word in name for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")), name


def test_host_secret_file_is_not_reachable():
    assert not os.path.exists(HOST_PATH)
    assert not os.path.exists(os.path.dirname(HOST_PATH))


def test_secret_is_nowhere_in_the_container_filesystem():
    needle = CANARY.encode()
    for current, dirnames, filenames in os.walk("/"):
        dirnames[:] = [d for d in dirnames if os.path.join(current, d) not in SKIP]
        for name in filenames:
            path = os.path.join(current, name)
            try:
                if os.path.getsize(path) > 4_000_000:
                    continue
                with open(path, "rb") as handle:
                    assert needle not in handle.read(), path
            except OSError:
                continue


def test_process_listing_shows_no_host_processes():
    pids = [int(name) for name in os.listdir("/proc") if name.isdigit()]
    assert len(pids) < 20
"""


def test_network_access_is_blocked(sandbox: DockerSandbox) -> None:
    assert_probe_passed(run_probe(sandbox, NETWORK_PROBE), expected_tests=5)


def test_process_has_no_privileges(sandbox: DockerSandbox) -> None:
    assert_probe_passed(run_probe(sandbox, PRIVILEGE_PROBE), expected_tests=5)


def test_filesystem_is_confined(sandbox: DockerSandbox) -> None:
    assert_probe_passed(run_probe(sandbox, FILESYSTEM_PROBE), expected_tests=17)


def test_planted_secrets_do_not_reach_the_sandbox(
    docker_sandbox: DockerSandbox, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = f"patchpilot-canary-{secrets.token_hex(16)}"
    secret_file = tmp_path / "credentials" / "token.txt"
    secret_file.parent.mkdir()
    secret_file.write_text(canary)
    for name in ("ANTHROPIC_API_KEY", "PATCHPILOT_GITHUB_TOKEN", "GITHUB_TOKEN", "AWS_SECRET_KEY"):
        monkeypatch.setenv(name, canary)
    # A fresh sandbox, so it is built from the environment that holds the secrets.
    sandbox = DockerSandbox()
    code = SECRET_PROBE.replace("__HOST_PATH__", str(secret_file))
    code = code.replace("__REVERSED_CANARY__", canary[::-1])
    assert canary not in code

    result = run_probe(sandbox, code)

    assert_probe_passed(result, expected_tests=4)
    assert sandbox.list_containers() == []
    assert canary not in result.stdout
    assert canary not in result.stderr
    assert canary not in (result.junit_xml or "")
    assert "ANTHROPIC_API_KEY" not in result.stdout
    assert "GITHUB_TOKEN" not in result.stdout


def test_a_source_tree_cannot_smuggle_a_host_file_in_through_a_symlink(tmp_path: Path) -> None:
    secret_file = tmp_path / "token.txt"
    secret_file.write_text("planted-secret")
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n")
    (repo / "tests" / "fixture.txt").symlink_to(secret_file)

    with pytest.raises(TreeError, match="symbolic link"):
        collect_tree(repo)


def test_a_traversal_target_never_reaches_the_sandbox(sandbox: DockerSandbox) -> None:
    with pytest.raises(ValidationError):
        PytestCommand(name="escape", targets=("../../etc",))
    with pytest.raises(TreeError):
        sandbox.run({"../outside.py": b""}, PROBE, SMALL)
