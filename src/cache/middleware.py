from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from src.billing.pricing import normalize_gateway_cache_hit_usage
from src.billing.tier_pricing import (
    attach_pricing_metadata,
    resolve_tier_pricing,
    resolve_token_billing_result,
)
from src.chat.preflight import run_text_preflight
from src.embedding_preflight import run_embedding_preflight
from src.middleware.auth import authenticate_request
from src.middleware.errors import proxy_error_response
from src.middleware.rate_limit import _release_rate_limits
from src.metrics import increment_request, increment_spend, increment_usage
from src.models.errors import ProxyError
from src.models.requests import (
    ChatCompletionRequest,
    CompletionsRequest,
    EmbeddingRequest,
    ResponsesRequest,
)
from src.providers.resolution import provider_from_model, resolve_provider, resolve_upstream_model
from src.router.runtime_generation import pin_routing_runtime_generation
from src.routers.text_adapters import (
    completions_to_chat_request,
    responses_to_chat_request,
)
from src.telemetry.request_failures import enqueue_request_log_write, maybe_log_proxy_error
from src.telemetry.event_identity import get_or_create_billing_event_id

from .backends.base import CacheBackend, CacheEntry
from .key_builder import CacheKeyBuilder
from .metrics import CacheMetricsProtocol, NoopCacheMetrics
from .pricing import has_cache_hit_only_pricing, provider_cache_miss_usage

