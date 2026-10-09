"""Private signed persistence routes are registered only by the request role."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response

from src.billing.accounting.transport.accounting_auth import (
    ACCOUNTING_INTERNAL_PREFIX,
    ACCOUNTING_MAX_BODY_BYTES,
    ACCOUNTING_SIGNATURE_HEADER,
    ACCOUNTING_TIMESTAMP_HEADER,
    verify_accounting_signature,
)
from src.billing.accounting.transport.accounting_rpc_contracts import (
    LocalFundingRequest,
    LocalReturnRequest,
    LocalTerminalRequest,
)
from src.billing.accounting.transport.accounting_rpc_service import AccountingRpcService
from src.billing.accounting.durable_microbatch import DurableBatchClosed, DurableBatchFull
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.metrics.accounting import increment_accounting_failure


def accounting_rpc_router(service: AccountingRpcService, *, signing_secret: str) -> APIRouter:
    if not 1 <= len(signing_secret) <= 4096:
        raise ValueError("accounting signing secret is missing or exceeds its limit")
    router = APIRouter(prefix=ACCOUNTING_INTERNAL_PREFIX, include_in_schema=False)

    @router.get("/health")
    async def health(request: Request) -> Response:
        try:
            async with asyncio.timeout(0.5):
                body = await _authenticated_body(request, signing_secret)
                if body:
                    raise HTTPException(400, "Invalid accounting request")
                value = service.health()
                return Response(value.model_dump_json(), media_type="application/json")
        except (AccountingProtocolUnavailable, TimeoutError):
            raise HTTPException(503, "Accounting unavailable") from None

    async def execute(
        request: Request, action: Literal["allocate", "return", "finalize"]
    ) -> Response:
        received_at = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout_at(received_at + 5):
                body = await _authenticated_body(request, signing_secret)
                if action == "allocate":
                    value = LocalFundingRequest.model_validate_json(body)
                    deadline = received_at + value.budget_ms / 1000
                    async with asyncio.timeout_at(deadline):
                        result = await service.allocate(value, expires_at=deadline)
                elif action == "return":
                    value = LocalReturnRequest.model_validate_json(body)
                    deadline = received_at + value.budget_ms / 1000
                    async with asyncio.timeout_at(deadline):
                        result = await service.return_suffixes(value, expires_at=deadline)
                else:
                    value = LocalTerminalRequest.model_validate_json(body)
                    deadline = received_at + value.budget_ms / 1000
                    result = await service.finalize(value, expires_at=deadline)
                return Response(result, media_type="application/json")
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(400, "Invalid accounting request") from None
        except (AccountingProtocolUnavailable, DurableBatchClosed, DurableBatchFull, TimeoutError):
            raise HTTPException(503, "Accounting unavailable") from None
        except Exception:
            increment_accounting_failure("transport", "server", "unknown")
            raise HTTPException(503, "Accounting unavailable") from None

    @router.post("/allocate/local/batch")
    async def allocate(request: Request) -> Response:
        return await execute(request, "allocate")

    @router.post("/return/local/batch")
    async def return_suffixes(request: Request) -> Response:
        return await execute(request, "return")

    @router.post("/finalize/local/batch")
    async def finalize(request: Request) -> Response:
        return await execute(request, "finalize")

    return router


async def _authenticated_body(request: Request, signing_secret: str) -> bytes:
    if request.headers.get("content-encoding", "identity") != "identity":
        raise HTTPException(400, "Invalid accounting request")
    length = request.headers.get("content-length")
    if length is not None and (
        len(length) > 10
        or not length.isascii()
        or not length.isdecimal()
        or int(length) > ACCOUNTING_MAX_BODY_BYTES
    ):
        raise HTTPException(413, "Accounting request is too large")
    retained = bytearray()
    async for part in request.stream():
        if len(retained) + len(part) > ACCOUNTING_MAX_BODY_BYTES:
            raise HTTPException(413, "Accounting request is too large")
        retained.extend(part)
    body = bytes(retained)
    if not verify_accounting_signature(
        signing_secret,
        timestamp=request.headers.get(ACCOUNTING_TIMESTAMP_HEADER, ""),
        signature=request.headers.get(ACCOUNTING_SIGNATURE_HEADER, ""),
        path=request.url.path,
        body=body,
    ):
        raise HTTPException(401, "Invalid accounting signature")
    return body
