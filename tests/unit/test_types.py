"""Tests for type definitions."""

import pytest
from pydantic import ValidationError

from deltallm.types import (
    Message,
    CompletionRequest,
    ContentBlock,
    ResponseFormat,
)


class TestMessage:
    """Tests for Message type."""
    
    def test_create_user_message(self):
        """Test creating a user message."""
        msg = Message.user("Hello!")
        assert msg.role == "user"
        assert msg.content == "Hello!"
    
    def test_create_system_message(self):
        """Test creating a system message."""
        msg = Message.system("You are a helpful assistant.")
        assert msg.role == "system"
        assert msg.content == "You are a helpful assistant."
    
    def test_create_assistant_message(self):
        """Test creating an assistant message."""
        msg = Message.assistant("How can I help you?")
        assert msg.role == "assistant"
        assert msg.content == "How can I help you?"
    
    def test_create_tool_message(self):
        """Test creating a tool message."""
        msg = Message.tool("Result", tool_call_id="call_123")
        assert msg.role == "tool"
        assert msg.content == "Result"
        assert msg.tool_call_id == "call_123"
    
    def test_tool_message_requires_tool_call_id(self):
        """Test that tool messages require tool_call_id."""
        with pytest.raises(ValidationError):
            Message(role="tool", content="Result")
    
    def test_with_image(self):
        """Test creating a message with an image."""
        msg = Message.with_image(
            text="What's in this image?",
            image_url="https://example.com/image.jpg"
        )
        assert msg.role == "user"
        assert isinstance(msg.content, list)
        assert len(msg.content) == 2
        assert msg.content[0].type == "text"
        assert msg.content[0].text == "What's in this image?"
        assert msg.content[1].type == "image_url"


class TestCompletionRequest:
    """Tests for CompletionRequest."""
    
    def test_basic_request(self):
        """Test creating a basic completion request."""
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message.user("Hello!")]
        )
        assert request.model == "gpt-4o"
        assert len(request.messages) == 1
        assert request.temperature == 1.0
        assert request.stream is False
    
    def test_request_with_options(self):
        """Test creating a request with options."""
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message.user("Hello!")],
            temperature=0.5,
            max_tokens=100,
            stream=True,
        )
        assert request.temperature == 0.5
        assert request.max_tokens == 100
        assert request.stream is True
    
    def test_temperature_validation(self):
        """Test temperature validation."""
        with pytest.raises(ValidationError):
            CompletionRequest(
                model="gpt-4o",
                messages=[Message.user("Hello!")],
                temperature=3.0,  # Invalid: > 2
            )
        
        with pytest.raises(ValidationError):
            CompletionRequest(
                model="gpt-4o",
                messages=[Message.user("Hello!")],
                temperature=-0.5,  # Invalid: < 0
            )
    
    def test_mutually_exclusive_max_tokens(self):
        """Test that max_tokens and max_completion_tokens are mutually exclusive."""
        with pytest.raises(ValidationError):
            CompletionRequest(
                model="gpt-4o",
                messages=[Message.user("Hello!")],
                max_tokens=100,
                max_completion_tokens=100,
            )


class TestContentBlock:
    """Tests for ContentBlock."""
    
    def test_text_block(self):
        """Test creating a text content block."""
        block = ContentBlock(type="text", text="Hello")
        assert block.type == "text"
        assert block.text == "Hello"
    
    def test_text_block_requires_text(self):
        """Test that text blocks require text."""
        with pytest.raises(ValidationError):
            ContentBlock(type="text")
    
    def test_image_block(self):
        """Test creating an image content block."""
        block = ContentBlock(
            type="image_url",
            image_url={"url": "https://example.com/image.jpg"}
        )
        assert block.type == "image_url"
        assert block.image_url["url"] == "https://example.com/image.jpg"


class TestResponseFormat:
    """Tests for ResponseFormat."""
    
    def test_text_format(self):
        """Test text response format."""
        fmt = ResponseFormat(type="text")
        assert fmt.type == "text"
    
    def test_json_object_format(self):
        """Test JSON object response format."""
        fmt = ResponseFormat(type="json_object")
        assert fmt.type == "json_object"
    
    def test_json_schema_requires_schema(self):
        """Test that json_schema requires a schema."""
        with pytest.raises(ValidationError):
            ResponseFormat(type="json_schema")
        
        fmt = ResponseFormat(
            type="json_schema",
            json_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                }
            }
        )
        assert fmt.type == "json_schema"
        assert "properties" in fmt.json_schema
