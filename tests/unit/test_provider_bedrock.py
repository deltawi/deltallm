"""Tests for AWS Bedrock provider."""

import pytest
from deltallm.providers import AWSBedrockProvider
from deltallm.types import CompletionRequest, Message


class TestAWSBedrockProvider:
    """Test AWS Bedrock provider."""
    
    @pytest.fixture
    def provider(self):
        """Create Bedrock provider fixture."""
        return AWSBedrockProvider()
    
    def test_init(self, provider):
        """Test provider initialization."""
        assert provider.provider_name == "bedrock"
    
    def test_get_model_id(self, provider):
        """Test getting Bedrock model ID."""
        # Claude model
        assert provider._get_model_id("anthropic.claude-3-sonnet") == "anthropic.claude-3-sonnet"
        
        # With provider prefix
        assert provider._get_model_id("bedrock/anthropic.claude-3-haiku") == "anthropic.claude-3-haiku"
    
    def test_get_provider_from_model(self, provider):
        """Test detecting provider from model ID."""
        assert provider._get_provider_from_model("anthropic.claude-3-sonnet") == "anthropic"
        assert provider._get_provider_from_model("meta.llama3-8b") == "meta"
        assert provider._get_provider_from_model("amazon.titan-text") == "amazon"
        assert provider._get_provider_from_model("cohere.command-r") == "cohere"
        assert provider._get_provider_from_model("mistral.mistral-large") == "mistral"
    
    def test_is_streaming_model(self, provider):
        """Test checking if model supports streaming."""
        assert provider._is_streaming_model("anthropic.claude-3-sonnet") is True
        assert provider._is_streaming_model("amazon.titan-text") is False
    
    def test_transform_request_anthropic(self, provider):
        """Test transforming request for Anthropic models."""
        request = CompletionRequest(
            model="anthropic.claude-3-sonnet",
            messages=[Message.user("Hello")],
            temperature=0.7,
            max_tokens=100,
        )
        
        data = provider.transform_request(request)
        
        assert "anthropic_version" in data
        assert data["max_tokens"] == 100
        assert data["temperature"] == 0.7
        assert "messages" in data
    
    def test_transform_request_llama(self, provider):
        """Test transforming request for Llama models."""
        request = CompletionRequest(
            model="meta.llama3-8b-instruct",
            messages=[
                Message.system("You are helpful"),
                Message.user("Hello"),
            ],
            temperature=0.5,
        )
        
        data = provider.transform_request(request)
        
        assert "prompt" in data
        assert "max_gen_len" in data
        assert "temperature" in data
    
    def test_transform_response_anthropic(self, provider):
        """Test transforming Anthropic response."""
        bedrock_response = {
            "id": "msg-123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-3-sonnet",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
            },
        }
        
        result = provider.transform_response(bedrock_response, "anthropic.claude-3-sonnet")
        
        assert result.choices[0].message["content"] == "Hello!"
        assert result.choices[0].finish_reason == "stop"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5
    
    def test_transform_response_llama(self, provider):
        """Test transforming Llama response."""
        bedrock_response = {
            "generation": "Hello there!",
            "prompt_token_count": 10,
            "generation_token_count": 5,
        }
        
        result = provider.transform_response(bedrock_response, "meta.llama3-8b")
        
        assert result.choices[0].message["content"] == "Hello there!"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5
    
    def test_map_finish_reason(self, provider):
        """Test finish reason mapping."""
        assert provider._map_finish_reason("end_turn", "anthropic") == "stop"
        assert provider._map_finish_reason("max_tokens", "anthropic") == "length"
        assert provider._map_finish_reason("stop_sequence", "anthropic") == "stop"
        assert provider._map_finish_reason("length", "meta") == "length"
        assert provider._map_finish_reason(None, "anthropic") is None
    
    def test_capabilities(self, provider):
        """Test provider capabilities."""
        assert provider.capabilities.chat is True
        assert provider.capabilities.streaming is True
        assert provider.capabilities.embeddings is False
        assert provider.capabilities.vision is True
