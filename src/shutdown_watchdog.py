"""A launcher-owned last resort, including blocked interpreter thread joins."""

from __future__ import annotations

import atexit
import os
import threading
from time import monotonic

from src.process_lifecycle import ShutdownDeadlines


class ShutdownWatchdog:
    def __init__(self) -> None:
        self._timer: threading.Timer | None = None
        # CPython joins non-daemon threads before these exit handlers. Keeping the
        # timer armed until then also bounds cancelled PR7 synchronous callbacks.
        atexit.register(self.close)

    def arm(self, deadlines: ShutdownDeadlines) -> None:
        if self._timer is not None:
            return
        self._timer = threading.Timer(max(0, deadlines.total - monotonic()), self._expire)
        self._timer.daemon = True
        self._timer.start()

    @staticmethod
    def _expire() -> None:
        try:
            os.set_blocking(2, False)
            os.write(
                2, b"process shutdown deadline exceeded; forced exit; durable recovery required\n"
            )
        finally:
            os._exit(70)

    def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
