from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator

from src.models.errors import ServiceUnavailableError
from src.providers.base import ProviderAdapter
from src.providers.grpc_channel import GrpcChannelManager

logger = logging.getLogger(__name__)

try:
    import grpc
    import grpc.aio

    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False
    grpc = None  # type: ignore[assignment]

VLLM_GRPC_UNARY_METHOD = "/vllm.EntrypointsService/Chat"
VLLM_GRPC_STREAM_METHOD = "/vllm.EntrypointsService/ChatStream"


def _build_chat_request_bytes(payload: dict[str, Any]) -> bytes:
    """Serialize an OpenAI-compatible chat payload to bytes for gRPC transport.

    vLLM's gRPC interface accepts JSON-serialized OpenAI-compatible payloads
    as raw bytes, mirroring the HTTP API contract. This is by design — vLLM
    does not publish a compiled proto schema for external callers; instead the
    gRPC methods accept and return JSON byte strings directly. We use identity
    serializer/deserializer on the gRPC channel accordingly.
    """
    return json.dumps(payload).encode("utf-8")


def _parse_response_bytes(data: bytes) -> dict[str, Any]:
    return json.loads(data.decode("utf-8"))


def _build_auth_metadata(api_key: str | None) -> list[tuple[str, str]]:
    if api_key:
        return [("authorization", f"Bearer {api_key}")]
    return []


class VLLMGrpcAdapter(ProviderAdapter):
    provider_name = "vllm"

    def __init__(self, channel_manager: GrpcChannelManager) -> None:
        self._channel_manager = channel_manager

    async def translate_request(
        self,
        canonical_request: Any,
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        from src.providers.resolution import resolve_upstream_model

        upstream_model = resolve_upstream_model(provider_config)
        messages = []
        if hasattr(canonical_request, "messages") and canonical_request.messages:
            for msg in canonical_request.messages:
                m: dict[str, Any] = {"role": msg.role}
                if isinstance(msg.content, str):
                    m["content"] = msg.content
                elif isinstance(msg.content, list):
                    parts = []
                    for part in msg.content:
                        if hasattr(part, "type"):
                            if part.type == "text":
                                parts.append({"type": "text", "text": part.text})
                            elif part.type == "image_url":
                                parts.append({"type": "image_url", "image_url": {"url": part.image_url.url}})
                        else:
                            parts.append(part)
                    m["content"] = parts
                messages.append(m)

        request_payload: dict[str, Any] = {
            "model": upstream_model,
            "messages": messages,
        }

        if hasattr(canonical_request, "temperature") and canonical_request.temperature is not None:
            request_payload["temperature"] = canonical_request.temperature
        if hasattr(canonical_request, "top_p") and canonical_request.top_p is not None:
            request_payload["top_p"] = canonical_request.top_p
        if hasattr(canonical_request, "max_tokens") and canonical_request.max_tokens is not None:
            request_payload["max_tokens"] = canonical_request.max_tokens
        if hasattr(canonical_request, "stop") and canonical_request.stop is not None:
            request_payload["stop"] = canonical_request.stop
        if hasattr(canonical_request, "stream") and canonical_request.stream:
            request_payload["stream"] = True

        return request_payload

    async def translate_response(self, provider_response: Any, model_name: str) -> Any:
        from src.models.responses import ChatCompletionResponse

        if isinstance(provider_response, bytes):
            provider_response = _parse_response_bytes(provider_response)

        choices = []
        for choice in provider_response.get("choices", []):
            choices.append({
                "index": choice.get("index", 0),
                "message": {
                    "role": choice.get("message", {}).get("role", "assistant"),
                    "content": choice.get("message", {}).get("content", ""),
                },
                "finish_reason": choice.get("finish_reason", "stop"),
            })

        response_data = {
            "id": provider_response.get("id", f"chatcmpl-{uuid.uuid4().hex[:12]}"),
            "object": "chat.completion",
            "created": provider_response.get("created", 0),
            "model": model_name,
            "choices": choices,
            "usage": provider_response.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}),
        }
        return ChatCompletionResponse.model_validate(response_data)

    async def translate_stream(self, provider_stream: AsyncIterator[str]) -> AsyncIterator[str]:
        async for line in provider_stream:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("data: "):
                yield stripped
            elif stripped == "[DONE]":
                yield "data: [DONE]"
            else:
                yield f"data: {stripped}"

    def map_error(self, provider_error: Exception) -> Exception:
        if GRPC_AVAILABLE and isinstance(provider_error, grpc.aio.AioRpcError):
            code = provider_error.code()
            details = provider_error.details() or "gRPC error"
            if code == grpc.StatusCode.UNAVAILABLE:
                return ServiceUnavailableError(message=f"vLLM gRPC unavailable: {details}")
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                return ServiceUnavailableError(message=f"vLLM gRPC timeout: {details}")
            if code == grpc.StatusCode.RESOURCE_EXHAUSTED:
                return ServiceUnavailableError(message=f"vLLM gRPC resource exhausted: {details}")
            return ServiceUnavailableError(message=f"vLLM gRPC error ({code.name}): {details}")
        return provider_error

    async def health_check(self, provider_config: dict[str, Any]) -> bool:
        address = provider_config.get("grpc_address")
        if not address:
            return False
        return await self._channel_manager.check_connectivity(address, timeout=5.0)

    async def execute_grpc_chat(
        self,
        address: str,
        payload: dict[str, Any],
        *,
        timeout: int = 300,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        if not GRPC_AVAILABLE:
            raise RuntimeError("grpcio is not installed")

        channel = await self._channel_manager.get_channel(address)
        request_bytes = _build_chat_request_bytes(payload)
        metadata = _build_auth_metadata(api_key)

        try:
            response_bytes = await channel.unary_unary(
                VLLM_GRPC_UNARY_METHOD,
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request_bytes, timeout=timeout, metadata=metadata or None)
            return _parse_response_bytes(response_bytes)
        except Exception as exc:
            raise self.map_error(exc) from exc

    async def execute_grpc_stream(
        self,
        address: str,
        payload: dict[str, Any],
        *,
        timeout: int = 300,
        api_key: str | None = None,
    ) -> AsyncIterator[str]:
        if not GRPC_AVAILABLE:
            raise RuntimeError("grpcio is not installed")

        channel = await self._channel_manager.get_channel(address)
        request_bytes = _build_chat_request_bytes(payload)
        metadata = _build_auth_metadata(api_key)

        try:
            call = channel.unary_stream(
                VLLM_GRPC_STREAM_METHOD,
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request_bytes, timeout=timeout, metadata=metadata or None)

            async for response_bytes in call:
                chunk = _parse_response_bytes(response_bytes)
                yield f"data: {json.dumps(chunk)}"
            yield "data: [DONE]"
        except Exception as exc:
            raise self.map_error(exc) from exc
