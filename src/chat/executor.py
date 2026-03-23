from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, AsyncIterator

import httpx
from fastapi import Request

from src.models.errors import ServiceUnavailableError
from src.models.requests import ChatCompletionRequest
from src.providers.registry import resolve_chat_upstream
from src.providers.signing import apply_request_signing
from src.router.router import Deployment
from src.router.usage import record_router_usage


@dataclass
class OpenedStream:
    context_manager: Any
    response: Any
    translated_stream: Any
    first_line: str
    deployment: Deployment
    params: dict[str, Any]
    api_base: str

    async def close(self, exc: Exception | None = None) -> None:
        if exc is None:
            await self.context_manager.__aexit__(None, None, None)
            return
        await self.context_manager.__aexit__(type(exc), exc, exc.__traceback__)


async def _execute_grpc_chat(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
    upstream: Any,
) -> tuple[dict[str, Any], float]:
    adapter = upstream.adapter
    params = deployment.deltallm_params
    upstream_request = payload.model_copy(update={"metadata": None})
    upstream_payload = await adapter.translate_request(upstream_request, params)

    from src.routers.utils import apply_default_params

    apply_default_params(upstream_payload, deployment.model_info)

    upstream_start = perf_counter()
    grpc_address = upstream.grpc_address

    from src.providers.resolution import resolve_provider

    provider = resolve_provider(params)

    if provider == "triton":
        triton_model = upstream.grpc_metadata.get("triton_model_name", "")
        triton_version = upstream.grpc_metadata.get("triton_model_version", "")
        data = await adapter.execute_grpc_chat(
            grpc_address,
            upstream_payload,
            model_name=triton_model,
            model_version=triton_version,
            timeout=upstream.timeout,
            display_model=payload.model,
        )
    else:
        data = await adapter.execute_grpc_chat(
            grpc_address,
            upstream_payload,
            timeout=upstream.timeout,
        )

    canonical = await adapter.translate_response(data, payload.model)
    canonical_payload = canonical.model_dump(mode="json")

    await record_router_usage(
        request.app.state.router_state_backend,
        deployment.deployment_id,
        mode="chat",
        usage=canonical_payload.get("usage"),
    )
    return canonical_payload, (perf_counter() - upstream_start) * 1000


async def execute_chat(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
) -> tuple[dict[str, Any], float]:
    params = deployment.deltallm_params
    upstream = resolve_chat_upstream(request, params, is_stream=bool(payload.stream))

    if upstream.transport == "grpc" and upstream.grpc_address:
        return await _execute_grpc_chat(request, payload, deployment, upstream)

    adapter, api_base, endpoint, headers, timeout = (
        upstream.adapter,
        upstream.api_base,
        upstream.endpoint,
        upstream.headers,
        upstream.timeout,
    )
    upstream_request = payload.model_copy(update={"metadata": None})
    upstream_payload = await adapter.translate_request(upstream_request, params)

    from src.routers.utils import apply_default_params

    apply_default_params(upstream_payload, deployment.model_info)

    upstream_start = perf_counter()
    request_url = f"{api_base}{endpoint}"
    signed_headers, body_override = apply_request_signing(
        params=params,
        method="POST",
        url=request_url,
        headers=headers,
        json_body=upstream_payload,
    )
    if body_override is not None:
        response = await request.app.state.http_client.post(
            request_url,
            headers=signed_headers,
            content=body_override,
            timeout=timeout,
        )
    else:
        response = await request.app.state.http_client.post(
            request_url,
            headers=signed_headers,
            json=upstream_payload,
            timeout=timeout,
        )
    if response.status_code >= 400:
        status_exc = httpx.HTTPStatusError(
            f"Upstream chat call failed with status {response.status_code}",
            request=httpx.Request("POST", request_url),
            response=response,
        )
        raise adapter.map_error(status_exc)
    data = response.json()
    canonical = await adapter.translate_response(data, payload.model)
    canonical_payload = canonical.model_dump(mode="json")

    await record_router_usage(
        request.app.state.router_state_backend,
        deployment.deployment_id,
        mode="chat",
        usage=canonical_payload.get("usage"),
    )
    return canonical_payload, (perf_counter() - upstream_start) * 1000


