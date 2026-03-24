from __future__ import annotations

import pytest

from src.providers.grpc_channel import GrpcChannelManager
from src.providers.vllm_grpc import VLLMGrpcAdapter
from src.providers.triton_grpc import (
    TritonGrpcAdapter,
    _build_triton_infer_request_pb,
    _parse_triton_response_pb,
    _encode_string_for_triton,
)


class TestVLLMGrpcAdapter:
    def test_provider_name(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)
        assert adapter.provider_name == "vllm"

    @pytest.mark.asyncio
    async def test_translate_request(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)

        class FakeMessage:
            role = "user"
            content = "Hello"

        class FakeRequest:
            messages = [FakeMessage()]
            temperature = 0.7
            top_p = None
            max_tokens = 100
            stop = None
            stream = False

        config = {"model": "meta-llama/Llama-3-8b", "provider": "vllm"}
        result = await adapter.translate_request(FakeRequest(), config)

        assert result["model"] == "meta-llama/Llama-3-8b"
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "Hello"
        assert result["temperature"] == 0.7
        assert result["max_tokens"] == 100
        assert "top_p" not in result

    @pytest.mark.asyncio
    async def test_translate_response(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)

        provider_response = {
            "id": "chatcmpl-abc123",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "llama-3",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi there!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

        result = await adapter.translate_response(provider_response, "my-model")
        assert result.model == "my-model"
        assert result.choices[0].message.content == "Hi there!"
        assert result.usage.prompt_tokens == 10

    @pytest.mark.asyncio
    async def test_health_check_no_address(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)
        result = await adapter.health_check({})
        assert result is False

    def test_map_error_passthrough(self):
        cm = GrpcChannelManager()
        adapter = VLLMGrpcAdapter(cm)
        original = ValueError("test error")
        assert adapter.map_error(original) is original


class TestTritonGrpcAdapter:
    def test_provider_name(self):
        cm = GrpcChannelManager()
        adapter = TritonGrpcAdapter(cm)
        assert adapter.provider_name == "triton"

    @pytest.mark.asyncio
    async def test_translate_request(self):
        cm = GrpcChannelManager()
        adapter = TritonGrpcAdapter(cm)

        class FakeMessage:
            role = "user"
            content = "What is 2+2?"

        class FakeRequest:
            messages = [FakeMessage()]
            temperature = 0.5
            top_p = None
            max_tokens = 50
            stop = None
            stream = False

        config = {"model": "ensemble_llm", "provider": "triton"}
        result = await adapter.translate_request(FakeRequest(), config)

        assert result["model"] == "ensemble_llm"
        assert len(result["messages"]) == 1
        assert result["temperature"] == 0.5

    @pytest.mark.asyncio
    async def test_translate_response_from_dict(self):
        cm = GrpcChannelManager()
        adapter = TritonGrpcAdapter(cm)

        provider_response = {
            "id": "chatcmpl-triton",
            "object": "chat.completion",
            "created": 1700000000,
            "model": "ensemble_llm",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "4"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        }

        result = await adapter.translate_response(provider_response, "my-triton-model")
        assert result.model == "my-triton-model"
        assert result.choices[0].message.content == "4"

    @pytest.mark.asyncio
    async def test_health_check_no_address(self):
        cm = GrpcChannelManager()
        adapter = TritonGrpcAdapter(cm)
        result = await adapter.health_check({})
        assert result is False

    def test_map_error_passthrough(self):
        cm = GrpcChannelManager()
        adapter = TritonGrpcAdapter(cm)
        original = RuntimeError("test")
        assert adapter.map_error(original) is original


class TestTritonProtobufWireFormat:
    def test_encode_string_for_triton(self):
        encoded = _encode_string_for_triton("hello")
        assert len(encoded) == 4 + 5
        import struct
        str_len = struct.unpack("<I", encoded[:4])[0]
        assert str_len == 5
        assert encoded[4:].decode("utf-8") == "hello"

    def test_build_infer_request_pb_is_bytes(self):
        payload = {"messages": [{"role": "user", "content": "test"}]}
        result = _build_triton_infer_request_pb(payload, "my_model", "1")
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_build_infer_request_contains_model_name(self):
        payload = {"messages": [{"role": "user", "content": "test"}]}
        result = _build_triton_infer_request_pb(payload, "ensemble_llm", "")
        assert b"ensemble_llm" in result

    def test_parse_response_pb_returns_dict(self):
        empty_response = b""
        result = _parse_triton_response_pb(empty_response, "test-model")
        assert isinstance(result, dict)
        assert result["model"] == "test-model"
        assert len(result["choices"]) == 1
        assert result["choices"][0]["message"]["role"] == "assistant"
