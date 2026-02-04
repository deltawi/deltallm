"""Tests for token counter utilities."""

import pytest
from deltallm.utils.token_counter import TokenCounter, count_tokens, count_message_tokens


class TestTokenCounter:
    """Test token counter functionality."""

    def test_count_tokens_simple_text(self):
        """Test counting tokens in simple text."""
        counter = TokenCounter()
        text = "Hello, world!"
        count = counter.count_tokens(text, model="gpt-4")
        
        # Should return a positive integer
        assert isinstance(count, int)
        assert count > 0
        # "Hello, world!" is about 4 tokens
        assert 3 <= count <= 10

    def test_count_tokens_empty_string(self):
        """Test counting tokens in empty string."""
        counter = TokenCounter()
        count = counter.count_tokens("", model="gpt-4")
        assert count >= 0

    def test_count_tokens_long_text(self):
        """Test counting tokens in longer text."""
        counter = TokenCounter()
        text = "This is a longer piece of text that should have more tokens. " * 10
        count = counter.count_tokens(text, model="gpt-4")
        
        # Should be significantly more than short text
        short_count = counter.count_tokens("Hello", model="gpt-4")
        assert count > short_count

    def test_count_tokens_different_models(self):
        """Test token counting for different models."""
        counter = TokenCounter()
        text = "Hello, world!"
        
        models = ["gpt-4", "gpt-3.5-turbo", "gpt-4o", "claude-3-opus"]
        counts = {}
        
        for model in models:
            counts[model] = counter.count_tokens(text, model=model)
            assert counts[model] > 0

    def test_count_message_tokens_simple(self):
        """Test counting tokens in simple messages."""
        counter = TokenCounter()
        messages = [
            {"role": "user", "content": "Hello!"}
        ]
        count = counter.count_message_tokens(messages, model="gpt-4")
        
        # Should include overhead for message formatting
        assert count > 4  # Base tokens per message

    def test_count_message_tokens_conversation(self):
        """Test counting tokens in a conversation."""
        counter = TokenCounter()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        count = counter.count_message_tokens(messages, model="gpt-4")
        
        # Should be sum of all messages plus overhead
        assert count > 12  # 3 messages * 4 base tokens each

    def test_count_message_tokens_with_name(self):
        """Test counting tokens with name field."""
        counter = TokenCounter()
        messages = [
            {"role": "user", "content": "Hello!", "name": "Alice"}
        ]
        count = counter.count_message_tokens(messages, model="gpt-4")
        
        # Should handle name field correctly
        assert count > 0

    def test_count_message_tokens_with_content_blocks(self):
        """Test counting tokens with content blocks (vision)."""
        counter = TokenCounter()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's in this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/image.jpg"}}
                ]
            }
        ]
        count = counter.count_message_tokens(messages, model="gpt-4")
        
        # Should handle content blocks
        assert count > 0

    def test_count_message_tokens_empty_list(self):
        """Test counting tokens with empty message list."""
        counter = TokenCounter()
        count = counter.count_message_tokens([], model="gpt-4")
        
        # Just the reply priming tokens
        assert count == 2

    def test_estimate_image_tokens_low_detail(self):
        """Test image token estimation for low detail."""
        counter = TokenCounter()
        tokens = counter.estimate_image_tokens(
            width=1024, height=1024,
            model="gpt-4o",
            detail="low"
        )
        assert tokens == 85  # Fixed cost for low detail

    def test_estimate_image_tokens_high_detail_gpt4o(self):
        """Test image token estimation for high detail GPT-4o."""
        counter = TokenCounter()
        tokens = counter.estimate_image_tokens(
            width=1024, height=1024,
            model="gpt-4o",
            detail="high"
        )
        # Base 85 + tiles
        assert tokens > 85
        
        # 1024x1024 should be 4 tiles (2x2 of 512x512)
        # 85 + 4 * 170 = 765
        assert tokens == 765

    def test_estimate_image_tokens_large_image(self):
        """Test image token estimation for very large image."""
        counter = TokenCounter()
        tokens = counter.estimate_image_tokens(
            width=4000, height=3000,
            model="gpt-4o",
            detail="high"
        )
        # Should scale down to 2048x2048 max
        assert tokens > 85

    def test_estimate_image_tokens_claude(self):
        """Test image token estimation for Claude."""
        counter = TokenCounter()
        tokens = counter.estimate_image_tokens(
            width=1024, height=1024,
            model="claude-3-opus",
            detail="high"
        )
        # Claude uses rough estimate
        assert tokens == 1000

    def test_estimate_image_tokens_auto_detail(self):
        """Test image token estimation with auto detail."""
        counter = TokenCounter()
        tokens = counter.estimate_image_tokens(
            width=1024, height=1024,
            model="gpt-4o",
            detail="auto"
        )
        # Auto should use high detail logic
        assert tokens > 85

    def test_encoder_caching(self):
        """Test that encoders are cached."""
        counter = TokenCounter()
        
        # First call should create encoder
        count1 = counter.count_tokens("Hello", model="gpt-4")
        
        # Second call should use cached encoder
        count2 = counter.count_tokens("World", model="gpt-4")
        
        assert count1 > 0
        assert count2 > 0
        assert "gpt-4" in counter._encoders

    def test_encoder_unknown_model(self):
        """Test handling of unknown model."""
        counter = TokenCounter()
        text = "Hello, world!"
        
        # Should fallback to character-based estimation
        count = counter.count_tokens(text, model="unknown-model-v1")
        assert count > 0

    def test_convenience_functions(self):
        """Test convenience functions."""
        text = "Hello, world!"
        messages = [{"role": "user", "content": "Hello!"}]
        
        # Test count_tokens function
        count1 = count_tokens(text, model="gpt-4")
        assert count1 > 0
        
        # Test count_message_tokens function
        count2 = count_message_tokens(messages, model="gpt-4")
        assert count2 > 0

    def test_message_with_none_values(self):
        """Test handling messages with None values."""
        counter = TokenCounter()
        messages = [
            {"role": "user", "content": "Hello!", "name": None, "tool_calls": None}
        ]
        count = counter.count_message_tokens(messages, model="gpt-4")
        assert count > 0

    def test_message_with_complex_content(self):
        """Test handling messages with complex nested content."""
        counter = TokenCounter()
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'}
                    }
                ]
            }
        ]
        count = counter.count_message_tokens(messages, model="gpt-4")
        assert count > 0

    def test_different_message_roles(self):
        """Test counting tokens for different message roles."""
        counter = TokenCounter()
        
        roles = ["system", "user", "assistant", "tool"]
        for role in roles:
            messages = [{"role": role, "content": "Test message"}]
            count = counter.count_message_tokens(messages, model="gpt-4")
            assert count > 0, f"Failed for role: {role}"
