"""One signed HTTP call keeps the caller's deadline and payload limits."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Literal

import httpx

from src.billing.accounting_auth import (
    ACCOUNTING_INTERNAL_PREFIX,
    ACCOUNTING_MAX_BODY_BYTES,
    ACCOUNTING_MAX_RESPONSE_BYTES,
    ACCOUNTING_SIGNATURE_HEADER,
    ACCOUNTING_TIMESTAMP_HEADER,
    accounting_signature,
)
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.telemetry_acceptance import AcceptanceFailure

AccountingEndpoint = Literal["/health", "/reserve/compact/batch", "/finalize/local/batch"]
_ENDPOINTS = {"/health", "/reserve/compact/batch", "/finalize/local/batch"}


class AccountingHttpTransport:
    def __init__(
        self,
        *,
        service_url: str,
        signing_secret: str,
        max_connections: int = 64,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = httpx.URL(service_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("accounting URL must contain only an HTTP or HTTPS origin")
        if not 1 <= len(signing_secret) <= 4096:
            raise ValueError("accounting signing secret is missing or exceeds its limit")
        if type(max_connections) is not int or not 1 <= max_connections <= 256:
            raise ValueError("accounting connection capacity is invalid")
        self._secret = signing_secret
        self._closed = False
        self._client = httpx.AsyncClient(
            base_url=str(url),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=min(32, max_connections),
                keepalive_expiry=30,
            ),
        )

    async def close(self) -> None:
        self._closed = True
        await self._client.aclose()

    async def request(
        self, endpoint: AccountingEndpoint, body: bytes, *, expires_at: float
    ) -> bytes:
        if self._closed:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DATABASE_UNAVAILABLE)
        if endpoint not in _ENDPOINTS or len(body) > ACCOUNTING_MAX_BODY_BYTES:
            raise ValueError("accounting request endpoint or byte limit is invalid")
        if endpoint == "/health" and body:
            raise ValueError("accounting health request cannot contain a body")
        remaining = expires_at - asyncio.get_running_loop().time()
        if not math.isfinite(remaining) or remaining <= 0:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)
        path = ACCOUNTING_INTERNAL_PREFIX + endpoint
        timestamp = str(int(time.time()))
        headers = {
            "content-type": "application/json",
            "accept-encoding": "identity",
            ACCOUNTING_TIMESTAMP_HEADER: timestamp,
            ACCOUNTING_SIGNATURE_HEADER: accounting_signature(
                self._secret,
                timestamp=timestamp,
                path=path,
                body=body,
            ),
        }
        try:
            async with asyncio.timeout_at(expires_at):
                async with self._client.stream(
                    "GET" if endpoint == "/health" else "POST",
                    path,
                    content=body,
                    headers=headers,
                    timeout=httpx.Timeout(remaining),
                ) as response:
                    response.raise_for_status()
                    result = await _bounded_response(response)
                    if asyncio.get_running_loop().time() >= expires_at:
                        raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)
                    return result
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, TimeoutError):
            raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE) from None
        except httpx.HTTPError:
            raise AccountingProtocolUnavailable(AcceptanceFailure.CONNECTION) from None


async def _bounded_response(response: httpx.Response) -> bytes:
    if response.headers.get("content-encoding", "identity") != "identity":
        raise AccountingProtocolUnavailable(AcceptanceFailure.INVALID_INPUT)
    length = response.headers.get("content-length")
    if length is not None and (
        len(length) > 10
        or not length.isascii()
        or not length.isdecimal()
        or int(length) > ACCOUNTING_MAX_RESPONSE_BYTES
    ):
        raise AccountingProtocolUnavailable(AcceptanceFailure.INVALID_INPUT)
    parts, size = [], 0
    async for part in response.aiter_bytes(chunk_size=65536):
        size += len(part)
        if size > ACCOUNTING_MAX_RESPONSE_BYTES:
            raise AccountingProtocolUnavailable(AcceptanceFailure.INVALID_INPUT)
        parts.append(part)
    return b"".join(parts)
