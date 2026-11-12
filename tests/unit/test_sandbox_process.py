import os
import sys
import time

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
