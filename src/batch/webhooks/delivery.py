from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

from src.batch.webhooks.network_policy import ResolvedBatchWebhookTarget
from src.outbound.http import close_response_bounded, suppress_httpcore_debug_traces


@dataclass(frozen=True, slots=True)
class BatchWebhookHTTPResponse:
    status_code: int
    retry_after: str | None = field(default=None, repr=False)


class BatchWebhookTransportError(OSError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _transport_error_reason(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, httpx.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, httpx.WriteTimeout):
        return "write_timeout"
    if isinstance(exc, httpx.PoolTimeout):
        return "pool_timeout"
    if isinstance(exc, httpx.ConnectError):
        return "connect_error"
    if isinstance(exc, httpx.ReadError):
        return "read_error"
    if isinstance(exc, httpx.WriteError):
        return "write_error"
    if isinstance(exc, httpx.ProtocolError):
        return "protocol_error"
    return "transport_error"


class BatchWebhookHTTPSender:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport,
        timeout_seconds: float,
        max_response_bytes: int = 65_536,
    ) -> None:
        self.transport = transport
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.max_response_bytes = max(0, int(max_response_bytes))

    async def send(
        self,
        *,
        target: ResolvedBatchWebhookTarget,
        raw_body: bytes,
        headers: dict[str, str],
    ) -> BatchWebhookHTTPResponse:
        deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        request_headers = dict(headers)
        request_headers["Host"] = target.host_header
        extensions: dict[str, object] = {
            "timeout": {
                "connect": self.timeout_seconds,
                "read": self.timeout_seconds,
                "write": self.timeout_seconds,
                "pool": self.timeout_seconds,
            }
        }
        if target.sni_hostname is not None:
            extensions["sni_hostname"] = target.sni_hostname
        request = httpx.Request(
            "POST",
            target.connection_url,
            headers=request_headers,
            content=raw_body,
            extensions=extensions,
        )

        with suppress_httpcore_debug_traces():
            response: httpx.Response | None = None
            result: BatchWebhookHTTPResponse | None = None
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    response = await self.transport.handle_async_request(request)
                    result = BatchWebhookHTTPResponse(
                        status_code=int(response.status_code),
                        retry_after=response.headers.get("Retry-After"),
                    )
                    consumed = 0
                    if not response.is_stream_consumed and self.max_response_bytes > 0:
                        async for chunk in response.aiter_raw():
                            consumed += len(chunk)
                            if consumed >= self.max_response_bytes:
                                break
                    return result
            except TimeoutError:
                # Once response headers exist, the status is the delivery outcome;
                # a slow or endless response body must not turn an acknowledged
                # event into another outbound attempt.
                if result is not None:
                    return result
                raise BatchWebhookTransportError("request_timeout") from None
            except httpx.HTTPError as exc:
                if result is not None:
                    return result
                raise BatchWebhookTransportError(_transport_error_reason(exc)) from None
            finally:
                if response is not None:
                    await close_response_bounded(response, deadline=deadline)
