from __future__ import annotations

import json
import logging
import struct
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


def _encode_varint(value: int) -> bytes:
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def _encode_field(field_number: int, wire_type: int, data: bytes) -> bytes:
    tag = _encode_varint((field_number << 3) | wire_type)
    if wire_type == 2:
        return tag + _encode_varint(len(data)) + data
    return tag + data


def _build_infer_input_tensor_pb(name: str, datatype: str, shape: list[int], raw_data: bytes) -> bytes:
    msg = b""
    msg += _encode_field(1, 2, name.encode("utf-8"))
    msg += _encode_field(2, 2, datatype.encode("utf-8"))
    for s in shape:
        msg += _encode_field(3, 0, _encode_varint(s))
    msg += _encode_field(5, 2, raw_data)
    return msg


def _build_model_infer_request_pb(
    model_name: str,
    model_version: str,
    inputs: list[bytes],
) -> bytes:
    msg = b""
    msg += _encode_field(1, 2, model_name.encode("utf-8"))
    msg += _encode_field(2, 2, model_version.encode("utf-8"))
    for inp in inputs:
        msg += _encode_field(5, 2, inp)
    return msg


def _encode_string_for_triton(text: str) -> bytes:
    text_bytes = text.encode("utf-8")
    return struct.pack("<I", len(text_bytes)) + text_bytes


def _build_triton_infer_request_pb(
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

    raw_data = _encode_string_for_triton(prompt)
    input_tensor = _build_infer_input_tensor_pb(
        name="text_input",
        datatype="BYTES",
        shape=[1, 1],
        raw_data=raw_data,
    )

    return _build_model_infer_request_pb(
        model_name=model_name,
        model_version=model_version,
        inputs=[input_tensor],
    )


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if not (byte & 0x80):
            break
        shift += 7
    return result, offset


def _parse_triton_response_pb(data: bytes, model_name_display: str) -> dict[str, Any]:
    text_output = ""
    offset = 0
    while offset < len(data):
        tag, offset = _decode_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 0x07

        if wire_type == 2:
            length, offset = _decode_varint(data, offset)
            field_data = data[offset:offset + length]
            offset += length

            if field_number == 6:
                inner_offset = 0
                output_name = ""
                raw_output_data = b""
                while inner_offset < len(field_data):
                    inner_tag, inner_offset = _decode_varint(field_data, inner_offset)
                    inner_field = inner_tag >> 3
                    inner_wire = inner_tag & 0x07
                    if inner_wire == 2:
                        inner_len, inner_offset = _decode_varint(field_data, inner_offset)
                        inner_field_data = field_data[inner_offset:inner_offset + inner_len]
                        inner_offset += inner_len
                        if inner_field == 1:
                            output_name = inner_field_data.decode("utf-8")
                        elif inner_field == 5:
                            raw_output_data = inner_field_data
                    elif inner_wire == 0:
                        _, inner_offset = _decode_varint(field_data, inner_offset)
                    else:
                        break

                if output_name == "text_output" and raw_output_data:
                    if len(raw_output_data) >= 4:
                        str_len = struct.unpack("<I", raw_output_data[:4])[0]
                        text_output = raw_output_data[4:4 + str_len].decode("utf-8")
        elif wire_type == 0:
            _, offset = _decode_varint(data, offset)
        elif wire_type == 1:
            offset += 8
        elif wire_type == 5:
            offset += 4
        else:
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
            provider_response = _parse_triton_response_pb(provider_response, model_name)

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
        request_bytes = _build_triton_infer_request_pb(payload, model_name, model_version)

        try:
            response_bytes = await channel.unary_unary(
                "/inference.GRPCInferenceService/ModelInfer",
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request_bytes, timeout=timeout)
            return _parse_triton_response_pb(response_bytes, display_model or model_name)
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
        request_bytes = _build_triton_infer_request_pb(payload, model_name, model_version)

        try:
            call = channel.unary_stream(
                "/inference.GRPCInferenceService/ModelStreamInfer",
                request_serializer=lambda x: x,
                response_deserializer=lambda x: x,
            )(request_bytes, timeout=timeout)

            async for response_bytes in call:
                parsed = _parse_triton_response_pb(response_bytes, model_name)
                text_output = ""
                if parsed.get("choices"):
                    text_output = parsed["choices"][0].get("message", {}).get("content", "")
                chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": text_output}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}"
            yield "data: [DONE]"
        except Exception as exc:
            raise self.map_error(exc) from exc
