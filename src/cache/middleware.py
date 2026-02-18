from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .backends.base import CacheBackend, CacheEntry
from .key_builder import CacheKeyBuilder
from .metrics import CacheMetricsProtocol, NoopCacheMetrics

logger = logging.getLogger(__name__)


class CacheControl(str, Enum):
    DEFAULT = "default"
    NO_CACHE = "no-cache"
    NO_STORE = "no-store"
    BYPASS = "bypass"


@dataclass
class CacheOptions:
    control: CacheControl = CacheControl.DEFAULT
    ttl: int | None = None
    custom_key: str | None = None


@dataclass
class CacheContext:
    cache_key: str
    options: CacheOptions
    model: str


def parse_cache_options(request_data: dict[str, Any], headers: dict[str, str]) -> CacheOptions:
    options = CacheOptions()

    cache_control = headers.get("cache-control", "").lower()
    if "no-cache" in cache_control:
        options.control = CacheControl.NO_CACHE
    if "no-store" in cache_control:
        options.control = CacheControl.NO_STORE

    ttl_header = headers.get("cache-ttl")
    if ttl_header:
        try:
            options.ttl = int(ttl_header)
        except ValueError:
            options.ttl = None

    metadata = request_data.get("metadata") or {}
    if isinstance(metadata, dict):
        cache_ttl = metadata.get("cache_ttl")
        if isinstance(cache_ttl, int):
            options.ttl = cache_ttl

        custom_key = metadata.get("cache_key")
        if isinstance(custom_key, str) and custom_key.strip():
            options.custom_key = custom_key.strip()

        cache_setting = metadata.get("cache")
        if cache_setting is False:
            options.control = CacheControl.BYPASS
        elif cache_setting == "no-cache":
            options.control = CacheControl.NO_CACHE
        elif cache_setting == "no-store":
            options.control = CacheControl.NO_STORE

    return options


class CacheMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        default_ttl: int = 3600,
        enabled_endpoints: set[str] | None = None,
    ) -> None:
        super().__init__(app)
        self.default_ttl = default_ttl
        self.enabled_endpoints = enabled_endpoints or {
            "/v1/chat/completions",
            "/v1/embeddings",
        }

    async def dispatch(self, request: Request, call_next):
        backend: CacheBackend | None = getattr(request.app.state, "cache_backend", None)
        key_builder: CacheKeyBuilder | None = getattr(request.app.state, "cache_key_builder", None)
        metrics: CacheMetricsProtocol = getattr(request.app.state, "cache_metrics", NoopCacheMetrics())
        streaming_handler = getattr(request.app.state, "streaming_cache_handler", None)

        if backend is None or key_builder is None or not self._should_cache(request):
            return await call_next(request)

        request_data = await self._read_request_data(request)
        if not request_data:
            return await call_next(request)

        cache_options = parse_cache_options(request_data, self._normalized_headers(request))
        model = str(request_data.get("model") or "unknown")
        endpoint = request.url.path

        if cache_options.control == CacheControl.BYPASS:
            response = await call_next(request)
            response.headers["x-litellm-cache-hit"] = "false"
            return response

        cache_key = key_builder.build_key_from_payload(request_data, cache_options.custom_key)
        request.state.cache_context = CacheContext(cache_key=cache_key, options=cache_options, model=model)

        if bool(request_data.get("stream")) and streaming_handler is not None:
            if cache_options.control != CacheControl.NO_CACHE:
                cached = await backend.get(cache_key)
                if cached is not None:
                    metrics.hit(endpoint=endpoint, model=model)
                    return StreamingResponse(
                        streaming_handler.reconstruct_sse_stream(cached.response),
                        media_type="text/event-stream",
                        headers={
                            "Cache-Control": "no-cache",
                            "Connection": "keep-alive",
                            "x-litellm-cache-hit": "true",
                        },
                    )
                metrics.miss(endpoint=endpoint, model=model)

            request.state.cache_context = CacheContext(cache_key=cache_key, options=cache_options, model=model)
            response = await call_next(request)
            response.headers["x-litellm-cache-hit"] = "false"
            return response

        if cache_options.control != CacheControl.NO_CACHE:
            cached_entry = await backend.get(cache_key)
            if cached_entry is not None:
                metrics.hit(endpoint=endpoint, model=model)
                return self._cached_json_response(cached_entry.response, cache_key)
            metrics.miss(endpoint=endpoint, model=model)

        response = await call_next(request)
        response.headers["x-litellm-cache-hit"] = "false"

        if cache_options.control == CacheControl.NO_STORE:
            return response

        response, response_data = await self._materialize_response(response)
        await self._maybe_store(
            backend=backend,
            response=response,
            response_data=response_data,
            cache_key=cache_key,
            ttl=cache_options.ttl or self.default_ttl,
            model=model,
            metrics=metrics,
            endpoint=endpoint,
        )
        return response

    def _should_cache(self, request: Request) -> bool:
        return request.method.upper() == "POST" and request.url.path in self.enabled_endpoints

    async def _read_request_data(self, request: Request) -> dict[str, Any] | None:
        if hasattr(request.state, "request_data"):
            return request.state.request_data

        body = await request.body()
        request._body = body  # noqa: SLF001 - Starlette request body caching convention
        if not body:
            request.state.request_data = None
            return None

        try:
            request_data = json.loads(body)
        except json.JSONDecodeError:
            request.state.request_data = None
            return None

        request.state.request_data = request_data
        return request_data

    def _normalized_headers(self, request: Request) -> dict[str, str]:
        return {k.lower(): v for k, v in request.headers.items()}

    def _cached_json_response(self, payload: dict[str, Any], cache_key: str) -> JSONResponse:
        response = JSONResponse(status_code=200, content=payload)
        response.headers["x-litellm-cache-hit"] = "true"
        response.headers["x-litellm-cache-key"] = cache_key
        return response

    async def _maybe_store(
        self,
        *,
        backend: CacheBackend,
        response: Response,
        response_data: dict[str, Any] | None,
        cache_key: str,
        ttl: int,
        model: str,
        metrics: CacheMetricsProtocol,
        endpoint: str,
    ) -> None:
        if response.status_code != 200:
            return
        if isinstance(response, StreamingResponse):
            return

        if response_data is None or "error" in response_data:
            return

        entry = CacheEntry(
            response=response_data,
            model=model,
            cached_at=time.time(),
            ttl=ttl,
            token_count=int((response_data.get("usage") or {}).get("total_tokens") or 0),
        )

        try:
            await backend.set(cache_key, entry, ttl)
            metrics.write(endpoint=endpoint, model=model)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("cache write failed: %s", exc)
            metrics.error(operation="set")

    async def _materialize_response(self, response: Response) -> tuple[Response, dict[str, Any] | None]:
        if isinstance(response, StreamingResponse):
            return response, None

        body = getattr(response, "body", None)
        if body:
            return response, self._decode_json(body)

        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            return response, None

        chunks = [chunk async for chunk in body_iterator]
        body_bytes = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8") for chunk in chunks)
        rebuilt = Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
            background=response.background,
        )
        return rebuilt, self._decode_json(body_bytes)

    def _decode_json(self, body: bytes | str) -> dict[str, Any] | None:
        try:
            if isinstance(body, bytes):
                return json.loads(body.decode("utf-8"))
            return json.loads(body)
        except Exception:
            return None
