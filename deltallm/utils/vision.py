"""Vision and multimodal utilities for handling images across providers."""

import base64
import re
from typing import Optional
from urllib.parse import urlparse

import httpx


async def download_image(url: str, timeout: float = 30.0) -> tuple[bytes, str]:
    """Download an image from URL.
    
    Args:
        url: Image URL
        timeout: Request timeout
        
    Returns:
        Tuple of (image bytes, mime type)
        
    Raises:
        ValueError: If URL is invalid or download fails
    """
    # Validate URL
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    
    # Only allow http/https
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        
        content_type = response.headers.get("content-type", "image/jpeg")
        return response.content, content_type


def encode_image_base64(image_bytes: bytes) -> str:
    """Encode image bytes to base64 string.
    
    Args:
        image_bytes: Raw image bytes
        
    Returns:
        Base64 encoded string
    """
    return base64.b64encode(image_bytes).decode("utf-8")


def get_mime_type_from_data_url(url: str) -> str:
    """Extract MIME type from a data URL.
    
    Args:
        url: Data URL (e.g., "data:image/jpeg;base64,...")
        
    Returns:
        MIME type or "image/jpeg" as default
    """
    match = re.match(r"data:([^;]+);base64,", url)
    return match.group(1) if match else "image/jpeg"


def parse_data_url(url: str) -> tuple[str, str]:
    """Parse a data URL into MIME type and base64 data.
    
    Args:
        url: Data URL
        
    Returns:
        Tuple of (mime_type, base64_data)
        
    Raises:
        ValueError: If URL format is invalid
    """
    match = re.match(r"data:([^;]+);base64,(.+)", url)
    if not match:
        raise ValueError(f"Invalid data URL format")
    
    return match.group(1), match.group(2)


def is_data_url(url: str) -> bool:
    """Check if URL is a data URL.
    
    Args:
        url: URL to check
        
    Returns:
        True if data URL
    """
    return url.startswith("data:")


def is_image_url(url: str) -> bool:
    """Check if URL is an image URL.
    
    Args:
        url: URL to check
        
    Returns:
        True if URL appears to be an image
    """
    if is_data_url(url):
        mime_type = get_mime_type_from_data_url(url)
        return mime_type.startswith("image/")
    
    # Check file extension
    image_extensions = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg")
    parsed = urlparse(url)
    path = parsed.path.lower()
    return any(path.endswith(ext) for ext in image_extensions)


async def get_image_as_base64(
    url: str,
    download: bool = True,
    timeout: float = 30.0,
) -> tuple[str, str]:
    """Get image as base64 with MIME type.
    
    This function handles both data URLs and regular URLs.
    For regular URLs, it downloads the image and encodes it.
    
    Args:
        url: Image URL (data or http/https)
        download: Whether to download remote URLs
        timeout: Download timeout
        
    Returns:
        Tuple of (base64_data, mime_type)
    """
    if is_data_url(url):
        mime_type, base64_data = parse_data_url(url)
        return base64_data, mime_type
    
    if not download:
        raise ValueError(f"Remote URL requires download: {url}")
    
    image_bytes, content_type = await download_image(url, timeout)
    base64_data = encode_image_base64(image_bytes)
    return base64_data, content_type


# Image token estimation (rough estimates based on OpenAI/Anthropic docs)
IMAGE_TOKEN_ESTIMATES = {
    "low": 85,      # Low detail
    "high": 1105,   # High detail base
    "auto": 170,    # Auto detail (assume medium)
}


def estimate_image_tokens(
    width: int = 1024,
    height: int = 1024,
    detail: str = "auto",
) -> int:
    """Estimate tokens for an image.
    
    Based on OpenAI's vision pricing model:
    - Low detail: 85 tokens
    - High detail: base 85 + 170 * tiles (where tiles are 512x512 sections)
    
    Args:
        width: Image width
        height: Image height
        detail: Detail level ("low", "high", "auto")
        
    Returns:
        Estimated token count
    """
    if detail == "low":
        return IMAGE_TOKEN_ESTIMATES["low"]
    
    if detail == "auto":
        return IMAGE_TOKEN_ESTIMATES["auto"]
    
    # For high detail, calculate tiles
    # Each tile is 512x512, rounded up
    tiles_x = (width + 511) // 512
    tiles_y = (height + 511) // 512
    total_tiles = tiles_x * tiles_y
    
    # Base + per-tile cost (capped at reasonable limit)
    tokens = IMAGE_TOKEN_ESTIMATES["high"] + (170 * min(total_tiles, 16))
    return tokens
