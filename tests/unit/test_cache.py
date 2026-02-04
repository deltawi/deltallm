"""Tests for caching system."""

import pytest
import asyncio
from datetime import datetime, timedelta

from deltallm.cache import InMemoryCache, CacheConfig, CacheEntry
from deltallm.cache.base import CacheManager
from deltallm.types import CompletionRequest, CompletionResponse, Message, Usage


class TestCacheConfig:
    """Test cache configuration."""

    def test_default_config(self):
        """Test default cache configuration."""
        config = CacheConfig()
        assert config.ttl == 3600
        assert config.max_size is None
        assert config.similarity_threshold == 0.95
        assert config.enabled is True
        assert config.cache_streaming is False
        assert config.excluded_models == []

    def test_custom_config(self):
        """Test custom cache configuration."""
        config = CacheConfig(
            ttl=7200,
            max_size=1000,
            enabled=False,
            excluded_models=["gpt-4", "claude-3"],
        )
        assert config.ttl == 7200
        assert config.max_size == 1000
        assert config.enabled is False
        assert config.excluded_models == ["gpt-4", "claude-3"]


class TestCacheEntry:
    """Test cache entry."""

    @pytest.fixture
    def sample_response(self):
        """Create a sample response."""
        return CompletionResponse(
            id="test-123",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o",
            choices=[{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    def test_entry_creation(self, sample_response):
        """Test cache entry creation."""
        now = datetime.utcnow()
        expires = now + timedelta(hours=1)
        
        entry = CacheEntry(
            key="test-key",
            response=sample_response,
            created_at=now,
            expires_at=expires,
            model="gpt-4o",
            tokens=15,
        )
        
        assert entry.key == "test-key"
        assert entry.model == "gpt-4o"
        assert entry.tokens == 15
        assert not entry.is_expired()

    def test_entry_expired(self, sample_response):
        """Test expired entry detection."""
        now = datetime.utcnow()
        expires = now - timedelta(hours=1)  # Expired 1 hour ago
        
        entry = CacheEntry(
            key="test-key",
            response=sample_response,
            created_at=now - timedelta(hours=2),
            expires_at=expires,
            model="gpt-4o",
        )
        
        assert entry.is_expired()

    def test_entry_serialization(self, sample_response):
        """Test entry serialization."""
        now = datetime.utcnow()
        expires = now + timedelta(hours=1)
        
        entry = CacheEntry(
            key="test-key",
            response=sample_response,
            created_at=now,
            expires_at=expires,
            model="gpt-4o",
            tokens=15,
        )
        
        data = entry.to_dict()
        assert data["key"] == "test-key"
        assert data["model"] == "gpt-4o"
        assert data["tokens"] == 15
        assert "response" in data
        assert "created_at" in data
        assert "expires_at" in data


class TestInMemoryCache:
    """Test in-memory cache implementation."""

    @pytest.fixture
    def cache(self):
        """Create a cache instance."""
        return InMemoryCache()

    @pytest.fixture
    def sample_request(self):
        """Create a sample request."""
        return CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
        )

    @pytest.fixture
    def sample_response(self):
        """Create a sample response."""
        return CompletionResponse(
            id="test-123",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o",
            choices=[{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    async def test_set_and_get(self, cache, sample_response):
        """Test setting and getting cache entry."""
        await cache.set("key1", sample_response, "gpt-4o", tokens=15)
        
        entry = await cache.get("key1")
        assert entry is not None
        assert entry.model == "gpt-4o"
        assert entry.tokens == 15
        assert entry.response.id == "test-123"

    async def test_get_miss(self, cache):
        """Test cache miss."""
        entry = await cache.get("nonexistent")
        assert entry is None

    async def test_delete(self, cache, sample_response):
        """Test deleting cache entry."""
        await cache.set("key1", sample_response, "gpt-4o")
        
        # Delete should return True
        assert await cache.delete("key1") is True
        
        # Entry should be gone
        entry = await cache.get("key1")
        assert entry is None
        
        # Delete non-existent should return False
        assert await cache.delete("nonexistent") is False

    async def test_clear(self, cache, sample_response):
        """Test clearing cache."""
        await cache.set("key1", sample_response, "gpt-4o")
        await cache.set("key2", sample_response, "gpt-4o")
        
        await cache.clear()
        
        assert await cache.get("key1") is None
        assert await cache.get("key2") is None

    async def test_cache_expiration(self):
        """Test cache entry expiration."""
        # Create cache with very short TTL
        cache = InMemoryCache(CacheConfig(ttl=0.001))  # 1ms
        
        response = CompletionResponse(
            id="test-123",
            object="chat.completion",
            created=1700000000,
            model="gpt-4o",
            choices=[],
            usage=None,
        )
        
        await cache.set("key1", response, "gpt-4o")
        
        # Wait for expiration
        import asyncio
        await asyncio.sleep(0.01)
        
        # Entry should be expired
        entry = await cache.get("key1")
        assert entry is None

    async def test_max_size_eviction(self, sample_response):
        """Test LRU eviction when max size reached."""
        cache = InMemoryCache(CacheConfig(max_size=3))
        
        # Add 3 entries
        await cache.set("key1", sample_response, "gpt-4o")
        await cache.set("key2", sample_response, "gpt-4o")
        await cache.set("key3", sample_response, "gpt-4o")
        
        # Add 4th entry - should evict key1
        await cache.set("key4", sample_response, "gpt-4o")
        
        assert await cache.get("key1") is None  # Evicted
        assert await cache.get("key2") is not None
        assert await cache.get("key3") is not None
        assert await cache.get("key4") is not None

    async def test_lru_ordering(self, cache, sample_response):
        """Test LRU ordering is maintained."""
        cache.config.max_size = 3
        
        # Add 3 entries
        await cache.set("key1", sample_response, "gpt-4o")
        await cache.set("key2", sample_response, "gpt-4o")
        await cache.set("key3", sample_response, "gpt-4o")
        
        # Access key1 to make it recently used
        await cache.get("key1")
        
        # Add 4th entry - should evict key2 (least recently used)
        await cache.set("key4", sample_response, "gpt-4o")
        
        assert await cache.get("key1") is not None  # Recently used
        assert await cache.get("key2") is None  # Evicted
        assert await cache.get("key3") is not None
        assert await cache.get("key4") is not None

    async def test_get_response_and_cache(self, cache, sample_request, sample_response):
        """Test high-level get_response and cache_response methods."""
        # Cache miss initially
        cached = await cache.get_response(sample_request)
        assert cached is None
        
        # Cache the response
        await cache.cache_response(sample_request, sample_response)
        
        # Cache hit now
        cached = await cache.get_response(sample_request)
        assert cached is not None
        assert cached.id == sample_response.id

    async def test_disabled_cache(self, sample_request, sample_response):
        """Test disabled cache doesn't store anything."""
        cache = InMemoryCache(CacheConfig(enabled=False))
        
        await cache.cache_response(sample_request, sample_response)
        
        cached = await cache.get_response(sample_request)
        assert cached is None

    async def test_excluded_models(self, sample_response):
        """Test excluded models are not cached."""
        cache = InMemoryCache(CacheConfig(excluded_models=["gpt-4o"]))
        
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
        )
        
        await cache.cache_response(request, sample_response)
        
        cached = await cache.get_response(request)
        assert cached is None

    async def test_streaming_not_cached_by_default(self, sample_response):
        """Test streaming requests are not cached by default."""
        cache = InMemoryCache()
        
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
            stream=True,
        )
        
        await cache.cache_response(request, sample_response)
        
        cached = await cache.get_response(request)
        assert cached is None

    async def test_streaming_cached_when_enabled(self, sample_response):
        """Test streaming requests are cached when enabled."""
        cache = InMemoryCache(CacheConfig(cache_streaming=True))
        
        request = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
            stream=True,
        )
        
        await cache.cache_response(request, sample_response)
        
        cached = await cache.get_response(request)
        assert cached is not None

    async def test_stats(self, cache, sample_request, sample_response):
        """Test cache statistics."""
        # Initial stats
        stats = await cache.get_stats()
        assert stats["backend"] == "memory"
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 0
        
        # Cache miss
        await cache.get_response(sample_request)
        
        stats = await cache.get_stats()
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0
        
        # Cache the response
        await cache.cache_response(sample_request, sample_response)
        
        # Cache hit
        await cache.get_response(sample_request)
        
        stats = await cache.get_stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    async def test_same_request_same_key(self, cache, sample_request, sample_response):
        """Test same request generates same cache key."""
        # Create identical request
        request2 = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
        )
        
        # Cache with first request
        await cache.cache_response(sample_request, sample_response)
        
        # Should get cache hit with second request
        cached = await cache.get_response(request2)
        assert cached is not None

    async def test_different_request_different_key(self, cache, sample_response):
        """Test different requests generate different cache keys."""
        request1 = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hello")],
        )
        
        request2 = CompletionRequest(
            model="gpt-4o",
            messages=[Message(role="user", content="Hi there")],  # Different content
        )
        
        # Cache with first request
        await cache.cache_response(request1, sample_response)
        
        # Should get cache miss with different request
        cached = await cache.get_response(request2)
        assert cached is None
