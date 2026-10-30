"""Bounded, policy-checked POSTs to operator-configured alert destinations."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
from pydantic import SecretStr

from src.concurrency import BoundedCapacityGate, CapacityGateFull, CapacityGateTimedOut
from src.notifications.webhook import WebhookResult
from src.outbound.http import close_response_bounded, suppress_httpcore_debug_traces
from src.outbound.network_policy import (
    OutboundNetworkPolicy,
    OutboundPolicyError,
    OutboundResolutionError,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(slots=True)
class AlertDestinationHTTP:
    """Borrows the control transport; bootstrap's client owns and closes it.

    Every call resolves the URL through the outbound policy (scheme, port,
    private and metadata addresses), pins the checked address, never follows
    a redirect, and reads at most ``max_response_bytes``. A call never raises.
    """

    transport: httpx.AsyncBaseTransport = field(repr=False)
    policy: OutboundNetworkPolicy = field(repr=False)
    # Two attempts fit in the 5 s budget the budget-alert worker gives one dispatch.
    timeout_seconds: float = 2.0
    max_response_bytes: int = 4096
    gate: BoundedCapacityGate = field(
        default_factory=lambda: BoundedCapacityGate(concurrency=8, max_waiters=32), repr=False
    )

    async def post(
        self,
        *,
        url: SecretStr,
        body: bytes,
        headers: dict[str, str],
        attempts: int = 2,
    ) -> WebhookResult:
        last = WebhookResult(ok=False, error="not_attempted")
        for attempt in range(1, max(1, attempts) + 1):
            last = await self._post_once(url=url, body=body, headers=headers, attempt=attempt)
            retryable = last.error in {"transport_error", "timeout"} or (
                last.status_code in _RETRYABLE_STATUS
            )
            if last.ok or not retryable:
                return last
        return last

    async def _post_once(
        self, *, url: SecretStr, body: bytes, headers: dict[str, str], attempt: int
    ) -> WebhookResult:
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
                        target = await self.policy.resolve(
                            url.get_secret_value(), attempt_count=attempt
                        )
                        remaining = max(0.001, deadline - loop.time())
                        request = httpx.Request(
                            "POST",
                            target.connection_url,
                            headers={
                                **headers,
                                "Host": target.host_header,
                                "Accept-Encoding": "identity",
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
                        status = int(response.status_code)
                        # The body is not used; read a bounded part so the
                        # connection can return to the pool.
                        consumed = 0
                        async for chunk in response.stream:
                            consumed += len(chunk)
                            if consumed >= self.max_response_bytes:
                                break
                finally:
                    if response is not None:
                        await close_response_bounded(response, deadline=deadline)
        except OutboundPolicyError as exc:
            return WebhookResult(ok=False, error=f"policy_{exc.reason}")
        except OutboundResolutionError as exc:
            return WebhookResult(ok=False, error=exc.reason)
        except (CapacityGateFull, CapacityGateTimedOut):
            return WebhookResult(ok=False, error="capacity_full")
        except TimeoutError:
            return WebhookResult(ok=False, error="timeout")
        except httpx.HTTPError:
            # The URL is a secret: log only the error class.
            logger.warning("alert destination post failed", extra={"attempt": attempt})
            return WebhookResult(ok=False, error="transport_error")
        finally:
            if acquired:
                await self.gate.release()
        # A redirect is a failure: the target is not where the operator configured it.
        ok = 200 <= status < 300
        return WebhookResult(ok=ok, status_code=status, error=None if ok else f"http_{status}")
