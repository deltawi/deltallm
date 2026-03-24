from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.providers.grpc_channel import GrpcChannelManager, GRPC_AVAILABLE
from src.providers.vllm_grpc import VLLMGrpcAdapter, VLLM_GRPC_UNARY_METHOD, VLLM_GRPC_STREAM_METHOD
from src.providers.triton_grpc import (
    TritonGrpcAdapter,
    _build_triton_infer_request_pb,
    _parse_triton_response_pb,
)
from src.config import GrpcSettings

needs_grpcio = pytest.mark.skipif(not GRPC_AVAILABLE, reason="grpcio is not installed")


class TestGrpcSettingsConfig:
    def test_defaults(self):
        settings = GrpcSettings()
        assert settings.max_pool_size == 8
        assert settings.keepalive_time_ms == 30_000
        assert settings.keepalive_timeout_ms == 10_000
        assert settings.max_message_length == 64 * 1024 * 1024

    def test_custom_values(self):
        settings = GrpcSettings(
            max_pool_size=16,
            keepalive_time_ms=60_000,
            keepalive_timeout_ms=20_000,
            max_message_length=128 * 1024 * 1024,
        )
        assert settings.max_pool_size == 16
        assert settings.keepalive_time_ms == 60_000

    def test_validation_bounds(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            GrpcSettings(max_pool_size=0)
        with pytest.raises(ValidationError):
            GrpcSettings(max_pool_size=200)
        with pytest.raises(ValidationError):
            GrpcSettings(keepalive_time_ms=500)


class TestChannelManagerConfigurable:
    def test_configurable_pool_size(self):
        mgr = GrpcChannelManager(max_pool_size=4)
        assert mgr._max_pool_size == 4

    def test_configurable_keepalive(self):
        mgr = GrpcChannelManager(keepalive_time_ms=60_000, keepalive_timeout_ms=20_000)
        assert mgr._keepalive_time_ms == 60_000
        assert mgr._keepalive_timeout_ms == 20_000

    def test_configurable_message_length(self):
        mgr = GrpcChannelManager(max_message_length=128 * 1024 * 1024)
        assert mgr._max_message_length == 128 * 1024 * 1024


class TestVLLMRpcMethods:
    def test_unary_method_path(self):
        assert VLLM_GRPC_UNARY_METHOD == "/vllm.EntrypointsService/Chat"

    def test_stream_method_path(self):
        assert VLLM_GRPC_STREAM_METHOD == "/vllm.EntrypointsService/ChatStream"


def _mock_grpc_unary(response_bytes: bytes):
    """Create a mock gRPC channel with unary_unary that returns response_bytes."""
    mock_channel = MagicMock()
    mock_callable = MagicMock()

    async def _do_call(request_bytes, timeout=None, metadata=None):
        _do_call.last_request = request_bytes
        _do_call.last_timeout = timeout
        _do_call.last_metadata = metadata
        return response_bytes

    mock_callable.side_effect = _do_call
    mock_callable.last_request = None
    mock_callable.last_metadata = None
    mock_channel.unary_unary = MagicMock(return_value=mock_callable)
    mock_channel._callable = mock_callable
    return mock_channel


def _mock_grpc_stream(response_chunks: list[bytes]):
    """Create a mock gRPC channel with unary_stream that yields response chunks."""
    mock_channel = MagicMock()

    class _MockCall:
        def __init__(self):
            self.last_metadata = None

        def __call__(self, request_bytes, timeout=None, metadata=None):
            self.last_metadata = metadata
            return self

        async def __aiter__(self):
            for chunk in response_chunks:
                yield chunk

    mock_call = _MockCall()
    mock_channel.unary_stream = MagicMock(return_value=mock_call)
    mock_channel._mock_call = mock_call
    return mock_channel


@needs_grpcio
class TestVLLMAuthMetadata:
    @pytest.mark.asyncio
    async def test_auth_metadata_sent_in_unary(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)

        response = json.dumps({
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        mock_channel = _mock_grpc_unary(response)

        with patch.object(cm, "get_channel", new_callable=AsyncMock, return_value=mock_channel):
            result = await adapter.execute_grpc_chat(
                "localhost:50051",
                {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
                api_key="sk-test-key",
            )

        assert mock_channel._callable.last_metadata == [("authorization", "Bearer sk-test-key")]
        assert result["choices"][0]["message"]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_no_metadata_without_api_key(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)

        response = json.dumps({
            "id": "chatcmpl-test",
            "choices": [{"message": {"role": "assistant", "content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }).encode()
        mock_channel = _mock_grpc_unary(response)

        with patch.object(cm, "get_channel", new_callable=AsyncMock, return_value=mock_channel):
            await adapter.execute_grpc_chat(
                "localhost:50051",
                {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
            )

        assert mock_channel._callable.last_metadata is None


@needs_grpcio
class TestTritonAuthMetadata:
    @pytest.mark.asyncio
    async def test_auth_metadata_sent_in_unary(self):
        cm = GrpcChannelManager()
        adapter = TritonGrpcAdapter(cm)

        mock_response = _build_mock_triton_response("World")
        mock_channel = _mock_grpc_unary(mock_response)

        with patch.object(cm, "get_channel", new_callable=AsyncMock, return_value=mock_channel):
            await adapter.execute_grpc_chat(
                "localhost:8001",
                {"model": "test", "messages": [{"role": "user", "content": "hi"}]},
                model_name="ensemble_llm",
                api_key="triton-key",
            )

        assert mock_channel._callable.last_metadata == [("authorization", "Bearer triton-key")]


@needs_grpcio
class TestVLLMEndToEndUnary:
    @pytest.mark.asyncio
    async def test_full_unary_flow(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)

        openai_response = {
            "id": "chatcmpl-abc123",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "meta-llama/Llama-3-8b",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "The answer is 42."},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_channel = _mock_grpc_unary(json.dumps(openai_response).encode())

        with patch.object(cm, "get_channel", new_callable=AsyncMock, return_value=mock_channel):
            data = await adapter.execute_grpc_chat(
                "localhost:50051",
                {"model": "meta-llama/Llama-3-8b", "messages": [{"role": "user", "content": "What is the meaning of life?"}]},
                timeout=30,
                api_key="test-key",
            )

        assert data["id"] == "chatcmpl-abc123"
        assert data["choices"][0]["message"]["content"] == "The answer is 42."
        assert data["usage"]["total_tokens"] == 15

        sent_bytes = mock_channel._callable.last_request
        sent_payload = json.loads(sent_bytes.decode())
        assert sent_payload["model"] == "meta-llama/Llama-3-8b"
        assert sent_payload["messages"][0]["content"] == "What is the meaning of life?"
        assert mock_channel._callable.last_metadata == [("authorization", "Bearer test-key")]

        mock_channel.unary_unary.assert_called_once()
        rpc_method = mock_channel.unary_unary.call_args[0][0]
        assert rpc_method == VLLM_GRPC_UNARY_METHOD


@needs_grpcio
class TestVLLMEndToEndStream:
    @pytest.mark.asyncio
    async def test_full_streaming_flow(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)

        chunks = [
            json.dumps({"id": "chatcmpl-s1", "object": "chat.completion.chunk", "choices": [{"delta": {"content": "Hello"}, "index": 0}]}).encode(),
            json.dumps({"id": "chatcmpl-s2", "object": "chat.completion.chunk", "choices": [{"delta": {"content": " world"}, "index": 0}]}).encode(),
        ]
        mock_channel = _mock_grpc_stream(chunks)

        with patch.object(cm, "get_channel", new_callable=AsyncMock, return_value=mock_channel):
            lines = []
            async for line in adapter.execute_grpc_stream(
                "localhost:50051",
                {"model": "test", "messages": [{"role": "user", "content": "hi"}], "stream": True},
                api_key="test-key",
            ):
                lines.append(line)

        assert len(lines) == 3
        assert lines[0].startswith("data: ")
        assert lines[-1] == "data: [DONE]"
        first_chunk = json.loads(lines[0][len("data: "):])
        assert "choices" in first_chunk

        mock_channel.unary_stream.assert_called_once()
        rpc_method = mock_channel.unary_stream.call_args[0][0]
        assert rpc_method == VLLM_GRPC_STREAM_METHOD
        assert mock_channel._mock_call.last_metadata == [("authorization", "Bearer test-key")]


@needs_grpcio
class TestTritonEndToEndUnary:
    @pytest.mark.asyncio
    async def test_full_unary_flow(self):
        cm = GrpcChannelManager()
        adapter = TritonGrpcAdapter(cm)

        mock_response = _build_mock_triton_response("Paris is the capital of France.")
        mock_channel = _mock_grpc_unary(mock_response)

        with patch.object(cm, "get_channel", new_callable=AsyncMock, return_value=mock_channel):
            data = await adapter.execute_grpc_chat(
                "localhost:8001",
                {"model": "ensemble_llm", "messages": [{"role": "user", "content": "Capital of France?"}]},
                model_name="ensemble_llm",
                model_version="1",
                timeout=30,
            )

        assert data["choices"][0]["message"]["content"] == "Paris is the capital of France."

        sent_bytes = mock_channel._callable.last_request
        assert isinstance(sent_bytes, bytes)
        assert b"ensemble_llm" in sent_bytes

        mock_channel.unary_unary.assert_called_once()
        rpc_method = mock_channel.unary_unary.call_args[0][0]
        assert rpc_method == "/inference.GRPCInferenceService/ModelInfer"


class TestTritonProtobufRoundTrip:
    def test_build_and_parse_roundtrip(self):
        payload = {
            "model": "ensemble_llm",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "What is 2+2?"},
            ],
        }
        request_bytes = _build_triton_infer_request_pb(payload, "ensemble_llm", "1")

        assert isinstance(request_bytes, bytes)
        assert len(request_bytes) > 0
        assert b"ensemble_llm" in request_bytes

        response_text = "The answer is 4."
        response_bytes = _build_mock_triton_response(response_text)
        parsed = _parse_triton_response_pb(response_bytes, "ensemble_llm")

        assert parsed["choices"][0]["message"]["content"] == response_text
        assert parsed["model"] == "ensemble_llm"


class TestUnsupportedProviderRejection:
    def test_openai_grpc_rejected(self):
        from src.providers.registry import resolve_chat_upstream
        from src.models.errors import InvalidRequestError

        state = MagicMock()
        cm = GrpcChannelManager()
        state.vllm_grpc_adapter = VLLMGrpcAdapter(cm)
        state.triton_grpc_adapter = TritonGrpcAdapter(cm)
        state.openai_adapter = MagicMock()
        state.settings = MagicMock()
        state.settings.openai_base_url = "https://api.openai.com/v1"

        request = MagicMock()
        request.app.state = state

        with pytest.raises(InvalidRequestError, match="does not support gRPC"):
            resolve_chat_upstream(request, {
                "provider": "openai",
                "model": "gpt-4",
                "transport": "grpc",
                "grpc_address": "localhost:50051",
            })

    def test_anthropic_grpc_rejected(self):
        from src.providers.registry import resolve_chat_upstream
        from src.models.errors import InvalidRequestError

        state = MagicMock()
        cm = GrpcChannelManager()
        state.vllm_grpc_adapter = VLLMGrpcAdapter(cm)
        state.triton_grpc_adapter = TritonGrpcAdapter(cm)
        state.anthropic_adapter = MagicMock()
        state.settings = MagicMock()
        state.settings.openai_base_url = "https://api.openai.com/v1"

        request = MagicMock()
        request.app.state = state

        with pytest.raises(InvalidRequestError, match="does not support gRPC"):
            resolve_chat_upstream(request, {
                "provider": "anthropic",
                "model": "claude-3-opus",
                "api_key": "sk-ant-test",
                "transport": "grpc",
                "grpc_address": "localhost:50051",
            })


def _build_mock_triton_response(text: str) -> bytes:
    from src.providers.triton_grpc import (
        _encode_varint,
        _encode_field,
        _encode_string_for_triton,
    )
    raw_data = _encode_string_for_triton(text)
    output_tensor = b""
    output_tensor += _encode_field(1, 2, b"text_output")
    output_tensor += _encode_field(2, 2, b"BYTES")
    output_tensor += _encode_field(3, 0, _encode_varint(1))
    output_tensor += _encode_field(5, 2, raw_data)

    response = b""
    response += _encode_field(1, 2, b"test_model")
    response += _encode_field(6, 2, output_tensor)
    return response
