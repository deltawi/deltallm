from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
import logging
from time import perf_counter
from typing import Literal

import httpx

from src.models.errors import ProxyError
from src.models.responses import ChatCompletionResponse
from src.providers.chat_upstream import ChatUpstream
from src.providers.error_body import provider_error_body_is_unavailable
from src.providers.signing import apply_request_signing
from src.providers.token_receipt import TokenReceiptObserver

logger = logging.getLogger(__name__)
RESPONSE_CLOSE_GRACE_SECONDS = 0.05


class ChatHopFailureCause(StrEnum):
    PROVIDER_ERROR = "provider_error"
    TRANSPORT_ERROR = "transport_error"
    INVALID_RESPONSE = "invalid_response"
    RESPONSE_TOO_LARGE = "response_too_large"
    RESPONSE_ENCODING = "response_encoding"


class ChatHopError(Exception):
    def __init__(self, cause: ChatHopFailureCause) -> None:
        self.cause = cause
        super().__init__("Bounded provider hop failed")


@dataclass(frozen=True, slots=True)
class BoundedChatResponse:
    max_bytes: int = 65_536

    def __post_init__(self) -> None:
        if type(self.max_bytes) is not int or not 1 <= self.max_bytes <= 65_536:
            raise ValueError("invalid bounded chat response limit")


HopPhase = Literal["upstream_http", "upstream_transform"]
HopOutcome = Literal["success", "error"]
HopObserver = Callable[[HopPhase, HopOutcome, float], None]


def _observe(
    observer: HopObserver | None, phase: HopPhase, outcome: HopOutcome, start: float
) -> None:
    if observer is not None:
        observer(phase, outcome, perf_counter() - start)


async def execute_chat_hop(
    *,
    client: httpx.AsyncClient,
    upstream: ChatUpstream,
    params: Mapping[str, object],
    payload: dict[str, object],
    model_name: str,
    timeout: httpx.Timeout,
    observer: HopObserver | None = None,
    bounded: BoundedChatResponse | None = None,
    receipt_observer: TokenReceiptObserver | None = None,
) -> ChatCompletionResponse:
    """Shared single-attempt transport; clients and answer accounting belong to callers."""
    request_url = f"{upstream.api_base}{upstream.endpoint}"
    headers, body = apply_request_signing(
        params=dict(params),
        method="POST",
        url=request_url,
        headers=dict(upstream.headers),
        json_body=payload,
    )
    started = perf_counter()
    try:
        response = await _send(
            client,
            request_url,
            headers=headers,
            body=body,
            payload=payload,
            timeout=timeout,
            bounded=bounded,
        )
    except Exception:
        _observe(observer, "upstream_http", "error", started)
        raise
    _observe(
        observer, "upstream_http", "error" if response.status_code >= 400 else "success", started
    )
    if response.status_code >= 400:
        error = httpx.HTTPStatusError(
            f"Upstream chat call failed with status {response.status_code}",
            request=httpx.Request("POST", request_url),
            response=response,
        )
        if bounded is not None:
            # Classify at this provider boundary; never retain an upstream exception/body.
            upstream.adapter.map_error(error)
            raise ChatHopError(ChatHopFailureCause.PROVIDER_ERROR) from None
        raise upstream.adapter.map_error(error)
    if bounded is not None and response.status_code >= 300:
        raise ChatHopError(ChatHopFailureCause.INVALID_RESPONSE)
    started = perf_counter()
    try:
        if bounded is not None:
            canonical = await upstream.adapter.translate_single_success_response(
                response, model_name, receipt_observer=receipt_observer
            )
        else:
            canonical = await upstream.adapter.translate_success_response(response, model_name)
    except ProxyError:
        if bounded is not None:
            raise ChatHopError(ChatHopFailureCause.INVALID_RESPONSE) from None
        raise
    _observe(observer, "upstream_transform", "success", started)
    return canonical


async def _send(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None,
    payload: dict[str, object],
    timeout: httpx.Timeout,
    bounded: BoundedChatResponse | None,
) -> httpx.Response:
    if bounded is not None:
        try:
            return await _send_bounded(
                client,
                url,
                headers=headers,
                body=body,
                payload=payload,
                timeout=timeout,
                bound=bounded,
            )
        except httpx.TimeoutException:
            raise ChatHopError(ChatHopFailureCause.TRANSPORT_ERROR) from None
        except httpx.HTTPError:
            raise ChatHopError(ChatHopFailureCause.TRANSPORT_ERROR) from None
    if body is not None:
        return await client.post(url, headers=headers, content=body, timeout=timeout)
    return await client.post(url, headers=headers, json=payload, timeout=timeout)


async def _send_bounded(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None,
    payload: dict[str, object],
    timeout: httpx.Timeout,
    bound: BoundedChatResponse,
) -> httpx.Response:
    headers = {**headers, "Accept-Encoding": "identity"}
    request = client.build_request(
        "POST",
        url,
        headers=headers,
        content=body,
        json=payload if body is None else None,
        timeout=timeout,
    )
    response = await client.send(request, stream=True, follow_redirects=False)
    try:
        return await _read_bounded(response, bound)
    except BaseException:
        # Preserve the primary failure if cleanup raises an ordinary error, but let
        # cancellation arriving during cleanup abort the operation instead of defaulting.
        try:
            await _close_bounded_response(response)
        except Exception:
            logger.warning("bounded_chat_response_cleanup_incomplete")
        raise


async def _read_bounded(response: httpx.Response, bound: BoundedChatResponse) -> httpx.Response:
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding not in ("", "identity") or provider_error_body_is_unavailable(response):
        raise ChatHopError(ChatHopFailureCause.RESPONSE_ENCODING)
    length = response.headers.get("content-length")
    if length is not None and length.isdecimal() and len(length) < 20:
        if int(length) > bound.max_bytes:
            raise ChatHopError(ChatHopFailureCause.RESPONSE_TOO_LARGE)
    data = bytearray()
    # Iterate the undecoded stream directly; aiter_raw would close on exhaustion,
    # outside our explicit bounded cleanup ownership.
    if response.is_stream_consumed:
        if len(response.content) > bound.max_bytes:
            raise ChatHopError(ChatHopFailureCause.RESPONSE_TOO_LARGE)
        data.extend(response.content)
    else:
        async for chunk in response.stream:
            if len(chunk) > bound.max_bytes - len(data):
                raise ChatHopError(ChatHopFailureCause.RESPONSE_TOO_LARGE)
            data.extend(chunk)
    if provider_error_body_is_unavailable(response):
        raise ChatHopError(ChatHopFailureCause.RESPONSE_TOO_LARGE)
    await _close_bounded_response(response)
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        content=bytes(data),
        request=response.request,
    )


async def _close_bounded_response(response: httpx.Response) -> None:
    try:
        async with asyncio.timeout(RESPONSE_CLOSE_GRACE_SECONDS):
            await response.aclose()
    except Exception:
        # The response owner classifies cleanup failures; it never owns the client.
        logger.warning("bounded_chat_response_cleanup_failed")
        raise ChatHopError(ChatHopFailureCause.TRANSPORT_ERROR) from None
