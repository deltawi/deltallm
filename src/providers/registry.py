from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from src.models.errors import InvalidRequestError
from src.providers.base import ProviderAdapter
from src.providers.resolution import is_openai_compatible_provider, resolve_provider, resolve_upstream_model


@dataclass
class ChatUpstream:
    adapter: ProviderAdapter
    api_base: str
    endpoint: str
    headers: dict[str, str]
    timeout: int
    transport: str = "http"
    grpc_address: str | None = None
    grpc_metadata: dict[str, str] = field(default_factory=dict)


def resolve_chat_upstream(
    request: Request,
    params: dict[str, Any],
    *,
    is_stream: bool = False,
) -> ChatUpstream:
    provider = resolve_provider(params)
    timeout = int(params.get("timeout") or 300)
    if provider == "anthropic":
        api_key = params.get("api_key")
        if not api_key:
            raise InvalidRequestError(message="Provider API key is missing for selected model")
        return ChatUpstream(
            adapter=request.app.state.anthropic_adapter,
            api_base=str(params.get("api_base") or "https://api.anthropic.com/v1").rstrip("/"),
            endpoint="/messages",
            headers={
                "x-api-key": str(api_key),
                "anthropic-version": str(params.get("api_version") or "2023-06-01"),
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    if provider in {"azure", "azure_openai"}:
        api_key = params.get("api_key")
        if not api_key:
            raise InvalidRequestError(message="Provider API key is missing for selected model")
        return ChatUpstream(
            adapter=request.app.state.azure_openai_adapter,
            api_base=str(params.get("api_base", request.app.state.settings.openai_base_url)).rstrip("/"),
            endpoint="/chat/completions",
            headers={"api-key": str(api_key), "Content-Type": "application/json"},
            timeout=timeout,
        )

    if provider == "gemini":
        api_key = params.get("api_key")
        if not api_key:
            raise InvalidRequestError(message="Provider API key is missing for selected model")
        if is_stream:
            raise InvalidRequestError(message="Gemini streaming is not supported yet")
        upstream_model = resolve_upstream_model(params)
        return ChatUpstream(
            adapter=request.app.state.gemini_adapter,
            api_base=str(params.get("api_base") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/"),
            endpoint=f"/models/{upstream_model}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )

    if provider == "bedrock":
        if is_stream:
            raise InvalidRequestError(message="Bedrock streaming is not supported yet")
        region = str(params.get("region") or "us-east-1")
        upstream_model = resolve_upstream_model(params)
        return ChatUpstream(
            adapter=request.app.state.bedrock_adapter,
            api_base=str(params.get("api_base") or f"https://bedrock-runtime.{region}.amazonaws.com").rstrip("/"),
            endpoint=f"/model/{upstream_model}/converse",
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )

    transport = str(params.get("transport", "http")).lower()
    grpc_address = params.get("grpc_address")

    if transport == "grpc" and grpc_address:
        if provider == "triton":
            adapter = getattr(request.app.state, "triton_grpc_adapter", None)
            if adapter is None:
                raise InvalidRequestError(message="Triton gRPC adapter is not available")
            http_fallback = str(params.get("http_fallback_base") or params.get("api_base") or "").rstrip("/")
            return ChatUpstream(
                adapter=adapter,
                api_base=http_fallback,
                endpoint="/chat/completions",
                headers={},
                timeout=timeout,
                transport="grpc",
                grpc_address=str(grpc_address),
                grpc_metadata={
                    "triton_model_name": str(params.get("triton_model_name") or resolve_upstream_model(params)),
                    "triton_model_version": str(params.get("triton_model_version") or ""),
                },
            )
        else:
            adapter = getattr(request.app.state, "vllm_grpc_adapter", None)
            if adapter is None:
                raise InvalidRequestError(message="vLLM gRPC adapter is not available")
            http_fallback = str(params.get("http_fallback_base") or params.get("api_base") or "").rstrip("/")
            api_key = params.get("api_key")
            headers: dict[str, str] = {}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            return ChatUpstream(
                adapter=adapter,
                api_base=http_fallback,
                endpoint="/chat/completions",
                headers=headers,
                timeout=timeout,
                transport="grpc",
                grpc_address=str(grpc_address),
            )

    if provider not in {"unknown", ""} and not is_openai_compatible_provider(provider):
        raise InvalidRequestError(message=f"Unsupported provider '{provider}' for chat endpoint")

    api_key = params.get("api_key")
    if not api_key:
        raise InvalidRequestError(message="Provider API key is missing for selected model")
    return ChatUpstream(
        adapter=request.app.state.openai_adapter,
        api_base=str(params.get("api_base", request.app.state.settings.openai_base_url)).rstrip("/"),
        endpoint="/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=timeout,
    )
