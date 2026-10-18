"""Bounded outbound HTTP for OAuth discovery, registration, token and revocation calls.

Every call resolves the host through the shared outbound network policy, connects
to the checked address (DNS rebinding cannot move it), sends no redirects, reads
at most ``max_response_bytes``, and stops at one total deadline.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.outbound.http import close_response_bounded, suppress_httpcore_debug_traces
from src.outbound.network_policy import (
    OutboundNetworkPolicy,
    OutboundPolicyError,
    OutboundResolutionError,
)

from .errors import MCPOAuthError

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024


@dataclass(frozen=True)
class OAuthHTTPResponse:
    status_code: int
    headers: httpx.Headers = field(repr=False)
    body: bytes = field(repr=False)

    def json_object(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            return None
        return payload if isinstance(payload, dict) else None


@dataclass(frozen=True)
class MCPOAuthHTTP:
    """Borrows the control transport; bootstrap's client owns and closes it."""

    transport: httpx.AsyncBaseTransport = field(repr=False)
    policy: OutboundNetworkPolicy = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    gate: BoundedCapacityGate = field(
        default_factory=lambda: BoundedCapacityGate(concurrency=16, max_waiters=32), repr=False
    )

    @property
    def allow_http(self) -> bool:
        return self.policy.allow_http

    async def get(self, url: str, *, headers: dict[str, str] | None = None) -> OAuthHTTPResponse:
        return await self.request("GET", url, headers=headers)

    async def post_form(
        self,
        url: str,
        form: dict[str, str],
        *,
        headers: dict[str, str] | None = None,
    ) -> OAuthHTTPResponse:
        return await self.request(
            "POST",
            url,
            headers={**(headers or {}), "Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode(form).encode("ascii"),
        )

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> OAuthHTTPResponse:
        return await self.request(
            "POST",
            url,
            headers={**(headers or {}), "Content-Type": "application/json"},
            body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        )

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> OAuthHTTPResponse:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        acquired = False
        response: httpx.Response | None = None
        try:
            with suppress_httpcore_debug_traces():
                try:
                    async with asyncio.timeout_at(deadline):
                        await self.gate.acquire(timeout_seconds=0.5)
                        acquired = True
                        target = await self.policy.resolve(url)
                        remaining = max(0.001, deadline - loop.time())
                        request = httpx.Request(
                            method,
                            target.connection_url,
                            headers={
                                **(headers or {}),
                                "Host": target.host_header,
                                "Accept": (headers or {}).get("Accept", "application/json"),
                                "Accept-Encoding": "identity",
                                "User-Agent": "DeltaLLM-MCP-OAuth",
                            },
                            content=body,
                            extensions={
                                "sni_hostname": target.sni_hostname,
                                "timeout": {
                                    "connect": remaining,
                                    "read": remaining,
                                    "write": remaining,
                                    "pool": remaining,
                                },
                            },
                        )
                        response = await self.transport.handle_async_request(request)
                        response.request = request
                        payload = await self._read_bounded(response)
                        return OAuthHTTPResponse(
                            status_code=response.status_code,
                            headers=response.headers,
                            body=payload,
                        )
                finally:
                    if response is not None:
                        await close_response_bounded(response, deadline=deadline)
        except OutboundPolicyError as exc:
            raise MCPOAuthError(
                f"The OAuth endpoint address is not allowed ({exc.reason})"
            ) from None
        except OutboundResolutionError:
            raise MCPOAuthError("The OAuth endpoint host name could not be resolved") from None
        except (CapacityGateFull, CapacityGateTimedOut, httpx.PoolTimeout):
            raise MCPOAuthError("Too many OAuth requests are in progress. Try again.") from None
        except (httpx.TimeoutException, TimeoutError):
            raise MCPOAuthError("The OAuth endpoint did not respond in time") from None
        except httpx.HTTPError:
            raise MCPOAuthError("The OAuth endpoint could not be reached") from None
        finally:
            if acquired:
                await self.gate.release()

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        encoding = response.headers.get("content-encoding", "identity").lower()
        if encoding not in {"identity", ""}:
            raise MCPOAuthError("The OAuth endpoint returned an unsupported encoding")
        body = bytearray()
        async for chunk in response.stream:
            if len(body) + len(chunk) > self.max_response_bytes:
                raise MCPOAuthError("The OAuth endpoint response is too large")
            body.extend(chunk)
        return bytes(body)
