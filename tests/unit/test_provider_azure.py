"""Tests for Azure OpenAI provider."""

import pytest
from deltallm.providers import AzureOpenAIProvider
from deltallm.types import CompletionRequest, Message


class TestAzureOpenAIProvider:
    """Test Azure OpenAI provider."""
    
    @pytest.fixture
    def provider(self):
        """Create Azure provider fixture."""
        return AzureOpenAIProvider()
    
    def test_init(self, provider):
        """Test provider initialization."""
        assert provider.provider_name == "azure"
        assert provider.api_version == "2024-02-01"
    
    def test_init_with_api_version(self):
        """Test initialization with custom API version."""
        provider = AzureOpenAIProvider(api_version="2024-06-01")
        assert provider.api_version == "2024-06-01"
    
    def test_transform_request_removes_model(self, provider):
        """Test that model is removed from request body for Azure."""
        request = CompletionRequest(
            model="azure/gpt-4",
            messages=[Message.user("Hello")],
        )
        
        data = provider.transform_request(request)
        assert "model" not in data  # Model is in URL, not body
    
    def test_transform_request_with_deployment(self, provider):
        """Test request transformation with deployment."""
        request = CompletionRequest(
            model="gpt-4-deployment",
            messages=[Message.user("Hello")],
            temperature=0.7,
            max_tokens=100,
        )
        
        data = provider.transform_request(request)
        assert "messages" in data
        assert data["temperature"] == 0.7
        assert data["max_tokens"] == 100
    
    def test_azure_deployment_extraction(self):
        """Test extracting deployment name from model."""
        # Azure models should use azure/ prefix
        provider = AzureOpenAIProvider()
        
        # Test with azure/ prefix
        request = CompletionRequest(
            model="azure/my-deployment",
            messages=[Message.user("Hello")],
        )
        data = provider.transform_request(request)
        assert "model" not in data
    
    def test_inherits_openai_capabilities(self, provider):
        """Test Azure inherits OpenAI capabilities."""
        assert provider.capabilities.chat is True
        assert provider.capabilities.streaming is True
        assert provider.capabilities.embeddings is True