logger = logging.getLogger(__name__)
_CACHE_SCHEMA_VERSION = "v2"


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
        metrics: CacheMetricsProtocol = getattr(
            request.app.state, "cache_metrics", NoopCacheMetrics()
        )
        streaming_handler = getattr(request.app.state, "streaming_cache_handler", None)

        if backend is None or key_builder is None or not self._should_cache(request):
            return await call_next(request)

        request_data = await self._read_request_data(request)
        if not request_data:
            return await call_next(request)
        try:
            await authenticate_request(request)
        except HTTPException as exc:
            headers = getattr(exc, "headers", None) or {}
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}, headers=headers
            )
        try:
            prepared_data = await self._prepare_request(request, request_data)
        except ValidationError:
            # Let FastAPI preserve its endpoint-specific 422 response contract.
            return await call_next(request)
        except ProxyError as exc:
            await maybe_log_proxy_error(request, exc)
            return proxy_error_response(exc)

        if prepared_data is None:
            return await call_next(request)

        request_data = prepared_data
        try:
            cache_options = parse_cache_options(request_data, self._normalized_headers(request))
            model = str(request_data.get("model") or "unknown")
            endpoint = request.url.path

            if cache_options.control == CacheControl.BYPASS:
                request.state.cache_hit = False
                response = await call_next(request)
                response.headers["x-deltallm-cache-hit"] = "false"
                return response

            cache_key = key_builder.build_key_from_payload(request_data, cache_options.custom_key)
            response_mode = "stream" if bool(request_data.get("stream")) else "json"
            cache_key = (
                f"schema:{_CACHE_SCHEMA_VERSION}:mode:{response_mode}:"
                f"endpoint:{endpoint}:{cache_key}"
            )
            cache_key = self._scoped_cache_key(cache_key, request)
            request.state.cache_context = CacheContext(
                cache_key=cache_key, options=cache_options, model=model
            )
            request.state.cache_context.hit = False

            if bool(request_data.get("stream")) and streaming_handler is not None:
                if request.url.path != "/v1/chat/completions":
                    response = await call_next(request)
                    response.headers["x-deltallm-cache-hit"] = "false"
                    return response
                if cache_options.control != CacheControl.NO_CACHE:
                    cached = await backend.get(cache_key)
                    if cached is not None and streaming_handler.can_replay(cached):
                        metrics.hit(endpoint=endpoint, model=model)
                        request.state.cache_context.hit = True
                        request.state.cache_hit = True
                        await self._record_cache_hit_accounting(
                            request, endpoint, model, cache_key, cached
                        )
                        return StreamingResponse(
                            streaming_handler.reconstruct_sse_stream(
                                cached,
                                include_usage=_stream_usage_requested(request_data),
                            ),
                            media_type="text/event-stream",
                            headers={
                                "Cache-Control": "no-cache",
                                "Connection": "keep-alive",
                                "x-deltallm-cache-hit": "true",
                            },
                        )
                    metrics.miss(endpoint=endpoint, model=model)

                request.state.cache_context = CacheContext(
                    cache_key=cache_key, options=cache_options, model=model
                )
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
                    await self._record_cache_hit_accounting(
                        request, endpoint, model, cache_key, cached_entry
                    )
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
                request=request,
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
            if not bool(getattr(request.state, "_rate_limit_lifecycle_managed", False)):
                await _release_rate_limits(request)

    async def _prepare_request(
        self,
        request: Request,
        request_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        endpoint = request.url.path
        routing_runtime = pin_routing_runtime_generation(request.app.state, request.state)
        if endpoint == "/v1/embeddings":
            payload = EmbeddingRequest.model_validate(request_data)
            prepared = await run_embedding_preflight(
                request=request,
                payload=payload,
                routing_runtime=routing_runtime,
            )
            return dict(prepared.request_data)

        if endpoint == "/v1/chat/completions":
            payload = ChatCompletionRequest.model_validate(request_data)
            canonical_data: dict[str, Any] | None = request_data
        elif endpoint == "/v1/completions":
            payload = completions_to_chat_request(CompletionsRequest.model_validate(request_data))
            canonical_data = None
        elif endpoint == "/v1/responses":
            payload = responses_to_chat_request(ResponsesRequest.model_validate(request_data))
            canonical_data = None
        else:
            return None

        prepared = await run_text_preflight(
            request=request,
            payload=payload,
            request_data=canonical_data,
            routing_runtime=routing_runtime,
        )
        return dict(prepared.request_data)

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
        general_settings = getattr(
            getattr(request.app.state, "app_config", None), "general_settings", None
        )
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

    async def _record_cache_hit_accounting(
        self,
        request: Request,
        endpoint: str,
        model: str,
        cache_key: str,
        entry: CacheEntry,
    ) -> None:
        auth = getattr(request.state, "user_api_key", None)
        if auth is None:
            return
        call_type = "embedding" if endpoint == "/v1/embeddings" else "completion"
        payload = entry.response if isinstance(entry.response, dict) else {}
        raw_usage = payload.get("usage") if isinstance(payload, dict) else None
        raw_usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
        usage = normalize_gateway_cache_hit_usage(raw_usage)
        deployment_model = _normalized_text(entry.deployment_model)
        api_provider = _normalized_provider(entry.provider)
        deployment = None
        if api_provider is None or deployment_model is None:
            deployment = _find_runtime_deployment(request, entry.deployment_id)
        if deployment is not None:
            deployment_model = deployment_model or _normalized_text(
                deployment.deltallm_params.get("model")
            )
            api_provider = api_provider or _normalized_provider(
                resolve_provider(deployment.deltallm_params)
            )
        if api_provider is None:
            api_provider = (
                _provider_from_model_value(deployment_model)
                or _provider_from_model_value(model)
                or "unknown"
            )
        pricing_model_info = dict(entry.pricing or {}) if isinstance(entry.pricing, dict) else None
        pricing = resolve_tier_pricing(
            auth=auth,
            model=model,
            provider_model=resolve_upstream_model(
                {"model": deployment_model} if deployment_model is not None else None,
                fallback_model=model,
            ),
            tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
            deployment_model_info=pricing_model_info,
            mode="sync",
        )
        customer_billing = resolve_token_billing_result(
            pricing,
            model=model,
            usage=usage,
            cache_hit=True,
        )
        request_cost = customer_billing.billing.cost
        if has_cache_hit_only_pricing(pricing.provider_model_info):
            avoided_billing = resolve_token_billing_result(
                pricing,
                model=model,
                usage=usage,
                cache_hit=True,
                pricing_view="provider",
            )
            provider_cost_avoided_basis = "cache_hit_pricing_fallback"
        else:
            avoided_billing = resolve_token_billing_result(
                pricing,
                model=model,
                usage=provider_cache_miss_usage(raw_usage),
                cache_hit=False,
                pricing_view="provider",
            )
            provider_cost_avoided_basis = "provider_miss_pricing"
        provider_cost_avoided = avoided_billing.billing.cost
        provider_cost = 0.0
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
        spend_metadata = attach_pricing_metadata(
            {
                "api_base": "cache",
                "cache_key": cache_key,
                "provider": api_provider,
                "deployment_model": deployment_model,
                "cache_cost_basis": "avoided_provider_cost",
                "provider_cost_avoided": round(float(provider_cost_avoided), 10),
                "provider_cost_avoided_basis": provider_cost_avoided_basis,
                "provider_cost_avoided_billing": {
                    "cost": avoided_billing.billing.cost,
                    "billing_unit": avoided_billing.billing.billing_unit,
                    "pricing_fields_used": list(avoided_billing.billing.pricing_fields_used),
                    "usage_snapshot": dict(avoided_billing.billing.usage_snapshot),
                    "unpriced_reason": avoided_billing.billing.unpriced_reason,
                    "missing_pricing_fields": list(avoided_billing.missing_pricing_fields),
                },
            },
            pricing,
            provider_cost=provider_cost,
            billing=customer_billing.billing,
            effective_pricing_sources=customer_billing.pricing_sources_used,
            missing_pricing_fields=customer_billing.missing_pricing_fields,
        )
        await enqueue_request_log_write(
            request,
            request.app.state.spend_tracking_service.log_spend(
                event_id=get_or_create_billing_event_id(request),
                request_id=request.headers.get("x-request-id") or "",
                api_key=auth.api_key,
                user_id=auth.user_id,
                team_id=auth.team_id,
                organization_id=getattr(auth, "organization_id", None),
                owner_account_id=getattr(auth, "owner_account_id", None),
                end_user_id=None,
                model=model,
                call_type=call_type,
                usage=usage,
                cost=request_cost,
                metadata=spend_metadata,
                cache_hit=True,
            ),
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
        request: Request,
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
            pricing=_pricing_for_cache_entry(request),
            deployment_id=_deployment_id_for_cache_entry(request),
            provider=_provider_for_cache_entry(request),
            deployment_model=_deployment_model_for_cache_entry(request),
        )

        try:
            await backend.set(cache_key, entry, ttl)
            metrics.write(endpoint=endpoint, model=model)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.warning("cache write failed: %s", exc)
            metrics.error(operation="set")

    async def _materialize_response(
        self, response: Response
    ) -> tuple[Response, dict[str, Any] | None]:
        if isinstance(response, StreamingResponse):
            return response, None

        body = getattr(response, "body", None)
        if body:
            return response, self._decode_json(body)

        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            return response, None

        chunks = [chunk async for chunk in body_iterator]
        body_bytes = b"".join(
            chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8") for chunk in chunks
        )
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


def _pricing_for_cache_entry(request: Request) -> dict[str, Any] | None:
    pricing = getattr(request.state, "cache_store_pricing", None)
    return dict(pricing) if isinstance(pricing, dict) else None


def _deployment_id_for_cache_entry(request: Request) -> str | None:
    deployment_id = getattr(request.state, "cache_store_deployment_id", None)
    return str(deployment_id) if deployment_id else None


def _provider_for_cache_entry(request: Request) -> str | None:
    return _normalized_provider(getattr(request.state, "cache_store_provider", None))


def _deployment_model_for_cache_entry(request: Request) -> str | None:
    return _normalized_text(getattr(request.state, "cache_store_deployment_model", None))


def _find_runtime_deployment(request: Request, deployment_id: str | None):
    if not deployment_id:
        return None
    registry = getattr(getattr(request.app.state, "router", None), "deployment_registry", {}) or {}
    for deployments in registry.values():
        for deployment in deployments:
            if deployment.deployment_id == deployment_id:
                return deployment
    return None


def _stream_usage_requested(request_data: dict[str, Any]) -> bool:
    stream_options = request_data.get("stream_options")
    return isinstance(stream_options, dict) and stream_options.get("include_usage") is True


def _normalized_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalized_provider(value: Any) -> str | None:
    normalized = _normalized_text(value)
    if normalized is None:
        return None
    return normalized.lower()


def _provider_from_model_value(value: Any) -> str | None:
    provider = provider_from_model(_normalized_text(value))
    return None if provider == "unknown" else provider
