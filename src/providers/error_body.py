from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

from src.metrics.counters import increment_provider_error_body_discard

MAX_PROVIDER_ERROR_BODY_BYTES = 65_536
PROVIDER_ERROR_BODY_TRUNCATED_EXTENSION = "deltallm_provider_error_body_truncated"
PROVIDER_ERROR_BODY_OPAQUE_EXTENSION = "deltallm_provider_error_body_opaque"


class _BoundedProviderErrorStream(httpx.AsyncByteStream):
    """Bound wire bytes before httpx can assemble an upstream error body."""

    def __init__(self, response: httpx.Response, stream: httpx.AsyncByteStream) -> None:
        self._response = response
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        remaining = MAX_PROVIDER_ERROR_BODY_BYTES
        async for chunk in self._stream:
            if not chunk:
                continue
            if len(chunk) <= remaining:
                remaining -= len(chunk)
                yield chunk
                continue
            if remaining:
                yield chunk[:remaining]
            self._response.extensions[PROVIDER_ERROR_BODY_TRUNCATED_EXTENSION] = True
            increment_provider_error_body_discard(reason="oversized")
            await self._stream.aclose()
            return

    async def aclose(self) -> None:
        await self._stream.aclose()


def provider_error_body_is_unavailable(response: object | None) -> bool:
    extensions = getattr(response, "extensions", {})
    return bool(
        extensions.get(PROVIDER_ERROR_BODY_TRUNCATED_EXTENSION, False)
        or extensions.get(PROVIDER_ERROR_BODY_OPAQUE_EXTENSION, False)
    )


async def bound_provider_error_response_body(response: httpx.Response) -> None:
    """Bound provider error bytes and never decompress untrusted encoded bodies."""

    if response.status_code < 400 or response.is_closed:
        return
    stream = response.stream
    if not isinstance(stream, httpx.AsyncByteStream):
        return

    content_encoding = response.headers.get("content-encoding", "").strip().lower()
    encodings = {value.strip() for value in content_encoding.split(",") if value.strip()}
    if encodings - {"identity"}:
        # Removing the header before httpx constructs its decoder keeps a compressed
        # error body opaque. Status and Retry-After remain available for routing.
        response.headers.pop("content-encoding", None)
        response.extensions[PROVIDER_ERROR_BODY_OPAQUE_EXTENSION] = True
        increment_provider_error_body_discard(reason="encoded")

    response.stream = _BoundedProviderErrorStream(response, stream)
