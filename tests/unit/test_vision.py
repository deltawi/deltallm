"""Tests for vision and multimodal utilities."""

import base64
import pytest
from deltallm.utils.vision import (
    encode_image_base64,
    get_mime_type_from_data_url,
    parse_data_url,
    is_data_url,
    is_image_url,
    estimate_image_tokens,
)


class TestVisionUtilities:
    """Test vision utility functions."""
    
    def test_encode_image_base64(self):
        """Test encoding image bytes to base64."""
        image_bytes = b"fake image data"
        encoded = encode_image_base64(image_bytes)
        assert encoded == base64.b64encode(image_bytes).decode("utf-8")
    
    def test_get_mime_type_from_data_url(self):
        """Test extracting MIME type from data URL."""
        url = "data:image/jpeg;base64,/9j/4AAQ..."
        mime_type = get_mime_type_from_data_url(url)
        assert mime_type == "image/jpeg"
        
        # Default for invalid URL
        invalid_url = "not a data url"
        mime_type = get_mime_type_from_data_url(invalid_url)
        assert mime_type == "image/jpeg"  # Default
    
    def test_parse_data_url(self):
        """Test parsing data URL."""
        url = "data:image/png;base64,iVBORw0KGgo..."
        mime_type, data = parse_data_url(url)
        assert mime_type == "image/png"
        assert data == "iVBORw0KGgo..."
    
    def test_parse_data_url_invalid(self):
        """Test parsing invalid data URL raises error."""
        with pytest.raises(ValueError):
            parse_data_url("not a data url")
    
    def test_is_data_url(self):
        """Test checking if URL is data URL."""
        assert is_data_url("data:image/jpeg;base64,/9j/...") is True
        assert is_data_url("https://example.com/image.jpg") is False
        assert is_data_url("http://example.com/image.png") is False
    
    def test_is_image_url(self):
        """Test checking if URL is an image URL."""
        # Data URLs
        assert is_image_url("data:image/jpeg;base64,/9j/...") is True
        assert is_image_url("data:image/png;base64,iVBORw...") is True
        
        # HTTP URLs with image extensions
        assert is_image_url("https://example.com/photo.jpg") is True
        assert is_image_url("https://example.com/photo.jpeg") is True
        assert is_image_url("https://example.com/photo.png") is True
        assert is_image_url("https://example.com/photo.webp") is True
        
        # Non-image URLs
        assert is_image_url("https://example.com/document.pdf") is False
        assert is_image_url("https://example.com/page.html") is False
    
    def test_estimate_image_tokens_low_detail(self):
        """Test token estimation for low detail images."""
        tokens = estimate_image_tokens(1024, 1024, "low")
        assert tokens == 85
    
    def test_estimate_image_tokens_high_detail(self):
        """Test token estimation for high detail images."""
        # Small image - single tile
        tokens = estimate_image_tokens(512, 512, "high")
        assert tokens == 1105 + 170  # base + 1 tile
        
        # Larger image - multiple tiles
        tokens = estimate_image_tokens(1024, 1024, "high")
        # 2x2 tiles = 4 tiles, but capped at 16
        assert tokens == 1105 + (170 * 4)
    
    def test_estimate_image_tokens_auto(self):
        """Test token estimation for auto detail."""
        tokens = estimate_image_tokens(1024, 1024, "auto")
        assert tokens == 170  # Default for auto


class TestMessageWithImage:
    """Test creating messages with images."""
    
    def test_create_message_with_image(self):
        """Test Message.with_image helper."""
        from deltallm.types import Message
        
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
        assert msg.content[1].image_url["url"] == "https://example.com/image.jpg"
    
    def test_create_message_with_image_detail(self):
        """Test Message.with_image with detail level."""
        from deltallm.types import Message
        
        msg = Message.with_image(
            text="Describe this",
            image_url="data:image/jpeg;base64,/9j/...",
            detail="high"
        )
        
        assert msg.content[1].image_url["detail"] == "high"


@pytest.mark.asyncio
class TestAsyncImageDownload:
    """Test async image download functionality."""
    
    async def test_get_image_as_base64_data_url(self):
        """Test getting image from data URL."""
        from deltallm.utils.vision import get_image_as_base64
        
        data_url = "data:image/png;base64,iVBORw0KGgo="
        base64_data, mime_type = await get_image_as_base64(data_url)
        
        assert base64_data == "iVBORw0KGgo="
        assert mime_type == "image/png"
    
    async def test_get_image_as_base64_without_download(self):
        """Test that remote URL without download raises error."""
        from deltallm.utils.vision import get_image_as_base64
        
        with pytest.raises(ValueError, match="Remote URL requires download"):
            await get_image_as_base64(
                "https://example.com/image.jpg",
                download=False
            )
