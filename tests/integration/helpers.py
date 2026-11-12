"""Helpers for tests that run probes inside a real sandbox container."""

from patchpilot.sandbox import DockerSandbox, PytestCommand, SandboxLimits, SandboxResult

# Small limits keep exhaustion tests quick and far away from the host's own
# capacity. Every limit is enforced on the container, never on the host.
SMALL = SandboxLimits(
    timeout_seconds=60, memory_mb=256, cpus=1.0, pids=64, workspace_mb=16, tmp_mb=16
)
PROBE = PytestCommand(name="probe", targets=("tests",))


def run_probe(sandbox: DockerSandbox, code: str, limits: SandboxLimits = SMALL) -> SandboxResult:
    """Run ``code`` as a pytest module inside the sandbox."""
    return sandbox.run({"tests/test_probe.py": code.encode()}, PROBE, limits)


def assert_probe_passed(result: SandboxResult, expected_tests: int) -> None:
    """Assert that every probe ran and passed.

    A probe passes only when the restriction it attacks actually held, so a
    weakened sandbox shows up here as a failed probe.
    """
    detail = f"status={result.status} error={result.error}\n{result.stdout}\n{result.stderr}"
    assert result.status.value == "passed", detail
    assert result.counts is not None, detail
    assert result.counts.passed == expected_tests, detail
    assert result.counts.total == expected_tests, detail
