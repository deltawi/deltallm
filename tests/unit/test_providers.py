"""Tests for provider adapters."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from deltallm.types import (
    CompletionRequest,
    Message,
    EmbeddingRequest,
)
from deltallm.providers.openai import OpenAIProvider
from deltallm.providers.anthropic import AnthropicProvider
from deltallm.providers.registry import ProviderRegistry
from deltallm.exceptions import AuthenticationError, RateLimitError


class TestProviderRegistry:
    """Tests for ProviderRegistry."""
    
    def test_register_and_get(self):
        """Test registering and getting a provider."""
        # Already registered via decorators
        provider_class = ProviderRegistry.get("openai")
        assert provider_class == OpenAIProvider
        
        provider_class = ProviderRegistry.get("anthropic")
        assert provider_class == AnthropicProvider
    
    def test_get_for_model_with_prefix(self):
        """Test getting provider for model with prefix."""
        provider_class = ProviderRegistry.get_for_model("openai/gpt-4o")
        assert provider_class == OpenAIProvider
        
        provider_class = ProviderRegistry.get_for_model("anthropic/claude-3-sonnet")
        assert provider_class == AnthropicProvider
    
    def test_get_for_model_without_prefix(self):
        """Test getting provider for model without prefix."""
        provider_class = ProviderRegistry.get_for_model("gpt-4o")
        assert provider_class == OpenAIProvider
        
        provider_class = ProviderRegistry.get_for_model("claude-3-sonnet")
        assert provider_class == AnthropicProvider
    
    def test_list_providers(self):
        """Test listing registered providers."""
        providers = ProviderRegistry.list_providers()
        assert "openai" in providers
        assert "anthropic" in providers


class TestOpenAIProvider:
    """Tests for OpenAIProvider."""
    
    @pytest.fixture
    def provider(self):
        """Create a test provider."""
        return OpenAIProvider(api_key="test-key")
    
    def test_init(self, provider):
        """Test provider initialization."""
        assert provider.api_key == "test-key"
        assert provider.api_base == "https://api.openai.com/v1"
    
    def test_init_without_key(self):
        """Test initialization without API key."""
        provider = OpenAIProvider()
        assert provider.api_key is None
    
    def test_transform_request_basic(self, provider):
        """Test basic request transformation."""
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message.user("Hello!")],
            temperature=0.5,
        )
        
        data = provider.transform_request(request)
        
        assert data["model"] == "gpt-4o"
        assert len(data["messages"]) == 1
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Hello!"
        assert data["temperature"] == 0.5
    
    def test_transform_request_with_tools(self, provider):
        """Test request transformation with tools."""
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message.user("What's the weather?")],
            tools=[{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}}
                }
            }],
            tool_choice="auto",
        )
        
        data = provider.transform_request(request)
        
        assert "tools" in data
        assert len(data["tools"]) == 1
        assert data["tool_choice"] == "auto"
    
    def test_transform_response(self, provider):
        """Test response transformation."""
        response_data = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1677652288,
            "model": "gpt-4o",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you?"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 10,
                "total_tokens": 20
            }
        }
        
        response = provider.transform_response(response_data, "gpt-4o")
        
        assert response.id == "chatcmpl-123"
        assert response.model == "gpt-4o"
        assert len(response.choices) == 1
        assert response.choices[0].message["content"] == "Hello! How can I help you?"
        assert response.usage.total_tokens == 20
    
    def test_get_model_info(self, provider):
        """Test getting model info."""
        info = provider.get_model_info("gpt-4o")
        
        assert info.id == "gpt-4o"
        assert info.provider == "openai"
        assert info.supports_vision is True
        assert info.supports_tools is True


class TestAnthropicProvider:
    """Tests for AnthropicProvider."""
    
    @pytest.fixture
    def provider(self):
        """Create a test provider."""
        return AnthropicProvider(api_key="test-key")
    
    def test_init(self, provider):
        """Test provider initialization."""
        assert provider.api_key == "test-key"
        assert provider.api_base == "https://api.anthropic.com/v1"
    
    def test_convert_messages(self, provider):
        """Test message conversion."""
        messages = [
            Message.system("You are helpful."),
            Message.user("Hello!"),
        ]
        
        system, anthropic_messages = provider._convert_messages(messages)
        
        assert system == "You are helpful."
        assert len(anthropic_messages) == 1
        assert anthropic_messages[0]["role"] == "user"
    
    def test_convert_messages_with_vision(self, provider):
        """Test message conversion with images."""
        messages = [
            Message.with_image(
                "Describe this",
                "data:image/jpeg;base64,/9j/4AAQ..."
            ),
        ]
        
        system, anthropic_messages = provider._convert_messages(messages)
        
        assert anthropic_messages[0]["role"] == "user"
        assert len(anthropic_messages[0]["content"]) == 2
        assert anthropic_messages[0]["content"][0]["type"] == "text"
        assert anthropic_messages[0]["content"][1]["type"] == "image"
    
    def test_transform_request(self, provider):
        """Test request transformation."""
        request = CompletionRequest(
            model="claude-3-sonnet",
            messages=[
                Message.system("Be helpful."),
                Message.user("Hello!"),
            ],
            temperature=0.5,
            max_tokens=100,
        )
        
        data = provider.transform_request(request)
        
        assert data["model"] == "claude-3-sonnet"
        assert data["system"] == "Be helpful."
        assert len(data["messages"]) == 1
        assert data["temperature"] == 0.5
        assert data["max_tokens"] == 100
    
    def test_transform_response(self, provider):
        """Test response transformation."""
        response_data = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-sonnet",
            "content": [{"type": "text", "text": "Hello!"}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
            }
        }
        
        response = provider.transform_response(response_data, "claude-3-sonnet")
        
        assert response.id == "msg_123"
        assert response.model == "anthropic/claude-3-sonnet"
        assert response.choices[0].message["content"] == "Hello!"
        assert response.usage.prompt_tokens == 10
        assert response.usage.completion_tokens == 5
    
    def test_transform_response_with_tools(self, provider):
        """Test response transformation with tool calls."""
        response_data = {
            "id": "msg_123",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-sonnet",
            "content": [
                {"type": "text", "text": "I'll help you."},
                {
                    "type": "tool_use",
                    "id": "tool_123",
                    "name": "get_weather",
                    "input": {"location": "NYC"}
                }
            ],
            "stop_reason": "tool_use",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 20,
            }
        }
        
        response = provider.transform_response(response_data, "claude-3-sonnet")
        
        assert "tool_calls" in response.choices[0].message
        assert response.choices[0].finish_reason == "tool_calls"
