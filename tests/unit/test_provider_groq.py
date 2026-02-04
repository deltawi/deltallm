"""Tests for Groq provider."""

import pytest
from deltallm.providers import GroqProvider
from deltallm.types import CompletionRequest, Message


class TestGroqProvider:
    """Test Groq provider."""
    
    @pytest.fixture
    def provider(self):
        """Create Groq provider fixture."""
        return GroqProvider()
    
    def test_init(self, provider):
        """Test provider initialization."""
        assert provider.provider_name == "groq"
    
    def test_inherits_from_openai(self, provider):
        """Test that Groq inherits from OpenAI provider."""
        from deltallm.providers import OpenAIProvider
        assert isinstance(provider, OpenAIProvider)
    
    def test_transform_request(self, provider):
        """Test request transformation."""
        request = CompletionRequest(
            model="llama-3.1-70b-versatile",
            messages=[Message.user("Hello")],
            temperature=0.7,
            max_tokens=100,
        )
        
        data = provider.transform_request(request)
        
        assert data["model"] == "llama-3.1-70b-versatile"
        assert data["temperature"] == 0.7
        assert data["max_tokens"] == 100
    
    def test_supported_models(self, provider):
        """Test that Groq has supported models defined."""
        assert len(provider.supported_models) > 0
        # New models (GPT OSS, Kimi, Llama 4, Llama 3.3, Qwen)
        assert "gpt-oss-20b" in provider.supported_models
        assert "kimi-k2-0905-1t" in provider.supported_models
        assert "llama-4-scout-17b-16e" in provider.supported_models
        assert "llama-3.3-70b-versatile" in provider.supported_models
        assert "qwen3-32b" in provider.supported_models
        # Legacy models
        assert "llama-3.1-70b-versatile" in provider.supported_models
        assert "mixtral-8x7b-32768" in provider.supported_models
    
    def test_capabilities(self, provider):
        """Test provider capabilities."""
        assert provider.capabilities.chat is True
        assert provider.capabilities.streaming is True
        assert provider.capabilities.vision is True
        assert provider.capabilities.tools is True
    
    def test_vision_support(self, provider):
        """Test vision support detection."""
        # Llama 4 models support vision
        assert provider.supports_vision("llama-4-scout-17b-16e") is True
        assert provider.supports_vision("llama-4-scout-17b-16e-128k") is True
        assert provider.supports_vision("llama-4-maverick-17b-128e") is True
        assert provider.supports_vision("llama-4-maverick-17b-128e-128k") is True
        
        # Other models don't support vision
        assert provider.supports_vision("llama-3.3-70b-versatile") is False
        assert provider.supports_vision("llama-3.1-8b-instant") is False
        assert provider.supports_vision("mixtral-8x7b-32768") is False
    
    def test_get_model_info(self, provider):
        """Test getting model info."""
        # Vision model
        info = provider.get_model_info("llama-4-scout-17b-16e")
        assert info.id == "llama-4-scout-17b-16e"
        assert info.max_tokens == 128000
        assert info.supports_vision is True
        assert info.supports_tools is True
        
        # Non-vision model
        info = provider.get_model_info("llama-3.3-70b-versatile")
        assert info.supports_vision is False
        
        # Legacy model with smaller context
        info = provider.get_model_info("mixtral-8x7b-32768")
        assert info.max_tokens == 32768
    
    def test_get_model_info_with_prefix(self, provider):
        """Test getting model info with groq/ prefix."""
        info = provider.get_model_info("groq/llama-4-scout-17b-16e")
        assert info.id == "llama-4-scout-17b-16e"
        assert info.supports_vision is True

    def test_chat_completion_signature(self, provider):
        """Test that chat_completion has correct optional parameters."""
        import inspect
        sig = inspect.signature(provider.chat_completion)
        params = sig.parameters

        # api_key should be optional with default None
        assert "api_key" in params
        assert params["api_key"].default is None

        # api_base should be optional with default None
        assert "api_base" in params
        assert params["api_base"].default is None

    def test_chat_completion_stream_signature(self, provider):
        """Test that chat_completion_stream has correct optional parameters."""
        import inspect
        sig = inspect.signature(provider.chat_completion_stream)
        params = sig.parameters

        # api_key should be optional with default None
        assert "api_key" in params
        assert params["api_key"].default is None

        # api_base should be optional with default None
        assert "api_base" in params
        assert params["api_base"].default is None
