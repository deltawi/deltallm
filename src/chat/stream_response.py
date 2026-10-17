from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from fastapi.responses import StreamingResponse

from src.chat.executor import OpenedStream
from src.metrics import increment_router_health_update_failure
from src.router.health_state import DeploymentHealthRef
from src.router.execution import ManagedFailoverResult, RequestDeadline


logger = logging.getLogger(__name__)

# Cleanup has a separate, short grace period because the request deadline may
# already be exhausted. Provider connections and distributed leases must not
# remain owned indefinitely when their close path is unhealthy.
STREAM_CLEANUP_TIMEOUT_SECONDS = 5.0


class ManagedStreamLifecycle:
    """Single idempotent owner for an opened upstream and its capacity permit."""

    def __init__(
        self,
        opened_stream: OpenedStream,
        managed_stream: ManagedFailoverResult[OpenedStream],
    ) -> None:
        self.opened_stream = opened_stream
        self.managed_stream = managed_stream
        self._closed = False
        self._close_lock = anyio.Lock()

    async def close_upstream(self, exc: BaseException | None = None) -> None:
        """Provider completion does not release the downstream response's permit."""
        await self.opened_stream.close(exc)

    async def close(self, exc: BaseException | None = None) -> None:
        async with self._close_lock:
            if self._closed:
                return
            try:
                await self.close_upstream(exc)
            finally:
                await self.managed_stream.release()
            self._closed = True


async def record_stream_health_outcome(
    *,
    cooldown_manager: Any,
    health_ref: DeploymentHealthRef,
    health_error: Exception | None,
    succeeded: bool,
    recovery_token: str | None,
) -> None:
    try:
        if health_error is not None:
            await cooldown_manager.record_failure(
                health_ref,
                str(health_error),
                exc=health_error,
                recovery_token=recovery_token,
            )
        elif succeeded:
            await cooldown_manager.record_success(
                health_ref,
                recovery_token=recovery_token,
            )
    except Exception:
        increment_router_health_update_failure()
        logger.warning(
            "post-stream router health update failed deployment_id=%s",
            health_ref.deployment_id,
            exc_info=True,
        )


async def close_stream_resources(
    close: Callable[[], Awaitable[None]],
    *,
    timeout_seconds: float = STREAM_CLEANUP_TIMEOUT_SECONDS,
) -> bool:
    """Run stream cleanup under cancellation shielding and a bounded grace period."""

    with anyio.move_on_after(timeout_seconds, shield=True) as cleanup_scope:
        await close()
    if cleanup_scope.cancel_called:
        logger.warning("stream resource cleanup timed out after %.1fs", timeout_seconds)
        return False
    return True


class DeadlineStreamingResponse(StreamingResponse):
    """Streaming response that owns the total send deadline and body cleanup."""

    def __init__(
        self,
        content: Any,
        *,
        deadline: RequestDeadline,
        close: Callable[[BaseException | None], Awaitable[None]],
        **kwargs: Any,
    ) -> None:
        super().__init__(content, **kwargs)
        self._deadline = deadline
        self._close = close

    async def stream_response(self, send: Callable[..., Awaitable[None]]) -> None:
        failure: BaseException | None = None
        try:
            await self._deadline.wait_for(super().stream_response(send))
        except BaseException as exc:
            failure = exc
            raise
        finally:
            try:
                await close_stream_resources(lambda: self._close(failure))
            finally:
                body_close = getattr(self.body_iterator, "aclose", None)
                if body_close is not None:
                    await close_stream_resources(body_close)
