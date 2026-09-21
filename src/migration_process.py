"""Bound the entire Prisma CLI process group, including its Node child."""

from __future__ import annotations

import os
import signal
import subprocess
import selectors
from time import monotonic
from collections.abc import Mapping

MAX_OUTPUT_BYTES = 256 * 1024


class MigrationOutputLimitError(RuntimeError):
    pass


def run_migration_process(
    command: list[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
    **_: object,
) -> subprocess.CompletedProcess[str]:
    deadline = monotonic() + timeout
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True
    )
    try:
        return _collect(process, command, deadline=deadline, timeout=timeout)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            # The container runtime is the final owner if a kernel wait cannot
            # finish. Popen's context manager would perform an unbounded wait.
            pass
        raise
    finally:
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                pipe.close()


def _collect(
    process: subprocess.Popen[bytes], command: list[str], *, deadline: float, timeout: float
) -> subprocess.CompletedProcess[str]:
    with selectors.DefaultSelector() as selector:
        streams = [bytearray(), bytearray()]
        for index, pipe in enumerate((process.stdout, process.stderr)):
            selector.register(pipe, selectors.EVENT_READ, index)
        while selector.get_map():
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _events in selector.select(timeout=min(remaining, 0.1)):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                buffer = streams[key.data]
                if len(buffer) + len(chunk) > MAX_OUTPUT_BYTES:
                    raise MigrationOutputLimitError("Prisma output exceeded its bound")
                buffer.extend(chunk)
        process.wait(timeout=max(0, deadline - monotonic()))
        return subprocess.CompletedProcess(
            command,
            process.returncode,
            *(stream.decode("utf-8", errors="replace") for stream in streams),
        )
