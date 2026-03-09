from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.billing.cost import completion_cost
from src.middleware.auth import authenticate_request
from src.middleware.rate_limit import _check_and_acquire_rate_limits, _release_rate_limits
from src.metrics import increment_request, increment_spend, increment_usage, infer_provider
from src.routers.utils import enforce_budget_if_configured, fire_and_forget

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
            "/v1/completions",
            "/v1/responses",
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
        try:
            await authenticate_request(request)
            await _check_and_acquire_rate_limits(request)
        except HTTPException as exc:
            headers = getattr(exc, "headers", None) or {}
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=headers)
        try:
            await enforce_budget_if_configured(request, model=str(request_data.get("model") or ""))

            cache_options = parse_cache_options(request_data, self._normalized_headers(request))
            model = str(request_data.get("model") or "unknown")
            endpoint = request.url.path

            if cache_options.control == CacheControl.BYPASS:
                request.state.cache_hit = False
                response = await call_next(request)
                response.headers["x-deltallm-cache-hit"] = "false"
                return response

            cache_key = key_builder.build_key_from_payload(request_data, cache_options.custom_key)
            cache_key = self._scoped_cache_key(cache_key, request)
            request.state.cache_context = CacheContext(cache_key=cache_key, options=cache_options, model=model)
            request.state.cache_context.hit = False

            if bool(request_data.get("stream")) and streaming_handler is not None:
                if request.url.path != "/v1/chat/completions":
                    response = await call_next(request)
                    response.headers["x-deltallm-cache-hit"] = "false"
                    return response
                if cache_options.control != CacheControl.NO_CACHE:
                    cached = await backend.get(cache_key)
                    if cached is not None:
                        metrics.hit(endpoint=endpoint, model=model)
                        request.state.cache_context.hit = True
                        request.state.cache_hit = True
                        self._record_cache_hit_accounting(request, endpoint, model, cached.response)
                        return StreamingResponse(
                            streaming_handler.reconstruct_sse_stream(cached.response),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "x-deltallm-cache-hit": "true",
                            },
                        )
                    metrics.miss(endpoint=endpoint, model=model)

                request.state.cache_context = CacheContext(cache_key=cache_key, options=cache_options, model=model)
                request.state.cache_context.hit = False
                request.state.cache_hit = False
                response = await call_next(request)
                response.headers["x-deltallm-cache-hit"] = "false"
                return response

            if cache_options.control != CacheControl.NO_CACHE:
                cached_entry = await backend.get(cache_key)
                if cached_entry is not None:
                    metrics.hit(endpoint=endpoint, model=model)
                    request.state.cache_context.hit = True
                    request.state.cache_hit = True
                    self._record_cache_hit_accounting(request, endpoint, model, cached_entry.response)
                    return self._cached_json_response(cached_entry.response, cache_key)
                metrics.miss(endpoint=endpoint, model=model)
                request.state.cache_hit = False

            response = await call_next(request)
            request.state.cache_hit = False
            response.headers["x-deltallm-cache-hit"] = "false"

            if cache_options.control == CacheControl.NO_STORE:
                return response

            response, response_data = await self._materialize_response(response)
            await self._maybe_store(
                backend=backend,
                response=response,
                response_data=response_data,
                cache_key=cache_key,
                ttl=cache_options.ttl or self._effective_default_ttl(request),
                model=model,
                metrics=metrics,
                endpoint=endpoint,
            )
            return response
        finally:
            await _release_rate_limits(request)

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

    def _effective_default_ttl(self, request: Request) -> int:
        general_settings = getattr(getattr(request.app.state, "app_config", None), "general_settings", None)
        configured = getattr(general_settings, "cache_ttl", None)
        try:
            return int(configured) if configured is not None else self.default_ttl
        except (TypeError, ValueError):
            return self.default_ttl

    def _cached_json_response(self, payload: dict[str, Any], cache_key: str) -> JSONResponse:
        response = JSONResponse(status_code=200, content=payload)
        response.headers["x-deltallm-cache-hit"] = "true"
        response.headers["x-deltallm-cache-key"] = cache_key
        return response

    def _record_cache_hit_accounting(self, request: Request, endpoint: str, model: str, payload: dict[str, Any]) -> None:
        auth = getattr(request.state, "user_api_key", None)
        if auth is None:
            return
        call_type = "embedding" if endpoint == "/v1/embeddings" else "completion"
        usage = payload.get("usage") if isinstance(payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        api_provider = infer_provider(model)
        request_cost = completion_cost(model=model, usage=usage, cache_hit=True)
        increment_request(
            model=model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            status_code=200,
        )
        increment_usage(
            model=model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )
        increment_spend(
            model=model,
            api_provider=api_provider,
            api_key=auth.api_key,
            user=auth.user_id,
            team=auth.team_id,
            spend=request_cost,
        )
        fire_and_forget(
            request.app.state.spend_tracking_service.log_spend(
                request_id=request.headers.get("x-request-id") or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                end_user_id=None,
                model=model,
                call_type=call_type,
                usage=usage,
                cost=request_cost,
                metadata={"api_base": "cache"},
                cache_hit=True,
            )
        )

    def _scoped_cache_key(self, cache_key: str, request: Request) -> str:
        auth = getattr(request.state, "user_api_key", None)
        if auth is None:
            return f"scope:anonymous:{cache_key}"
        scope_key = str(getattr(auth, "api_key", "") or "anonymous")
        return f"scope:key:{scope_key}:{cache_key}"

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
