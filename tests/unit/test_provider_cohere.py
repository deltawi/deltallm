"""Tests for Cohere provider."""

import pytest
from deltallm.providers import CohereProvider
from deltallm.types import CompletionRequest, Message


class TestCohereProvider:
    """Test Cohere provider."""
    
    @pytest.fixture
    def provider(self):
        """Create Cohere provider fixture."""
        return CohereProvider()
    
    def test_init(self, provider):
        """Test provider initialization."""
        assert provider.provider_name == "cohere"
        assert provider.api_base == "https://api.cohere.com/v2"
    
    def test_get_model_name(self, provider):
        """Test model name extraction."""
        assert provider._get_model_name("cohere/command-r") == "command-r"
        assert provider._get_model_name("command-r-plus") == "command-r-plus"
    
    def test_transform_messages(self, provider):
        """Test message transformation."""
        messages = [
            Message.system("You are helpful"),
            Message.user("Hello"),
        ]
        
        result = provider._transform_messages(messages)
        
        # Cohere doesn't have system role, it goes in preamble
        assert "preamble" in result
        assert result["preamble"] == "You are helpful"
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"
    
    def test_transform_request(self, provider):
        """Test request transformation."""
        request = CompletionRequest(
            model="command-r",
            messages=[Message.user("Hello")],
            temperature=0.7,
            max_tokens=100,
        )
        
        data = provider.transform_request(request)
        
        assert data["model"] == "command-r"
        assert data["temperature"] == 0.7
        assert "max_tokens" in data
        assert "messages" in data
    
    def test_transform_response(self, provider):
        """Test response transformation."""
        cohere_response = {
            "response_id": "resp-123",
            "text": "Hello there!",
            "generation_id": "gen-456",
            "finish_reason": "COMPLETE",
            "meta": {
                "tokens": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                }
            }
        }
        
        result = provider.transform_response(cohere_response, "command-r")
        
        assert result.choices[0].message["content"] == "Hello there!"
        assert result.choices[0].finish_reason == "stop"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5
    
    def test_capabilities(self, provider):
        """Test provider capabilities."""
        assert provider.capabilities.chat is True
        assert provider.capabilities.streaming is True
        assert provider.capabilities.embeddings is True
