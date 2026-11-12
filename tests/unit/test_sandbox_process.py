import os
import sys
import time
from pathlib import Path

import pytest

from patchpilot.sandbox.process import run_bounded

ENV = {"PATH": os.environ.get("PATH", "")}


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_captures_exit_code_and_both_streams() -> None:
    result = run_bounded(
        _python("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"),
        timeout=30,
        max_output_bytes=1024,
        env=ENV,
    )

    assert result.exit_code == 3
    assert result.stdout == b"out\n"
    assert result.stderr == b"err\n"
    assert not result.timed_out
    assert not result.stdout_truncated


def test_output_beyond_the_cap_is_discarded_and_flagged() -> None:
    result = run_bounded(
        _python("import sys; sys.stdout.write('a' * 5_000_000)"),
        timeout=30,
        max_output_bytes=1000,
        env=ENV,
    )

    assert result.exit_code == 0
    assert result.stdout == b"a" * 1000
    assert result.stdout_truncated
    assert not result.stderr_truncated


def test_input_is_passed_on_stdin() -> None:
    result = run_bounded(
        _python("import sys; print(len(sys.stdin.buffer.read()))"),
        timeout=30,
        max_output_bytes=1024,
        env=ENV,
        input_bytes=b"x" * 300_000,
    )

    assert result.stdout.strip() == b"300000"


def test_a_process_that_ignores_its_input_does_not_hang() -> None:
    result = run_bounded(
        _python("pass"),
        timeout=30,
        max_output_bytes=1024,
        env=ENV,
        input_bytes=b"x" * 2_000_000,
    )

    assert result.exit_code == 0


def test_timeout_kills_the_process_and_runs_the_callback_first() -> None:
    calls: list[str] = []
    started = time.monotonic()

    result = run_bounded(
        _python("import time; time.sleep(60)"),
        timeout=0.5,
        max_output_bytes=1024,
        env=ENV,
        on_timeout=lambda: calls.append("killed"),
    )

    assert result.timed_out
    assert result.exit_code is None
    assert calls == ["killed"]
    assert time.monotonic() - started < 10


def test_only_the_given_environment_is_visible(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("PATCHPILOT_CANARY", "do-not-leak")

    result = run_bounded(
        _python("import os; print(sorted(os.environ))"),
        timeout=30,
        max_output_bytes=4096,
        env=ENV,
    )

    assert b"PATCHPILOT_CANARY" not in result.stdout


WRITER = """
import sys, time
with open(sys.argv[1], "wb") as handle:
    for _ in range(int(sys.argv[2])):
        handle.write(b"x" * 65536)
        handle.flush()
        time.sleep(float(sys.argv[3]))
"""


def test_per_file_size_limit_stops_a_process_that_writes_too_much(tmp_path: Path) -> None:
    target = tmp_path / "big.bin"

    result = run_bounded(
        [sys.executable, "-c", WRITER, str(target), "64", "0"],
        timeout=30,
        max_output_bytes=4096,
        env=ENV,
        max_file_bytes=1_000_000,
    )

    assert result.exit_code != 0
    assert target.stat().st_size <= 1_000_000


def test_the_file_size_limit_does_not_leak_into_the_parent(tmp_path: Path) -> None:
    run_bounded(_python("pass"), timeout=30, max_output_bytes=1024, env=ENV, max_file_bytes=1000)

    (tmp_path / "after.bin").write_bytes(b"x" * 100_000)


def test_abort_condition_stops_a_running_process(tmp_path: Path) -> None:
    target = tmp_path / "growing.bin"
    started = time.monotonic()

    result = run_bounded(
        [sys.executable, "-c", WRITER, str(target), "2000", "0.01"],
        timeout=60,
        max_output_bytes=4096,
        env=ENV,
        abort_when=lambda: target.exists() and target.stat().st_size > 500_000,
    )

    assert result.aborted and not result.timed_out
    assert result.exit_code is None
    assert time.monotonic() - started < 20
    # Overshoot is bounded by what the process writes in one polling interval.
    assert target.stat().st_size < 2_000_000


def test_timeout_still_applies_while_an_abort_condition_is_polled() -> None:
    result = run_bounded(
        _python("import time; time.sleep(60)"),
        timeout=0.5,
        max_output_bytes=1024,
        env=ENV,
        abort_when=lambda: False,
    )

    assert result.timed_out and not result.aborted


def test_killing_a_process_group_takes_its_children_with_it(tmp_path: Path) -> None:
    marker = tmp_path / "child.pid"
    parent = (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )

    result = run_bounded(
        _python(parent), timeout=1.5, max_output_bytes=1024, env=ENV, new_process_group=True
    )

    assert result.timed_out
    child_pid = int(marker.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(child_pid, 9)
        pytest.fail("the child process survived its process group being killed")
