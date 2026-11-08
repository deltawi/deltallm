"""Preserve one request-owned accounting terminal when a stream is interrupted."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

import anyio
from fastapi import Request

from src.billing.accounting.accounting_protocol import AccountingOperationHandle
from src.billing.spend.spend_operations import SpendPersistenceUnavailable
from src.chat.stream_response import STREAM_CLEANUP_TIMEOUT_SECONDS
from src.telemetry.request_failures import enqueue_request_log_write, request_log_already_emitted
from src.telemetry.spend_operation import billing_write_context, operation_handle


async def write_stream_terminal(request: Request, write: Coroutine[object, object, None]) -> None:
    if not isinstance(operation_handle(request), AccountingOperationHandle):
        await enqueue_request_log_write(request, write)
        return

    # The request can expire or lose its client while the known receipt waits
    # for an ACK. Keep the same write alive for a bounded cleanup period.
    task = asyncio.create_task(enqueue_request_log_write(request, write))
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            with anyio.move_on_after(STREAM_CLEANUP_TIMEOUT_SECONDS, shield=True):
                await asyncio.shield(task)
        except SpendPersistenceUnavailable:
            pass  # The write already recorded the accounting failure.
        finally:
            if not task.done():
                task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(task, return_exceptions=True)
        raise


async def record_interrupted_stream(request: Request, reason: str) -> None:
    if request_log_already_emitted(request):
        return
    if not isinstance(operation_handle(request), AccountingOperationHandle):
        return
    service = request.app.state.spend_tracking_service
    try:
        await enqueue_request_log_write(
            request,
            service.log_request_failure(
                **billing_write_context(request),
                request_id=request.headers.get("x-request-id"),
                error_type=reason,
            ),
        )
    except SpendPersistenceUnavailable:
        pass  # Preserve the disconnect or deadline as the primary failure.
