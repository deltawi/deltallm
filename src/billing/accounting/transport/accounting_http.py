"""One signed HTTP call keeps the caller's deadline and payload limits."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from typing import Literal

import httpx

from src.billing.accounting.transport.accounting_auth import (
    ACCOUNTING_INTERNAL_PREFIX,
    ACCOUNTING_MAX_BODY_BYTES,
    ACCOUNTING_MAX_RESPONSE_BYTES,
    ACCOUNTING_SIGNATURE_HEADER,
    ACCOUNTING_TIMESTAMP_HEADER,
    accounting_signature,
)
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from src.db.runtime.telemetry_acceptance import AcceptanceFailure
from src.ingress import DRAINING_CODE
from src.metrics.accounting import accounting_drain_retries
from src.outbound.http import suppress_httpcore_debug_traces
from src.outbound.network_policy import (
    OutboundNetworkPolicy,
    OutboundPolicyError,
    OutboundResolutionError,
)

logger = logging.getLogger(__name__)

# The managed server closes idle HTTP connections after five seconds.
ACCOUNTING_HTTP_KEEPALIVE_EXPIRY_SECONDS = 4.0
# A rollout drains accounting request pods one at a time; two more sends
# inside the caller's deadline reach a serving pod.
ACCOUNTING_DRAIN_RETRY_ATTEMPTS = 3
ACCOUNTING_DRAIN_RETRY_PAUSE_SECONDS = 0.05
DRAINING_REPLY_MAX_BYTES = 4096


AccountingEndpoint = Literal[
    "/health",
    "/reserve/compact/batch",
    "/finalize/local/batch",
    "/allocate/local/batch",
    "/return/local/batch",
]
_ENDPOINTS = {
    "/health",
    "/reserve/compact/batch",
    "/finalize/local/batch",
    "/allocate/local/batch",
    "/return/local/batch",
}


class AccountingHttpTransport:
    def __init__(
        self,
        *,
        service_url: str,
        signing_secret: str,
        max_connections: int = 64,
        transport: httpx.AsyncBaseTransport | None = None,
        network_policy: OutboundNetworkPolicy | None = None,
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
        self._network_policy = network_policy or OutboundNetworkPolicy()
        self._client = httpx.AsyncClient(
            base_url=str(url),
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=min(32, max_connections),
                keepalive_expiry=ACCOUNTING_HTTP_KEEPALIVE_EXPIRY_SECONDS,
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
            with suppress_httpcore_debug_traces():
                return await self._request_with_drain_retry(path, body, headers, expires_at)
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, TimeoutError):
            logger.warning(
                "Accounting transport failed; endpoint=%s kind=deadline status=none", endpoint
            )
            raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE) from None
        except OutboundPolicyError:
            raise AccountingProtocolUnavailable(AcceptanceFailure.INVALID_INPUT) from None
        except (httpx.HTTPError, OutboundResolutionError) as error:
            kind = "dns" if isinstance(error, OutboundResolutionError) else "http_transport"
            status: int | str = "none"
            if isinstance(error, httpx.HTTPStatusError):
                kind = "http_status"
                code = error.response.status_code
                status = code if 100 <= code <= 599 else "unknown"
            # Only fixed fields enter the log. Never log bodies, signatures, or URLs.
            logger.warning(
                "Accounting transport failed; endpoint=%s kind=%s status=%s", endpoint, kind, status
            )
            raise AccountingProtocolUnavailable(AcceptanceFailure.CONNECTION) from None

    async def _request_with_drain_retry(
        self, path: str, body: bytes, headers: dict[str, str], expires_at: float
    ) -> bytes:
        """Send again only when a draining pod refused the call before running it.

        The draining reply is written by ingress admission before the request
        reaches any handler, so the call did nothing and a second send cannot
        repeat work. The reply closes its connection, so the next send opens a
        connection to a pod that is still serving. Every other failure, including
        a timeout or a reset after the body was sent, stays a single attempt.
        """
        for attempt in range(1, ACCOUNTING_DRAIN_RETRY_ATTEMPTS + 1):
            remaining = expires_at - asyncio.get_running_loop().time()
            try:
                return await self._request_target(path, body, headers, remaining, expires_at)
            except _DrainingReject as reject:
                pause = ACCOUNTING_DRAIN_RETRY_PAUSE_SECONDS * attempt
                if (
                    attempt == ACCOUNTING_DRAIN_RETRY_ATTEMPTS
                    or expires_at - asyncio.get_running_loop().time() <= pause
                ):
                    raise reject.error from None
                accounting_drain_retries.inc()
                await asyncio.sleep(pause)
        raise AssertionError("unreachable")

    async def _request_target(
        self, path: str, body: bytes, headers: dict[str, str], remaining: float, expires_at: float
    ) -> bytes:
        async with asyncio.timeout_at(expires_at):
            target = await self._network_policy.resolve(str(self._client.base_url.join(path)))
            headers["Host"] = target.host_header
            extensions = (
                {} if target.sni_hostname is None else {"sni_hostname": target.sni_hostname}
            )
            async with self._client.stream(
                "GET" if path == ACCOUNTING_INTERNAL_PREFIX + "/health" else "POST",
                target.connection_url,
                content=body,
                headers=headers,
                timeout=httpx.Timeout(remaining),
                extensions=extensions,
            ) as response:
                if response.status_code == 503 and await _is_draining_reject(response):
                    raise _DrainingReject(
                        httpx.HTTPStatusError(
                            "draining", request=response.request, response=response
                        )
                    )
                response.raise_for_status()
                result = await _bounded_response(response)
                if asyncio.get_running_loop().time() >= expires_at:
                    raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)
                return result


class _DrainingReject(Exception):
    def __init__(self, error: httpx.HTTPStatusError) -> None:
        super().__init__("accounting pod is draining")
        self.error = error


async def _is_draining_reject(response: httpx.Response) -> bool:
    """True only for the ingress admission reply of a draining pod."""
    try:
        reply = json.loads(await _bounded_response(response, limit=DRAINING_REPLY_MAX_BYTES))
    except (AccountingProtocolUnavailable, ValueError):
        return False
    error = reply.get("error") if isinstance(reply, dict) else None
    return isinstance(error, dict) and error.get("code") == DRAINING_CODE


async def _bounded_response(
    response: httpx.Response, *, limit: int = ACCOUNTING_MAX_RESPONSE_BYTES
) -> bytes:
    if response.headers.get("content-encoding", "identity") != "identity":
        raise AccountingProtocolUnavailable(AcceptanceFailure.INVALID_INPUT)
    length = response.headers.get("content-length")
    if length is not None and (
        len(length) > 10 or not length.isascii() or not length.isdecimal() or int(length) > limit
    ):
        raise AccountingProtocolUnavailable(AcceptanceFailure.INVALID_INPUT)
    parts, size = [], 0
    async for part in response.aiter_bytes(chunk_size=65536):
        size += len(part)
        if size > limit:
            raise AccountingProtocolUnavailable(AcceptanceFailure.INVALID_INPUT)
        parts.append(part)
    return b"".join(parts)
