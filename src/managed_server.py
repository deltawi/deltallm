"""Small adapter for the locked Uvicorn server's pre-lifespan drain boundary."""

from __future__ import annotations

import asyncio
import socket
from types import FrameType

import uvicorn
from uvicorn.lifespan.on import LifespanOn
from uvicorn.server import ServerState

from src.process_lifecycle import ProcessLifecycle
from src.telemetry.lifecycle import stop_tasks_before_deadline

SUPPORTED_UVICORN = "0.40.0"


class ManagedLifespan(LifespanOn):
    def __init__(
        self, config: uvicorn.Config, lifecycle: ProcessLifecycle, server_state: ServerState
    ) -> None:
        super().__init__(config)
        self.lifecycle = lifecycle
        self.server_state = server_state

    async def shutdown(self) -> None:
        deadlines = self.lifecycle.begin_drain()
        self.lifecycle.enter_phase("cancellation")
        await stop_tasks_before_deadline(
            (*self.server_state.tasks, *self.lifecycle.producers),
            deadline=deadlines.cancellation,
            cancel_first=self.lifecycle.remaining(deadlines.responses) == 0,
        )
        self.lifecycle.begin_stopping()
        await super().shutdown()


class ManagedServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config, lifecycle: ProcessLifecycle) -> None:
        if uvicorn.__version__ != SUPPORTED_UVICORN:
            raise RuntimeError("Managed server requires the repository's frozen Uvicorn version")
        if config.workers != 1 or config.reload or config.lifespan != "on":
            raise ValueError("Managed server requires one process, lifespan on, and no reload")
        super().__init__(config)
        self.lifecycle = lifecycle
        config.load()
        config.lifespan_class = lambda configured: ManagedLifespan(
            configured, lifecycle, self.server_state
        )

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # Signal handling must withdraw admission synchronously, before the server
        # starts waiting. Repeated signals neither reset budgets nor skip cleanup.
        self.lifecycle.begin_drain()

    async def on_tick(self, counter: int) -> bool:
        requested = await super().on_tick(counter)
        if requested:
            self.lifecycle.begin_drain()
        deadlines = self.lifecycle.deadlines
        return deadlines is not None and self.lifecycle.remaining(deadlines.withdrawal) == 0

    async def _wait_tasks_to_complete(self) -> None:
        await super()._wait_tasks_to_complete()
        pending = [task for task in self.lifecycle.producers if not task.done()]
        if pending:
            await asyncio.wait(pending)

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        deadlines = self.lifecycle.begin_drain()
        await asyncio.sleep(self.lifecycle.remaining(deadlines.withdrawal))
        self.lifecycle.enter_phase("responses")
        # Uvicorn 0.40.0 spends 100 ms notifying connections before its timeout.
        self.config.timeout_graceful_shutdown = max(
            0.0, self.lifecycle.remaining(deadlines.responses) - 0.1
        )
        await super().shutdown(sockets)