class _GrpcStreamContextManager:
    def __init__(self) -> None:
        self._closed = False

    async def __aenter__(self) -> "_GrpcStreamContextManager":
        return self

    async def __aexit__(self, *args: Any) -> None:
        self._closed = True


async def _open_grpc_stream(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
    upstream: Any,
) -> OpenedStream:
    adapter = upstream.adapter
    params = deployment.deltallm_params
    upstream_request = payload.model_copy(update={"metadata": None})
    upstream_payload = await adapter.translate_request(upstream_request, params)

    from src.routers.utils import apply_default_params

    apply_default_params(upstream_payload, deployment.model_info)

    grpc_address = upstream.grpc_address

    from src.providers.resolution import resolve_provider

    provider = resolve_provider(params)

    if provider == "triton":
        triton_model = upstream.grpc_metadata.get("triton_model_name", "")
        triton_version = upstream.grpc_metadata.get("triton_model_version", "")
        raw_stream = adapter.execute_grpc_stream(
            grpc_address,
            upstream_payload,
            model_name=triton_model,
            model_version=triton_version,
            timeout=upstream.timeout,
        )
    else:
        raw_stream = adapter.execute_grpc_stream(
            grpc_address,
            upstream_payload,
            timeout=upstream.timeout,
        )

    context_manager = _GrpcStreamContextManager()
    await context_manager.__aenter__()

    first_line: str | None = None
    async for line in raw_stream:
        if line:
            first_line = line
            break

    if first_line is None:
        await context_manager.__aexit__(None, None, None)
        raise ServiceUnavailableError(message="Provider gRPC stream ended before first chunk")

    async def remaining_stream() -> AsyncIterator[str]:
        async for line in raw_stream:
            yield line

    return OpenedStream(
        context_manager=context_manager,
        response=None,
        translated_stream=remaining_stream(),
        first_line=first_line,
        deployment=deployment,
        params=params,
        api_base=upstream.api_base,
    )


async def open_stream_with_first_chunk(
    request: Request,
    payload: ChatCompletionRequest,
    deployment: Deployment,
) -> OpenedStream:
    params = deployment.deltallm_params
    upstream = resolve_chat_upstream(request, params, is_stream=bool(payload.stream))

    if upstream.transport == "grpc" and upstream.grpc_address:
        return await _open_grpc_stream(request, payload, deployment, upstream)

    adapter, api_base, endpoint, headers, timeout = (
        upstream.adapter,
        upstream.api_base,
        upstream.endpoint,
        upstream.headers,
        upstream.timeout,
    )
    upstream_request = payload.model_copy(update={"metadata": None})
    upstream_payload = await adapter.translate_request(upstream_request, params)

    from src.routers.utils import apply_default_params

    apply_default_params(upstream_payload, deployment.model_info)

    request_url = f"{api_base}{endpoint}"
    signed_headers, body_override = apply_request_signing(
        params=params,
        method="POST",
        url=request_url,
        headers=headers,
        json_body=upstream_payload,
    )
    if body_override is not None:
        context_manager = request.app.state.http_client.stream(
            "POST",
            request_url,
            headers=signed_headers,
            content=body_override,
            timeout=timeout,
        )
    else:
        context_manager = request.app.state.http_client.stream(
            "POST",
            request_url,
            headers=signed_headers,
            json=upstream_payload,
            timeout=timeout,
        )
    response = await context_manager.__aenter__()
    try:
        if response.status_code >= 400:
            status_exc = httpx.HTTPStatusError(
                f"Upstream chat call failed with status {response.status_code}",
                request=httpx.Request("POST", request_url),
                response=response,
            )
            raise adapter.map_error(status_exc)

        translated_stream = adapter.translate_stream(response.aiter_lines())
        first_line: str | None = None
        async for line in translated_stream:
            if line:
                first_line = line
                break
        if first_line is None:
            raise ServiceUnavailableError(message="Provider stream ended before first chunk")

        return OpenedStream(
            context_manager=context_manager,
            response=response,
            translated_stream=translated_stream,
            first_line=first_line,
            deployment=deployment,
            params=params,
            api_base=api_base,
        )
    except Exception as exc:
        await context_manager.__aexit__(type(exc), exc, exc.__traceback__)
        raise
