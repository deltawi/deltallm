"""Tests for provider streaming functionality."""

import pytest
import httpx
import respx
from unittest.mock import AsyncMock, patch

from deltallm.providers.openai import OpenAIProvider
from deltallm.providers.anthropic import AnthropicProvider
from deltallm.types import CompletionRequest, Message
from deltallm.exceptions import RateLimitError, AuthenticationError, APITimeoutError, ServiceUnavailableError, ContextLengthExceededError, ContentPolicyViolationError


class TestOpenAIStreaming:
    """Test OpenAI streaming functionality."""

    @pytest.fixture
    def provider(self):
        return OpenAIProvider()

    @pytest.fixture
    def base_request(self):
        return CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
            stream=True,
        )

    @respx.mock
    async def test_streaming_success(self, provider, base_request):
        """Test successful streaming response."""
        # Mock SSE stream
        sse_data = (
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n\n'
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":" world"},"finish_reason":null}]}\n\n'
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: [DONE]\n\n'
        )
        
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse_data, headers={"content-type": "text/event-stream"})
        )
        
        chunks = []
        async for chunk in provider.chat_completion_stream(base_request, "sk-test"):
            chunks.append(chunk)
        
        assert len(chunks) == 4
        assert chunks[0].choices[0].delta.get("role") == "assistant"
        assert chunks[1].choices[0].delta.get("content") == "Hello"
        assert chunks[2].choices[0].delta.get("content") == " world"
        assert chunks[3].choices[0].finish_reason == "stop"

    @respx.mock
    async def test_streaming_with_usage(self, provider, base_request):
        """Test streaming with usage in final chunk."""
        sse_data = (
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
            b'data: [DONE]\n\n'
        )
        
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse_data, headers={"content-type": "text/event-stream"})
        )
        
        chunks = []
        async for chunk in provider.chat_completion_stream(base_request, "sk-test"):
            chunks.append(chunk)
        
        # Last chunk should have usage
        assert chunks[-1].usage is not None
        assert chunks[-1].usage.prompt_tokens == 10
        assert chunks[-1].usage.completion_tokens == 5

    @respx.mock
    async def test_streaming_rate_limit_error(self, provider, base_request):
        """Test streaming with rate limit error."""
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                429,
                json={"error": {"message": "Rate limit exceeded", "type": "rate_limit_exceeded"}},
                headers={"retry-after": "60"}
            )
        )
        
        with pytest.raises(RateLimitError) as exc_info:
            async for _ in provider.chat_completion_stream(base_request, "sk-test"):
                pass
        
        assert exc_info.value.retry_after == 60

    @respx.mock
    async def test_streaming_auth_error(self, provider, base_request):
        """Test streaming with authentication error."""
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                401,
                json={"error": {"message": "Invalid API key", "type": "authentication_error"}}
            )
        )
        
        with pytest.raises(AuthenticationError):
            async for _ in provider.chat_completion_stream(base_request, "sk-test"):
                pass

    @respx.mock
    async def test_streaming_empty_lines(self, provider, base_request):
        """Test streaming handles empty lines correctly."""
        sse_data = (
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
            b'\n'  # Empty line
            b': heartbeat\n\n'  # Comment line
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: [DONE]\n\n'
        )
        
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse_data, headers={"content-type": "text/event-stream"})
        )
        
        chunks = []
        async for chunk in provider.chat_completion_stream(base_request, "sk-test"):
            chunks.append(chunk)
        
        assert len(chunks) == 2

    @respx.mock
    async def test_streaming_tool_calls(self, provider):
        """Test streaming with tool calls."""
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="What's the weather?")],
            stream=True,
            tools=[{
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}}
                }
            }]
        )
        
        sse_data = (
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_123","type":"function","function":{"name":"get_weather","arguments":""}}]},"finish_reason":null}]}\n\n'
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"city\\": \\"NYC\\"}"}}]},"finish_reason":null}]}\n\n'
            b'data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            b'data: [DONE]\n\n'
        )
        
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=sse_data, headers={"content-type": "text/event-stream"})
        )
        
        chunks = []
        async for chunk in provider.chat_completion_stream(request, "sk-test"):
            chunks.append(chunk)
        
        assert len(chunks) == 3
        assert chunks[-1].choices[0].finish_reason == "tool_calls"


