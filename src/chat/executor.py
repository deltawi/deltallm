from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx
from fastapi import Request

from src.models.errors import ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.metrics import observe_request_phase
from src.providers.registry import resolve_chat_upstream
from src.providers.resolution import is_openai_family_provider, resolve_provider
from src.providers.signing import apply_request_signing
from src.router.router import Deployment
from src.router.usage import record_router_usage
from src.upstream_http import build_upstream_request_timeout_for_request


@dataclass
class OpenedStream:
    context_manager: Any
    response: Any
    translated_stream: Any
    first_line: str
    deployment: Deployment
    params: dict[str, Any]
    api_base: str
    client_stream_usage_requested: bool
    internal_stream_usage_requested: bool
    upstream_started: float
    _closed: bool = False

    async def close(self, exc: BaseException | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        if exc is None:
            await self.context_manager.__aexit__(None, None, None)
        else:
            await self.context_manager.__aexit__(type(exc), exc, exc.__traceback__)
        observe_request_phase(
            route="chat_completions",
            phase="upstream_stream",
            outcome="error" if exc is not None else "success",
            response_kind="stream",
            latency_seconds=perf_counter() - self.upstream_started,
        )


async def execute_chat(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
    *,
    record_usage: bool = True,
) -> tuple[dict[str, Any], float]:
    transform_started = perf_counter()
    params = deployment.deltallm_params
    upstream = resolve_chat_upstream(request, params, is_stream=bool(payload.stream))
    adapter, api_base, endpoint, headers, timeout = (
        upstream.adapter,
        upstream.api_base,
        upstream.endpoint,
        upstream.headers,
        upstream.timeout,
    )
    upstream_request = _upstream_chat_request(payload)
    upstream_payload = await adapter.translate_request(upstream_request, params)

    from src.routers.utils import apply_default_params

    apply_default_params(upstream_payload, deployment.model_info)
    if not payload.stream:
        upstream_payload.pop("stream_options", None)
    observe_request_phase(
        route="chat_completions",
        phase="upstream_transform",
        outcome="success",
        response_kind="nonstream",
        latency_seconds=perf_counter() - transform_started,
    )

    upstream_start = perf_counter()
    request_url = f"{api_base}{endpoint}"
    signed_headers, body_override = apply_request_signing(
        params=params,
        method="POST",
        url=request_url,
        headers=headers,
        json_body=upstream_payload,
    )
    request_timeout = build_upstream_request_timeout_for_request(request, timeout)
    http_started = perf_counter()
    try:
        if body_override is not None:
            response = await request.app.state.http_client.post(
                request_url,
                headers=signed_headers,
                content=body_override,
                timeout=request_timeout,
            )
        else:
            response = await request.app.state.http_client.post(
                request_url,
                headers=signed_headers,
                json=upstream_payload,
                timeout=request_timeout,
            )
    except Exception:
        observe_request_phase(
            route="chat_completions",
            phase="upstream_http",
            outcome="error",
            response_kind="nonstream",
            latency_seconds=perf_counter() - http_started,
        )
        raise
    observe_request_phase(
        route="chat_completions",
        phase="upstream_http",
        outcome="error" if response.status_code >= 400 else "success",
        response_kind="nonstream",
        latency_seconds=perf_counter() - http_started,
    )
    if response.status_code >= 400:
        status_exc = httpx.HTTPStatusError(
            f"Upstream chat call failed with status {response.status_code}",
            request=httpx.Request("POST", request_url),
            response=response,
        )
        raise adapter.map_error(status_exc)
    response_transform_started = perf_counter()
    data = response.json()
    canonical = await adapter.translate_response(data, payload.model)
    canonical_payload = canonical.model_dump(mode="json")
    observe_request_phase(
        route="chat_completions",
        phase="upstream_transform",
        outcome="success",
        response_kind="nonstream",
        latency_seconds=perf_counter() - response_transform_started,
    )

    if record_usage:
        router_state_backend = getattr(request.app.state, "router_state_backend", None)
        if router_state_backend is not None:
            usage_started = perf_counter()
            await record_router_usage(
                router_state_backend,
                deployment.deployment_id,
                mode="chat",
                usage=canonical_payload.get("usage"),
            )
            observe_request_phase(
                route="chat_completions",
                phase="router_usage",
                outcome="success",
                response_kind="nonstream",
                latency_seconds=perf_counter() - usage_started,
            )
    return canonical_payload, (perf_counter() - upstream_start) * 1000


async def open_stream_with_first_chunk(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
) -> OpenedStream:
    transform_started = perf_counter()
    params = deployment.deltallm_params
    upstream = resolve_chat_upstream(request, params, is_stream=bool(payload.stream))
    adapter, api_base, endpoint, headers, timeout = (
        upstream.adapter,
        upstream.api_base,
        upstream.endpoint,
        upstream.headers,
        upstream.timeout,
    )
    upstream_request = _upstream_chat_request(payload)
    client_stream_usage_requested = _client_stream_usage_requested(payload)
    upstream_payload = await adapter.translate_request(upstream_request, params)

    from src.routers.utils import apply_default_params

    apply_default_params(upstream_payload, deployment.model_info)
    internal_stream_usage_requested = _request_stream_usage_when_supported(upstream_payload, params)
    observe_request_phase(
        route="chat_completions",
        phase="upstream_transform",
        outcome="success",
        response_kind="stream",
        latency_seconds=perf_counter() - transform_started,
    )

    request_url = f"{api_base}{endpoint}"
    signed_headers, body_override = apply_request_signing(
        params=params,
        method="POST",
        url=request_url,
        headers=headers,
        json_body=upstream_payload,
    )
    request_timeout = build_upstream_request_timeout_for_request(request, timeout)
    if body_override is not None:
        context_manager = request.app.state.http_client.stream(
            "POST",
            request_url,
            headers=signed_headers,
            content=body_override,
            timeout=request_timeout,
        )
    else:
        context_manager = request.app.state.http_client.stream(
            "POST",
            request_url,
            headers=signed_headers,
            json=upstream_payload,
            timeout=request_timeout,
        )
    upstream_started = perf_counter()
    try:
        response = await context_manager.__aenter__()
    except Exception:
        observe_request_phase(
            route="chat_completions",
            phase="upstream_http",
            outcome="error",
            response_kind="stream",
            latency_seconds=perf_counter() - upstream_started,
        )
        raise
    try:
        if response.status_code >= 400:
            status_exc = httpx.HTTPStatusError(
                f"Upstream chat call failed with status {response.status_code}",
                request=httpx.Request("POST", request_url),
                response=response,
            )
            raise adapter.map_error(status_exc)

        raw_stream = response.aiter_bytes() if adapter.stream_uses_bytes else response.aiter_lines()
        translated_stream = adapter.translate_stream(raw_stream, model_name=payload.model)
        first_line: str | None = None
        async for line in translated_stream:
            if line:
                first_line = line
                break
        if first_line is None:
            raise ServiceUnavailableError(
                message="Provider stream ended before first chunk",
                affects_deployment_health=True,
            )

        observe_request_phase(
            route="chat_completions",
            phase="upstream_http",
            outcome="success",
            response_kind="stream",
            latency_seconds=perf_counter() - upstream_started,
        )

        return OpenedStream(
            context_manager=context_manager,
            response=response,
            translated_stream=translated_stream,
            first_line=first_line,
            deployment=deployment,
            params=params,
            api_base=api_base,
            client_stream_usage_requested=client_stream_usage_requested,
            internal_stream_usage_requested=internal_stream_usage_requested,
            upstream_started=upstream_started,
        )
    except BaseException as exc:
        observe_request_phase(
            route="chat_completions",
            phase="upstream_http",
            outcome="error",
            response_kind="stream",
            latency_seconds=perf_counter() - upstream_started,
        )
        await context_manager.__aexit__(type(exc), exc, exc.__traceback__)
        raise


def _request_stream_usage_when_supported(
    upstream_payload: dict[str, Any], params: dict[str, Any]
) -> bool:
    if not upstream_payload.get("stream"):
        return False
    if not is_openai_family_provider(resolve_provider(params)):
        return False
    stream_options = upstream_payload.get("stream_options")
    if stream_options is None:
        upstream_payload["stream_options"] = {"include_usage": True}
        return True
    if isinstance(stream_options, dict):
        if stream_options.get("include_usage") is True:
            return False
        upstream_payload["stream_options"] = {**stream_options, "include_usage": True}
        return True
    return False


def _client_stream_usage_requested(payload: ChatCompletionRequest) -> bool:
    stream_options = payload.stream_options
    return isinstance(stream_options, dict) and stream_options.get("include_usage") is True


def _upstream_chat_request(payload: ChatCompletionRequest) -> ChatCompletionRequest:
    updates: dict[str, Any] = {"metadata": None}
    if not payload.stream:
        updates["stream_options"] = None
    return payload.model_copy(update=updates)
