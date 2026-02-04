"""Tests for Google Gemini provider."""

import pytest
from deltallm.providers import GeminiProvider
from deltallm.types import CompletionRequest, Message


class TestGeminiProvider:
    """Test Google Gemini provider."""
    
    @pytest.fixture
    def provider(self):
        """Create Gemini provider fixture."""
        return GeminiProvider()
    
    def test_init(self, provider):
        """Test provider initialization."""
        assert provider.provider_name == "gemini"
        assert provider.use_vertex is False
    
    def test_init_with_vertex(self):
        """Test provider initialization with Vertex AI."""
        provider = GeminiProvider(use_vertex=True)
        assert provider.use_vertex is True
        assert "aiplatform" in provider.base_url
    
    def test_get_api_key_missing(self, provider, monkeypatch):
        """Test error when API key is missing."""
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        
        with pytest.raises(Exception):  # AuthenticationError
            provider._get_api_key()
    
    def test_get_api_key_from_env(self, provider, monkeypatch):
        """Test getting API key from environment."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
        assert provider._get_api_key() == "test-google-key"
    
    def test_get_model_name(self, provider):
        """Test model name extraction."""
        assert provider._get_model_name("gemini/gemini-1.5-pro") == "gemini-1.5-pro"
        assert provider._get_model_name("gemini-1.5-pro") == "gemini-1.5-pro"
        assert provider._get_model_name("vertex/gemini-pro") == "gemini-pro"
    
    def test_transform_request_basic(self, provider):
        """Test basic request transformation."""
        request = CompletionRequest(
            model="gemini-1.5-pro",
            messages=[Message.user("Hello")],
            temperature=0.7,
            max_tokens=100,
        )
        
        result = provider.transform_request(request)
        assert result["_needs_async_transform"] is True
    
    def test_transform_response(self, provider):
        """Test response transformation."""
        gemini_response = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "Hello there!"}]
                },
                "finishReason": "STOP",
            }],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 15,
            },
        }
        
        result = provider.transform_response(gemini_response)
        
        assert result.choices[0].message["content"] == "Hello there!"
        assert result.choices[0].finish_reason == "stop"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5
        assert result.usage.total_tokens == 15
    
    def test_transform_response_safety(self, provider):
        """Test response with safety finish reason."""
        gemini_response = {
            "candidates": [{
                "content": {"parts": []},
                "finishReason": "SAFETY",
            }],
            "usageMetadata": {},
        }
        
        result = provider.transform_response(gemini_response)
        assert result.choices[0].finish_reason == "content_filter"
    
    def test_build_url_ai_studio(self, provider, monkeypatch):
        """Test building URL for AI Studio."""
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        
        url = provider._build_url("gemini-1.5-pro", stream=False)
        assert "generativelanguage.googleapis.com" in url
        assert "gemini-1.5-pro:generateContent" in url
        assert "key=test-key" in url
    
    def test_capabilities(self, provider):
        """Test provider capabilities."""
        assert provider.capabilities.chat is True
        assert provider.capabilities.streaming is True
        assert provider.capabilities.embeddings is True
        assert provider.capabilities.vision is True
        assert provider.capabilities.tools is True
