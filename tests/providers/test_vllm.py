"""Tests for vLLM provider."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


class TestVLLMProvider:
    """Test cases for VLLMProvider."""
    
    @pytest.fixture
    def provider(self):
        """Create a vLLM provider instance."""
        from deltallm.providers import VLLMProvider
        return VLLMProvider(api_base="http://localhost:8000/v1")
    
    def test_provider_initialization(self, provider):
        """Test provider initialization."""
        assert provider.provider_name == "vllm"
        assert provider.api_base == "http://localhost:8000/v1"
        assert provider.capabilities.chat is True
        assert provider.capabilities.streaming is True
    
    def test_provider_initialization_default_base(self):
        """Test provider initialization with default API base."""
        from deltallm.providers import VLLMProvider
        prov = VLLMProvider()
        assert prov.api_base == "http://localhost:8000/v1"
    
    def test_transform_request_chat(self, provider):
        """Test request transformation for chat completion."""
        from deltallm.types import CompletionRequest, Message
        
        request = CompletionRequest(
            model="meta-llama/Llama-3-8b-chat-hf",
            messages=[
                Message(role="system", content="You are a helpful assistant."),
                Message(role="user", content="Hello!"),
            ],
            temperature=0.7,
            max_tokens=100,
        )
        
        body = provider.transform_request(request)
        
        assert body["model"] == "meta-llama/Llama-3-8b-chat-hf"
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert body["temperature"] == 0.7
        assert body["max_tokens"] == 100
    
    def test_transform_response(self, provider):
        """Test response transformation."""
        vllm_response = {
            "id": "vllm-123",
            "model": "meta-llama/Llama-3-8b-chat-hf",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello! How can I help you?",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
            "created": 1234567890,
        }
        
        response = provider.transform_response(vllm_response, "meta-llama/Llama-3-8b-chat-hf")
        
        assert response.model == "meta-llama/Llama-3-8b-chat-hf"
        assert len(response.choices) == 1
        assert response.choices[0].message["content"] == "Hello! How can I help you?"
        assert response.usage.prompt_tokens == 20
        assert response.usage.completion_tokens == 10
    
    def test_transform_stream_chunk(self, provider):
        """Test stream chunk transformation."""
        chunk = {
            "id": "vllm-chunk-1",
            "model": "meta-llama/Llama-3-8b-chat-hf",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "Hello",
                    },
                    "finish_reason": None,
                }
            ],
        }
        
        result = provider.transform_stream_chunk(chunk, "meta-llama/Llama-3-8b-chat-hf")
        
        assert result is not None
        assert result.model == "meta-llama/Llama-3-8b-chat-hf"
        assert result.choices[0].delta.content == "Hello"
    
    def test_get_model_info(self, provider):
        """Test getting model info."""
        info = provider.get_model_info("meta-llama/Llama-3-8b-chat-hf")
        
        assert info.id == "meta-llama/Llama-3-8b-chat-hf"
        assert info.name == "Llama-3-8b-chat-hf"
        assert info.max_tokens == 8192
        assert info.provider == "vllm"
        # Self-hosted = $0
        assert info.input_cost_per_token == 0.0
        assert info.output_cost_per_token == 0.0
    
    def test_get_model_info_default(self, provider):
        """Test getting info for unknown model."""
        info = provider.get_model_info("unknown-model")
        
        assert info.id == "unknown-model"
        assert info.max_tokens == 4096  # Default
        assert info.provider == "vllm"
    
    def test_supports_model(self, provider):
        """Test supports_model method."""
        assert provider.supports_model("meta-llama/Llama-3-8b-chat-hf") is True
        assert provider.supports_model("unknown-model") is True  # Returns default info
    
    @pytest.mark.asyncio
    async def test_chat_completion_error_handling(self):
        """Test error handling for chat completion."""
        from deltallm.types import CompletionRequest, Message
        from deltallm.exceptions import APIConnectionError
        
        # Use a non-existent server to trigger connection error
        from deltallm.providers import VLLMProvider
        provider = VLLMProvider(api_base="http://localhost:59999/v1")
        
        request = CompletionRequest(
            model="meta-llama/Llama-3-8b-chat-hf",
            messages=[Message(role="user", content="Hello")],
        )
        
        # Test connection error
        with pytest.raises(APIConnectionError):
            await provider.chat_completion(request)
    
    @pytest.mark.asyncio
    async def test_chat_completion_stream_error_handling(self):
        """Test error handling for streaming."""
        from deltallm.types import CompletionRequest, Message
        from deltallm.exceptions import APIConnectionError
        
        # Use a non-existent server to trigger connection error
        from deltallm.providers import VLLMProvider
        provider = VLLMProvider(api_base="http://localhost:59999/v1")
        
        request = CompletionRequest(
            model="meta-llama/Llama-3-8b-chat-hf",
            messages=[Message(role="user", content="Hello")],
        )
        
        with pytest.raises(APIConnectionError):
            async for chunk in provider.chat_completion_stream(request):
                pass


class TestVLLMProviderCapabilities:
    """Test vLLM provider capabilities."""
    
    def test_capabilities(self):
        """Test provider capabilities."""
        from deltallm.providers import VLLMProvider
        
        provider = VLLMProvider()
        caps = provider.capabilities
        
        assert caps.chat is True
        assert caps.streaming is True
        assert caps.embeddings is False  # vLLM doesn't support embeddings by default
        assert caps.images is False
        assert caps.audio is False
        assert caps.tools is True  # Some versions support tools
        assert caps.vision is False
        assert caps.json_mode is True
