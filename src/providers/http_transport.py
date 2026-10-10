"""Keep HTTPX contracts while using the maintained provider connection pool."""

from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Iterator
from contextlib import contextmanager

import httpcore2
import httpx


# Specific exceptions must precede their parents. Provider/router code continues
# to distinguish local PoolTimeout from remote read/connect/protocol failures.
_ERROR_TYPES: tuple[tuple[type[Exception], type[httpx.RequestError]], ...] = (
    (httpcore2.ConnectTimeout, httpx.ConnectTimeout),
    (httpcore2.ReadTimeout, httpx.ReadTimeout),
    (httpcore2.WriteTimeout, httpx.WriteTimeout),
    (httpcore2.PoolTimeout, httpx.PoolTimeout),
    (httpcore2.TimeoutException, httpx.TimeoutException),
    (httpcore2.ConnectError, httpx.ConnectError),
    (httpcore2.ReadError, httpx.ReadError),
    (httpcore2.WriteError, httpx.WriteError),
    (httpcore2.NetworkError, httpx.NetworkError),
    (httpcore2.ProxyError, httpx.ProxyError),
    (httpcore2.UnsupportedProtocol, httpx.UnsupportedProtocol),
    (httpcore2.LocalProtocolError, httpx.LocalProtocolError),
    (httpcore2.RemoteProtocolError, httpx.RemoteProtocolError),
    (httpcore2.ProtocolError, httpx.ProtocolError),
)


@contextmanager
def _httpx_errors(request: httpx.Request | None = None) -> Iterator[None]:
    try:
        yield
    except Exception as exc:
        for core_type, httpx_type in _ERROR_TYPES:
            if isinstance(exc, core_type):
                raise httpx_type(str(exc), request=request) from exc
        raise


def _core_url(url: httpx.URL) -> httpcore2.URL:
    return httpcore2.URL(
        scheme=url.raw_scheme, host=url.raw_host, port=url.port, target=url.raw_path
    )


class _ResponseStream(httpx.AsyncByteStream):
    def __init__(self, response: httpcore2.Response, request: httpx.Request) -> None:
        if not isinstance(response.stream, AsyncIterable):
            raise TypeError("Provider responses require an async byte stream")
        self._response = response
        self._stream = response.stream
        self._request = request

    async def __aiter__(self) -> AsyncIterator[bytes]:
        with _httpx_errors(self._request):
            # HTTPX already owns body consumption and calls aclose on completion
            # or failure. Forward the public core stream without another response
            # consumption layer; the core stream owns its iterator cleanup.
            async for chunk in self._stream:
                yield chunk

    async def aclose(self) -> None:
        with _httpx_errors(self._request):
            await self._response.aclose()


class UpstreamHTTPTransport(httpx.AsyncBaseTransport):
    """One bounded pool per direct/proxy mount, owned by the shared client.

    Pool assignment, reservation, expiry and cancellation belong to httpcore2.
    This adapter only translates public request, response and exception types.
    """

    def __init__(self, *, limits: httpx.Limits, proxy: str | None = None) -> None:
        ssl_context = httpx.create_ssl_context()
        core_proxy = None
        if proxy is not None:
            parsed = httpx.Proxy(proxy)
            if parsed.url.scheme in ("socks5", "socks5h"):
                # Match HTTPX's startup failure for an unavailable optional extra.
                import socksio  # noqa: F401
            core_proxy = httpcore2.Proxy(
                url=_core_url(parsed.url),
                auth=parsed.raw_auth,
                headers=parsed.headers.raw,
                ssl_context=parsed.ssl_context or ssl_context,
            )
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=ssl_context,
            proxy=core_proxy,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not isinstance(request.stream, httpx.AsyncByteStream):
            raise TypeError("Provider requests require an async byte stream")
        core_request = httpcore2.Request(
            method=request.method,
            url=_core_url(request.url),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        with _httpx_errors(request):
            response = await self._pool.handle_async_request(core_request)
        return httpx.Response(
            status_code=response.status,
            headers=response.headers,
            stream=_ResponseStream(response, request),
            extensions=response.extensions,
        )

    async def aclose(self) -> None:
        with _httpx_errors():
            await self._pool.aclose()