class TestAnthropicStreaming:
    """Test Anthropic streaming functionality."""

    @pytest.fixture
    def provider(self):
        return AnthropicProvider()

    @pytest.fixture
    def base_request(self):
        return CompletionRequest(
            model="claude-3-sonnet",
            messages=[Message(role="user", content="Hello")],
            stream=True,
        )

    @respx.mock
    async def test_streaming_success(self, provider, base_request):
        """Test successful streaming response."""
        sse_data = (
            b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_123","type":"message","role":"assistant","content":[],"model":"claude-3-sonnet","stop_reason":null}}\n\n'
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n'
            b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
            b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":10}}\n\n'
            b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        )
        
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, content=sse_data, headers={"content-type": "text/event-stream"})
        )
        
        chunks = []
        async for chunk in provider.chat_completion_stream(base_request, "sk-test"):
            chunks.append(chunk)
        
        # Should have role chunk + content chunks + stop chunk
        assert len(chunks) >= 3
        
        # First chunk should have role
        assert chunks[0].choices[0].delta.get("role") == "assistant"
        
        # Content should be accumulated
        content_parts = [c.choices[0].delta.get("content", "") for c in chunks[1:-1]]
        assert "".join(content_parts) == "Hello world"

    @respx.mock
    async def test_streaming_rate_limit_error(self, provider, base_request):
        """Test streaming with rate limit error."""
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                429,
                json={"type": "error", "error": {"type": "rate_limit_error", "message": "Rate limit exceeded"}},
                headers={"retry-after": "60"}
            )
        )
        
        with pytest.raises(RateLimitError) as exc_info:
            async for _ in provider.chat_completion_stream(base_request, "sk-test"):
                pass
        
        assert exc_info.value.retry_after == 60

    @respx.mock
    async def test_streaming_auth_error(self, provider, base_request):
        """Test streaming with authentication error."""
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                401,
                json={"type": "error", "error": {"type": "authentication_error", "message": "Invalid API key"}}
            )
        )
        
        with pytest.raises(AuthenticationError):
            async for _ in provider.chat_completion_stream(base_request, "sk-test"):
                pass


class TestProviderErrorHandling:
    """Test provider error handling."""

    @respx.mock
    async def test_openai_timeout_error(self):
        """Test OpenAI timeout handling."""
        provider = OpenAIProvider()
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
        )
        
        # Simulate timeout
        route = respx.post("https://api.openai.com/v1/chat/completions").side_effect = httpx.TimeoutException("Request timed out")
        
        with pytest.raises(APITimeoutError):
            await provider.chat_completion(request, "sk-test")

    @respx.mock
    async def test_openai_service_unavailable(self):
        """Test OpenAI 503 error handling."""
        provider = OpenAIProvider()
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
        )
        
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(503, json={"error": {"message": "Service unavailable"}})
        )
        
        
        with pytest.raises(ServiceUnavailableError):
            await provider.chat_completion(request, "sk-test")

    @respx.mock
    async def test_openai_context_length_error(self):
        """Test OpenAI context length exceeded error."""
        provider = OpenAIProvider()
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
        )
        
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "message": "This model's maximum context length is 8192 tokens",
                        "type": "invalid_request_error",
                        "code": "context_length_exceeded"
                    }
                }
            )
        )
        
        
        with pytest.raises(ContextLengthExceededError):
            await provider.chat_completion(request, "sk-test")

    @respx.mock
    async def test_openai_content_policy_error(self):
        """Test OpenAI content policy error."""
        provider = OpenAIProvider()
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
        )
        
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Content violates usage policies",
                        "type": "content_policy_violation"
                    }
                }
            )
        )
        
        
        with pytest.raises(ContentPolicyViolationError):
            await provider.chat_completion(request, "sk-test")
