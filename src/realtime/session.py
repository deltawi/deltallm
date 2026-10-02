from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from src.models.errors import ProxyError
from src.providers.openai_realtime import OpenAIRealtimeTarget
from src.realtime.contracts import (
    RealtimeError,
    RealtimeLimits,
    RealtimePermit,
    SocketClosed,
    TextSocket,
)
from src.realtime.protocol import (
    USAGE_EVENTS,
    client_event,
    encode_event,
    parse_event,
    server_event,
)
from src.realtime.errors import safe_identifier
from src.realtime.lifecycle import RealtimeDrain


@dataclass(slots=True)
class SessionStats:
    input_bytes: int = 0
    output_bytes: int = 0
    client_events: int = 0
    server_events: int = 0


class _Relay:
    def __init__(
        self,
        downstream: TextSocket,
        upstream: TextSocket,
        *,
        target: OpenAIRealtimeTarget,
        permit: RealtimePermit,
        limits: RealtimeLimits,
    ) -> None:
        self.downstream = downstream
        self.upstream = upstream
        self.target = target
        self.permit = permit
        self.limits = limits
        self.stats = SessionStats()
        self.last_activity = monotonic()

    async def client_to_provider(self) -> None:
        while True:
            message = await self.downstream.receive_text()
            event = parse_event(message, max_bytes=self.limits.max_message_bytes)
            self.stats.input_bytes += len(message.encode("utf-8"))
            self.stats.client_events += 1
            try:
                if (
                    self.stats.input_bytes > self.limits.max_input_bytes
                    or self.stats.client_events > self.limits.max_client_events
                ):
                    raise RealtimeError(
                        "input_limit_exceeded",
                        "Realtime session input limit reached",
                        close_code=1009,
                    )
                mapped = client_event(event, self.target)
                try:
                    self.permit.authorize_client_event(event)
                except ProxyError as exc:
                    raise RealtimeError.admission_denied() from exc
                async with asyncio.timeout(self.limits.write_seconds):
                    await self.permit.before_client_event(event)
                    await self.upstream.send_text(encode_event(mapped))
            except RealtimeError as exc:
                exc.client_event_id = safe_identifier(event.get("event_id"), self.target.headers)
                raise
            self.last_activity = monotonic()

    async def provider_to_client(self) -> None:
        while True:
            message = await self.upstream.receive_text()
            try:
                event = parse_event(message, max_bytes=self.limits.max_message_bytes)
                mapped = server_event(event, self.target)
            except RealtimeError as exc:
                raise RealtimeError(
                    "invalid_upstream_event", "Invalid upstream event", close_code=1011
                ) from exc
            self.stats.server_events += 1
            if self.stats.server_events > self.limits.max_server_events:
                raise RealtimeError("event_limit_exceeded", "Realtime session event limit reached")
            if event["type"] in USAGE_EVENTS:
                # Never emit a terminal result before the accounting owner
                # durably accepts its receipt (including missing-usage state).
                async with asyncio.timeout(self.limits.write_seconds):
                    await self.permit.accept_usage(event)
            payload = encode_event(mapped)
            async with asyncio.timeout(self.limits.write_seconds):
                await self.downstream.send_text(payload)
            self.stats.output_bytes += len(payload.encode("utf-8"))
            self.last_activity = monotonic()

    async def watch_health(self) -> None:
        while True:
            await asyncio.sleep(min(self.limits.health_seconds, self.limits.idle_seconds))
            if monotonic() - self.last_activity >= self.limits.idle_seconds:
                raise RealtimeError("idle_timeout", "Realtime session was idle")
            async with asyncio.timeout(self.limits.health_seconds):
                await self.permit.check_health()


async def relay_session(
    downstream: TextSocket,
    upstream: TextSocket,
    *,
    target: OpenAIRealtimeTarget,
    permit: RealtimePermit,
    limits: RealtimeLimits,
    drain: RealtimeDrain | None = None,
) -> SessionStats:
    """Relay one pinned connection with bounded pumps and no replay or queue.

    The caller owns admission, socket closing, and durable finalization. This
    owner cancels and joins every pump before returning or propagating failure.
    """
    relay = _Relay(downstream, upstream, target=target, permit=permit, limits=limits)
    drain = drain or RealtimeDrain(limits)
    tasks = [
        asyncio.create_task(pump(), name=f"realtime:{name}")
        for name, pump in (
            ("client", relay.client_to_provider),
            ("provider", relay.provider_to_client),
            ("health", relay.watch_health),
        )
    ]
    try:
        async with asyncio.timeout(limits.session_seconds):
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            # Inspect every completed task, prioritizing policy/accounting
            # failures over a simultaneous normal disconnect.
            for task in tasks:
                if task in done:
                    error = task.exception()
                    if error is not None and not isinstance(error, SocketClosed):
                        raise error
    except TimeoutError as exc:
        raise RealtimeError(
            "session_timeout", "Realtime session timed out", close_code=1013
        ) from exc
    finally:
        drain.begin()
        for task in tasks:
            task.cancel()
        async with drain.cleanup():
            await asyncio.gather(*tasks, return_exceptions=True)
    return relay.stats
