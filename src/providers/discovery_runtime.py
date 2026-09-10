from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.metrics.provider_discovery import DiscoveryOutcome, record_discovery
from src.outbound.http import close_response_bounded, suppress_httpcore_debug_traces
from src.outbound.network_policy import (
    OutboundNetworkPolicy,
    OutboundPolicyError,
    OutboundResolutionError,
)
from src.outbound.urls import normalize_outbound_url
from src.upstream_http import HEALTH_CHECK_POOL_TIMEOUT_RATIO

MAX_DISCOVERY_BYTES = 2_097_152


class DiscoveryUnavailable(ValueError):
    """Safe local policy/capacity failure; never penalize deployment health."""


def validated_discovery_base(api_base: str) -> str:
    try:
        base = normalize_outbound_url(api_base)
        if urlsplit(base).query or "?" in base:
            raise ValueError("API base query")
        return base.rstrip("/")
    except ValueError:
        raise DiscoveryUnavailable("Provider discovery destination is not allowed") from None


async def _read_bounded(response: httpx.Response) -> bytes:
    if response.is_stream_consumed:
        if len(response.content) > MAX_DISCOVERY_BYTES:
            raise ValueError("Provider model discovery exceeded its response size limit")
        return response.content
    encoding = response.headers.get("content-encoding", "identity").lower()
    if encoding not in {"identity", "gzip", "deflate"}:
        raise ValueError("Provider model discovery returned an unsupported encoding")
    decoder = (
        None if encoding == "identity" else zlib.decompressobj(31 if encoding == "gzip" else 15)
    )
    body = bytearray()
    # Iterate the raw transport stream: HTTPX's iterator implicitly closes at EOF,
    # which would put unbounded cleanup inside the success decision.
    async for raw in response.stream:
        chunk = (
            raw if decoder is None else decoder.decompress(raw, MAX_DISCOVERY_BYTES - len(body) + 1)
        )
        if len(body) + len(chunk) > MAX_DISCOVERY_BYTES:
            raise ValueError("Provider model discovery exceeded its response size limit")
        body.extend(chunk)
    if decoder is not None and (not decoder.eof or decoder.unused_data):
        raise ValueError("Provider model discovery returned an invalid encoding")
    return bytes(body)


@dataclass(frozen=True, slots=True)
class ProviderDiscoveryRuntime:
    """Borrows the control transport; bootstrap's client is its sole closer."""

    transport: httpx.AsyncBaseTransport = field(repr=False)
    policy: OutboundNetworkPolicy = field(repr=False)
    gate: BoundedCapacityGate = field(
        default_factory=lambda: BoundedCapacityGate(concurrency=32, max_waiters=32), repr=False
    )

    async def fetch(self, url: str, *, api_key: str, timeout: httpx.Timeout) -> bytes:
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + min(float(timeout.read or 10.0), 10.0)
        acquired = False
        response: httpx.Response | None = None
        outcome = DiscoveryOutcome.FAILURE
        try:
            with suppress_httpcore_debug_traces():
                try:
                    async with asyncio.timeout_at(deadline):
                        await self.gate.acquire(timeout_seconds=0.1)
                        acquired = True
                        target = await self.policy.resolve(url)
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            raise TimeoutError
                        request_timeouts = {
                            name: min(value if value is not None else remaining, remaining)
                            for name, value in timeout.as_dict().items()
                        }
                        # Preserve the health-check headroom after admission and DNS:
                        # pool exhaustion must surface before the total deadline.
                        request_timeouts["pool"] = min(
                            request_timeouts["pool"], remaining * HEALTH_CHECK_POOL_TIMEOUT_RATIO
                        )
                        request = httpx.Request(
                            "GET",
                            target.connection_url,
                            headers={
                                "Host": target.host_header,
                                "Authorization": f"Bearer {api_key}",
                                "Accept-Encoding": "identity",
                            },
                            extensions={
                                "sni_hostname": target.sni_hostname,
                                "timeout": request_timeouts,
                            },
                        )
                        response = await self.transport.handle_async_request(request)
                        response.request = request
                        if not 200 <= response.status_code < 300:
                            raise httpx.HTTPStatusError(
                                f"Provider model discovery returned {response.status_code}",
                                request=request,
                                response=response,
                            )
                        body = await _read_bounded(response)
                        outcome = DiscoveryOutcome.SUCCESS
                        return body
                finally:
                    if response is not None:
                        await close_response_bounded(response, deadline=deadline)
        except OutboundPolicyError:
            outcome = DiscoveryOutcome.POLICY
            raise DiscoveryUnavailable("Provider discovery destination is not allowed") from None
        except (CapacityGateFull, CapacityGateTimedOut, httpx.PoolTimeout):
            outcome = DiscoveryOutcome.CAPACITY
            raise DiscoveryUnavailable("Provider discovery capacity exceeded") from None
        except OutboundResolutionError:
            outcome = DiscoveryOutcome.DNS
            raise ValueError("Provider discovery DNS resolution failed") from None
        except (httpx.TimeoutException, TimeoutError):
            outcome = DiscoveryOutcome.TIMEOUT
            raise
        except asyncio.CancelledError:
            outcome = DiscoveryOutcome.CANCELLED
            raise
        except zlib.error:
            raise ValueError("Provider model discovery returned an invalid encoding") from None
        finally:
            if acquired:
                await self.gate.release()
            record_discovery(outcome, loop.time() - started)
