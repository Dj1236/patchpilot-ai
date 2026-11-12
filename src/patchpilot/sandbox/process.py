"""Run a host process with a time limit and bounded output capture."""

import contextlib
import os
import resource
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import IO

_CHUNK = 65_536


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    duration_seconds: float
    aborted: bool = False


class _BoundedReader(threading.Thread):
    """Keep the first ``limit`` bytes of a stream and discard the rest.

    The stream is always drained to the end, so a process that prints far
    more than the limit can neither block on a full pipe nor grow host memory.
    """

    def __init__(self, stream: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self._stream = stream
        self._limit = limit
        self._chunks: list[bytes] = []
        self._kept = 0
        self.truncated = False

    def run(self) -> None:
        try:
            while chunk := self._stream.read(_CHUNK):
                room = self._limit - self._kept
                if room > 0:
                    self._chunks.append(chunk[:room])
                    self._kept += min(len(chunk), room)
                if len(chunk) > room:
                    self.truncated = True
        except (OSError, ValueError):
            # The pipe was closed while the process was being killed.
            pass

    @property
    def data(self) -> bytes:
        return b"".join(self._chunks)


def _feed(stream: IO[bytes], data: bytes) -> None:
    try:
        stream.write(data)
        stream.close()
    except (BrokenPipeError, OSError, ValueError):
        # The process exited or was killed before reading all of its input.
        pass


def _kill(process: subprocess.Popen[bytes], *, group: bool) -> None:
    """Kill the process, and its whole process group when it leads one."""
    if group:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError):
        process.kill()


def run_bounded(
    argv: Sequence[str],
    *,
    timeout: float,
    max_output_bytes: int,
    env: Mapping[str, str],
    input_bytes: bytes | None = None,
    on_timeout: Callable[[], None] | None = None,
    new_process_group: bool = False,
    max_file_bytes: int | None = None,
    abort_when: Callable[[], bool] | None = None,
    poll_interval: float = 0.05,
) -> ProcessResult:
    """Run ``argv`` without a shell, enforcing a timeout and an output cap.

    ``on_timeout`` runs before the local process is killed. The sandbox uses
    it to kill the container, because killing a ``docker exec`` client does
    not stop the process it started inside the container.

    ``new_process_group`` starts the process as the leader of its own group
    and kills the whole group at the end, so helper processes it spawned do
    not outlive it. ``max_file_bytes`` sets the operating system's per-file
    size limit for the process and its children. ``abort_when`` is polled
    while the process runs; when it returns True the process is killed and
    the result is marked ``aborted``. Polling means a limit checked this way
    can be overshot by whatever the process does within one interval.
    """

    def limit_file_size() -> None:
        assert max_file_bytes is not None  # noqa: S101
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file_bytes, max_file_bytes))

    started = time.monotonic()
    # argv is assembled by PatchPilot from validated values and runs with no shell.
    process = subprocess.Popen(  # noqa: S603
        list(argv),
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        start_new_session=new_process_group,
        preexec_fn=limit_file_size if max_file_bytes is not None else None,
    )
    assert process.stdout is not None  # noqa: S101
    assert process.stderr is not None  # noqa: S101
    readers = (
        _BoundedReader(process.stdout, max_output_bytes),
        _BoundedReader(process.stderr, max_output_bytes),
    )
    for reader in readers:
        reader.start()
    feeder: threading.Thread | None = None
    if input_bytes is not None:
        assert process.stdin is not None  # noqa: S101
        feeder = threading.Thread(target=_feed, args=(process.stdin, input_bytes), daemon=True)
        feeder.start()

    timed_out = False
    aborted = False
    deadline = started + timeout
    while True:
        remaining = deadline - time.monotonic()
        wait = max(remaining, 0.0) if abort_when is None else min(poll_interval, max(remaining, 0))
        try:
            process.wait(timeout=wait)
            break
        except subprocess.TimeoutExpired:
            if abort_when is not None and abort_when():
                aborted = True
            elif time.monotonic() >= deadline:
                timed_out = True
            else:
                continue
        try:
            if timed_out and on_timeout is not None:
                on_timeout()
        finally:
            _kill(process, group=new_process_group)
            process.wait()
        break
    if new_process_group:
        # The leader is gone; make sure nothing it started is left behind.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)

    for reader in readers:
        reader.join(timeout=10)
    if feeder is not None:
        feeder.join(timeout=10)
    for stream in (process.stdout, process.stderr):
        stream.close()

    return ProcessResult(
        exit_code=None if (timed_out or aborted) else process.returncode,
        stdout=readers[0].data,
        stderr=readers[1].data,
        stdout_truncated=readers[0].truncated,
        stderr_truncated=readers[1].truncated,
        timed_out=timed_out,
        duration_seconds=time.monotonic() - started,
        aborted=aborted,
    )
