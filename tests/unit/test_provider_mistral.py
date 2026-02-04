"""Tests for Mistral AI provider."""

import pytest
from deltallm.providers import MistralProvider
from deltallm.types import CompletionRequest, Message


class TestMistralProvider:
    """Test Mistral AI provider."""
    
    @pytest.fixture
    def provider(self):
        """Create Mistral provider fixture."""
        return MistralProvider()
    
    def test_init(self, provider):
        """Test provider initialization."""
        assert provider.provider_name == "mistral"
    
    def test_inherits_from_openai(self, provider):
        """Test that Mistral inherits from OpenAI provider."""
        from deltallm.providers import OpenAIProvider
        assert isinstance(provider, OpenAIProvider)
    
    def test_transform_request(self, provider):
        """Test request transformation."""
        request = CompletionRequest(
            model="mistral-large",
            messages=[Message.user("Hello")],
            temperature=0.7,
            max_tokens=100,
        )
        
        data = provider.transform_request(request)
        
        assert data["model"] == "mistral-large"
        assert data["temperature"] == 0.7
        assert data["max_tokens"] == 100
    
    def test_capabilities(self, provider):
        """Test provider capabilities."""
        assert provider.capabilities.chat is True
        assert provider.capabilities.streaming is True
        assert provider.capabilities.embeddings is True
