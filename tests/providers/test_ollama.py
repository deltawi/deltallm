"""Tests for Ollama provider."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx


class TestOllamaProvider:
    """Test cases for OllamaProvider."""
    
    @pytest.fixture
    def provider(self):
        """Create an Ollama provider instance."""
        from deltallm.providers import OllamaProvider
        return OllamaProvider(api_base="http://localhost:11434")
    
    def test_provider_initialization(self, provider):
        """Test provider initialization."""
        assert provider.provider_name == "ollama"
        assert provider.api_base == "http://localhost:11434"
        assert provider.capabilities.chat is True
        assert provider.capabilities.streaming is True
    
    def test_provider_initialization_default_base(self):
        """Test provider initialization with default API base."""
        from deltallm.providers import OllamaProvider
        prov = OllamaProvider()
        assert prov.api_base == "http://localhost:11434"
    
    def test_transform_request_chat(self, provider):
        """Test request transformation for chat completion."""
        from deltallm.types import CompletionRequest, Message
        
        request = CompletionRequest(
            model="llama3",
            messages=[
                Message(role="system", content="You are a helpful assistant."),
                Message(role="user", content="Hello!"),
            ],
            temperature=0.7,
            max_tokens=100,
        )
        
        body = provider.transform_request(request)
        
        assert body["model"] == "llama3"
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert "options" in body
        assert body["options"]["temperature"] == 0.7
        assert body["options"]["num_predict"] == 100
    
    def test_transform_response(self, provider):
        """Test response transformation."""
        ollama_response = {
            "model": "llama3",
            "message": {
                "role": "assistant",
                "content": "Hello! How can I help you?",
            },
            "done": True,
            "prompt_eval_count": 20,
            "eval_count": 10,
        }
        
        response = provider.transform_response(ollama_response, "llama3")
        
        assert response.model == "llama3"
        assert len(response.choices) == 1
        assert response.choices[0].message["content"] == "Hello! How can I help you?"
        assert response.usage.prompt_tokens == 20
        assert response.usage.completion_tokens == 10
    
    def test_transform_stream_chunk(self, provider):
        """Test stream chunk transformation."""
        chunk = {
            "model": "llama3",
            "message": {
                "role": "assistant",
                "content": "Hello",
            },
            "done": False,
        }
        
        result = provider.transform_stream_chunk(chunk, "llama3")
        
        assert result is not None
        assert result.model == "llama3"
        assert result.choices[0].delta.content == "Hello"
    
    def test_transform_stream_chunk_done(self, provider):
        """Test stream chunk transformation when done."""
        chunk = {
            "model": "llama3",
            "message": {
                "role": "assistant",
                "content": "",
            },
            "done": True,
        }
        
        result = provider.transform_stream_chunk(chunk, "llama3")
        
        assert result is not None
        assert result.choices[0].finish_reason == "stop"
    
    def test_get_model_info(self, provider):
        """Test getting model info."""
        info = provider.get_model_info("llama3")
        
        assert info.id == "llama3"
        assert info.name == "llama3"
        assert info.max_tokens == 8192
        assert info.provider == "ollama"
        # Self-hosted = $0
        assert info.input_cost_per_token == 0.0
        assert info.output_cost_per_token == 0.0
    
    def test_get_model_info_phi3(self, provider):
        """Test getting info for Phi-3 model."""
        info = provider.get_model_info("phi3")
        
        assert info.id == "phi3"
        assert info.max_tokens == 128000  # Phi-3 has large context
    
    def test_get_model_info_default(self, provider):
        """Test getting info for unknown model."""
        info = provider.get_model_info("unknown-model")
        
        assert info.id == "unknown-model"
        assert info.max_tokens == 4096  # Default
        assert info.provider == "ollama"
    
    def test_supports_model(self, provider):
        """Test supports_model method."""
        assert provider.supports_model("llama3") is True
        assert provider.supports_model("mistral") is True
        assert provider.supports_model("unknown-model") is True  # Returns default info
    
    @pytest.mark.asyncio
    async def test_chat_completion_error_handling(self, provider):
        """Test error handling for chat completion."""
        from deltallm.types import CompletionRequest, Message
        from deltallm.exceptions import APIConnectionError
        
        request = CompletionRequest(
            model="llama3",
            messages=[Message(role="user", content="Hello")],
        )
        
        # Test connection error
        with pytest.raises(APIConnectionError):
            # This will fail since there's no real server
            await provider.chat_completion(request)
    
    @pytest.mark.asyncio
    async def test_chat_completion_stream_error_handling(self, provider):
        """Test error handling for streaming."""
        from deltallm.types import CompletionRequest, Message
        from deltallm.exceptions import APIConnectionError
        
        request = CompletionRequest(
            model="llama3",
            messages=[Message(role="user", content="Hello")],
        )
        
        with pytest.raises(APIConnectionError):
            async for chunk in provider.chat_completion_stream(request):
                pass


class TestOllamaProviderEmbeddings:
    """Test Ollama embeddings support."""
    
    @pytest.fixture
    def provider(self):
        """Create an Ollama provider instance."""
        from deltallm.providers import OllamaProvider
        return OllamaProvider()
    
    def test_embeddings_capability(self, provider):
        """Test that embeddings are supported."""
        assert provider.capabilities.embeddings is True
    
    @pytest.mark.asyncio
    async def test_embedding_request(self, provider):
        """Test embedding request transformation."""
        from deltallm.exceptions import APIConnectionError
        
        # Mock embedding request
        class MockEmbeddingRequest:
            model = "llama3"
            input = "Hello world"
        
        request = MockEmbeddingRequest()
        
        # Will fail due to no server
        with pytest.raises(APIConnectionError):
            await provider.embedding(request)


class TestOllamaProviderCapabilities:
    """Test Ollama provider capabilities."""
    
    def test_capabilities(self):
        """Test provider capabilities."""
        from deltallm.providers import OllamaProvider
        
        provider = OllamaProvider()
        caps = provider.capabilities
        
        assert caps.chat is True
        assert caps.streaming is True
        assert caps.embeddings is True  # Ollama supports embeddings
        assert caps.images is False
        assert caps.audio is False
        assert caps.tools is False  # Limited tool support
        assert caps.vision is False
        assert caps.json_mode is False
