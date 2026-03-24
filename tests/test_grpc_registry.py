from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.providers.registry import resolve_chat_upstream
from src.providers.grpc_channel import GrpcChannelManager
from src.providers.vllm_grpc import VLLMGrpcAdapter
from src.providers.triton_grpc import TritonGrpcAdapter


def _make_app_state():
    state = MagicMock()
    cm = GrpcChannelManager()
    state.vllm_grpc_adapter = VLLMGrpcAdapter(cm)
    state.triton_grpc_adapter = TritonGrpcAdapter(cm)
    state.openai_adapter = MagicMock()
    state.anthropic_adapter = MagicMock()
    state.azure_openai_adapter = MagicMock()
    state.gemini_adapter = MagicMock()
    state.bedrock_adapter = MagicMock()
    state.settings = MagicMock()
    state.settings.openai_base_url = "https://api.openai.com/v1"
    return state


def _make_request(app_state):
    request = MagicMock()
    request.app.state = app_state
    return request


class TestResolveGrpcUpstream:
    def test_vllm_grpc_transport(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "vllm",
            "model": "meta-llama/Llama-3-8b",
            "transport": "grpc",
            "grpc_address": "localhost:50051",
            "api_key": "test-key",
        }
        upstream = resolve_chat_upstream(request, params)
        assert upstream.transport == "grpc"
        assert upstream.grpc_address == "localhost:50051"
        assert isinstance(upstream.adapter, VLLMGrpcAdapter)

    def test_triton_grpc_transport(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "triton",
            "model": "ensemble_llm",
            "transport": "grpc",
            "grpc_address": "localhost:8001",
            "triton_model_name": "my_model",
            "triton_model_version": "1",
        }
        upstream = resolve_chat_upstream(request, params)
        assert upstream.transport == "grpc"
        assert upstream.grpc_address == "localhost:8001"
        assert isinstance(upstream.adapter, TritonGrpcAdapter)
        assert upstream.grpc_metadata["triton_model_name"] == "my_model"
        assert upstream.grpc_metadata["triton_model_version"] == "1"

    def test_vllm_http_fallback(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "vllm",
            "model": "meta-llama/Llama-3-8b",
            "transport": "http",
            "api_key": "test-key",
            "api_base": "http://localhost:8000/v1",
        }
        upstream = resolve_chat_upstream(request, params)
        assert upstream.transport == "http"
        assert upstream.grpc_address is None

    def test_grpc_with_http_fallback_base(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "vllm",
            "model": "meta-llama/Llama-3-8b",
            "transport": "grpc",
            "grpc_address": "localhost:50051",
            "http_fallback_base": "http://localhost:8000/v1",
            "api_key": "test-key",
        }
        upstream = resolve_chat_upstream(request, params)
        assert upstream.transport == "grpc"
        assert upstream.api_base == "http://localhost:8000/v1"

    def test_default_transport_is_http(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "vllm",
            "model": "meta-llama/Llama-3-8b",
            "api_key": "test-key",
            "api_base": "http://localhost:8000/v1",
        }
        upstream = resolve_chat_upstream(request, params)
        assert upstream.transport == "http"

    def test_grpc_prefix_in_api_base(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "vllm",
            "model": "meta-llama/Llama-3-8b",
            "api_base": "grpc://localhost:50051",
            "api_key": "test-key",
        }
        upstream = resolve_chat_upstream(request, params)
        assert upstream.transport == "grpc"
        assert upstream.grpc_address == "localhost:50051"

    def test_grpc_prefix_with_explicit_grpc_address(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "vllm",
            "model": "meta-llama/Llama-3-8b",
            "api_base": "grpc://some-host:50051",
            "grpc_address": "actual-host:50051",
            "api_key": "test-key",
        }
        upstream = resolve_chat_upstream(request, params)
        assert upstream.transport == "grpc"
        assert upstream.grpc_address == "actual-host:50051"

    def test_unsupported_provider_grpc_rejected(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "openai",
            "model": "gpt-4",
            "transport": "grpc",
            "grpc_address": "localhost:50051",
            "api_key": "test-key",
        }
        from src.models.errors import InvalidRequestError
        with pytest.raises(InvalidRequestError, match="does not support gRPC"):
            resolve_chat_upstream(request, params)

    def test_unsupported_provider_grpc_prefix_rejected(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "openai",
            "model": "gpt-4",
            "api_base": "grpc://localhost:50051",
            "api_key": "test-key",
        }
        from src.models.errors import InvalidRequestError
        with pytest.raises(InvalidRequestError, match="does not support gRPC"):
            resolve_chat_upstream(request, params)

    def test_grpc_prefix_strips_for_http_fallback(self):
        state = _make_app_state()
        request = _make_request(state)
        params = {
            "provider": "vllm",
            "model": "meta-llama/Llama-3-8b",
            "api_base": "grpc://localhost:50051",
            "api_key": "test-key",
        }
        upstream = resolve_chat_upstream(request, params)
        assert upstream.transport == "grpc"
        assert upstream.api_base == ""
