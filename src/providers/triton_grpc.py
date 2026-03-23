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


def _build_triton_infer_request(
    payload: dict[str, Any],
    model_name: str,
    model_version: str = "",
) -> bytes:
    prompt = ""
    messages = payload.get("messages", [])
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
            content = " ".join(text_parts)
        prompt += f"<|{role}|>\n{content}\n"
    prompt += "<|assistant|>\n"

    request_body: dict[str, Any] = {
        "model_name": model_name,
        "model_version": model_version,
        "inputs": [
            {
                "name": "text_input",
                "shape": [1, 1],
                "datatype": "BYTES",
                "data": [prompt],
            }
        ],
        "parameters": {},
    }

    if "temperature" in payload:
        request_body["parameters"]["temperature"] = str(payload["temperature"])
    if "top_p" in payload:
        request_body["parameters"]["top_p"] = str(payload["top_p"])
    if "max_tokens" in payload:
        request_body["parameters"]["max_tokens"] = str(payload["max_tokens"])
    if payload.get("stream"):
        request_body["parameters"]["stream"] = "true"

    return json.dumps(request_body).encode("utf-8")


def _parse_triton_response(data: bytes, model_name_display: str) -> dict[str, Any]:
    response = json.loads(data.decode("utf-8"))

    text_output = ""
    outputs = response.get("outputs", [])
    for output in outputs:
        if output.get("name") == "text_output":
            output_data = output.get("data", [])
            if output_data:
                text_output = str(output_data[0])
            break

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": 0,
        "model": model_name_display,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text_output},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


class TritonGrpcAdapter(ProviderAdapter):
    provider_name = "triton"

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
        if hasattr(canonical_request, "stream") and canonical_request.stream:
            request_payload["stream"] = True

        return request_payload

    async def translate_response(self, provider_response: Any, model_name: str) -> Any:
        from src.models.responses import ChatCompletionResponse

        if isinstance(provider_response, bytes):
            provider_response = _parse_triton_response(provider_response, model_name)

        if isinstance(provider_response, dict):
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

        return provider_response

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
                return ServiceUnavailableError(message=f"Triton gRPC unavailable: {details}")
            if code == grpc.StatusCode.DEADLINE_EXCEEDED:
                return ServiceUnavailableError(message=f"Triton gRPC timeout: {details}")
            if code == grpc.StatusCode.NOT_FOUND:
                return ServiceUnavailableError(message=f"Triton model not found: {details}")
            return ServiceUnavailableError(message=f"Triton gRPC error ({code.name}): {details}")
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
        model_name: str,
        model_version: str = "",
        *,
        timeout: int = 300,
        display_model: str = "",
    ) -> dict[str, Any]:
        if not GRPC_AVAILABLE:
            raise RuntimeError("grpcio is not installed")

        channel = await self._channel_manager.get_channel(address)
        request_bytes = _build_triton_infer_request(payload, model_name, model_version)

        try:
            response_bytes = await channel.unary_unary(
                "/inference.GRPCInferenceService/ModelInfer",
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request_bytes, timeout=timeout)
            return _parse_triton_response(response_bytes, display_model or model_name)
        except Exception as exc:
            raise self.map_error(exc) from exc

    async def execute_grpc_stream(
        self,
        address: str,
        payload: dict[str, Any],
        model_name: str,
        model_version: str = "",
        *,
        timeout: int = 300,
    ) -> AsyncIterator[str]:
        if not GRPC_AVAILABLE:
            raise RuntimeError("grpcio is not installed")

        channel = await self._channel_manager.get_channel(address)
        request_bytes = _build_triton_infer_request(payload, model_name, model_version)

        try:
            call = channel.unary_stream(
                "/inference.GRPCInferenceService/ModelStreamInfer",
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request_bytes, timeout=timeout)

            async for response_bytes in call:
                chunk_data = json.loads(response_bytes.decode("utf-8"))
                text_output = ""
                for output in chunk_data.get("outputs", []):
                    if output.get("name") == "text_output":
                        data = output.get("data", [])
                        if data:
                            text_output = str(data[0])
                        break
                chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": text_output}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}"
            yield "data: [DONE]"
        except Exception as exc:
            raise self.map_error(exc) from exc
