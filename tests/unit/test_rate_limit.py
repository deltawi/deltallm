"""Tests for rate limiting functionality."""

import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from deltallm.proxy.rate_limit import RateLimiter, RateLimitInfo, RateLimitMiddleware


class TestRateLimiter:
    """Test rate limiter functionality."""

    @pytest.fixture
    def memory_limiter(self):
        """Create a rate limiter with in-memory backend."""
        return RateLimiter()

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis = AsyncMock()
        # Mock pipeline
        pipe = AsyncMock()
        pipe.execute = AsyncMock(return_value=[0, 0, 1, True])  # zrem, zcard, zadd, expire
        redis.pipeline = MagicMock(return_value=pipe)
        redis.zremrangebyscore = AsyncMock(return_value=0)
        redis.zcard = AsyncMock(return_value=0)
        redis.zadd = AsyncMock(return_value=1)
        redis.expire = AsyncMock(return_value=True)
        return redis

    @pytest.fixture
    def redis_limiter(self, mock_redis):
        """Create a rate limiter with mock Redis backend."""
        return RateLimiter(redis_client=mock_redis)

    class TestMemoryBackend:
        """Test in-memory rate limiting."""

        async def test_check_rate_limit_allowed(self, memory_limiter):
            """Test that requests are allowed within limit."""
            info = await memory_limiter.check_rate_limit("key:123", limit=10)
            
            assert isinstance(info, RateLimitInfo)
            assert info.limit == 10
            assert info.remaining == 9
            assert info.reset > time.time()

        async def test_check_rate_limit_exceeded(self, memory_limiter):
            """Test that requests are blocked when limit exceeded."""
            # Make 10 requests
            for i in range(10):
                await memory_limiter.check_rate_limit("key:456", limit=10)
            
            # 11th request should be blocked
            with pytest.raises(HTTPException) as exc_info:
                await memory_limiter.check_rate_limit("key:456", limit=10)
            
            assert exc_info.value.status_code == 429
            assert "X-RateLimit-Limit" in exc_info.value.headers
            assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"

        async def test_check_rate_limit_cost(self, memory_limiter):
            """Test rate limiting with cost parameter."""
            # Request with cost of 5
            info = await memory_limiter.check_rate_limit("key:789", limit=10, cost=5)
            assert info.remaining == 5
            
            # Another request with cost of 5 should be allowed
            info = await memory_limiter.check_rate_limit("key:789", limit=10, cost=5)
            assert info.remaining == 0
            
            # Third request should be blocked
            with pytest.raises(HTTPException) as exc_info:
                await memory_limiter.check_rate_limit("key:789", limit=10, cost=1)
            assert exc_info.value.status_code == 429

        async def test_window_expiration(self, memory_limiter):
            """Test that old entries are expired from the window."""
            # Use a very short window for testing
            limiter = RateLimiter(window_size=0.01)  # 10ms window
            
            # Add an entry
            await limiter.check_rate_limit("key:expire", limit=10)
            
            # Wait for window to expire
            time.sleep(0.02)
            
            # Should be allowed again
            info = await limiter.check_rate_limit("key:expire", limit=10)
            assert info.remaining == 9

        async def test_different_keys_independent(self, memory_limiter):
            """Test that different keys have independent limits."""
            # Max out key A
            for i in range(10):
                await memory_limiter.check_rate_limit("key:A", limit=10)
            
            with pytest.raises(HTTPException):
                await memory_limiter.check_rate_limit("key:A", limit=10)
            
            # Key B should still be allowed
            info = await memory_limiter.check_rate_limit("key:B", limit=10)
            assert info.remaining == 9

        async def test_different_limit_types(self, memory_limiter):
            """Test that different limit types are independent."""
            # Max out requests
            for i in range(5):
                await memory_limiter.check_rate_limit("key:type", limit=5, limit_type="requests")
            
            with pytest.raises(HTTPException):
                await memory_limiter.check_rate_limit("key:type", limit=5, limit_type="requests")
            
            # Tokens should still be allowed
            info = await memory_limiter.check_rate_limit("key:type", limit=5, limit_type="tokens")
            assert info.remaining == 4

    class TestRedisBackend:
        """Test Redis-backed rate limiting."""

        async def test_redis_check_allowed(self, redis_limiter, mock_redis):
            """Test Redis-backed rate limiting allows requests."""
            # Setup mock for zcard to return 5 (current count)
            pipe = mock_redis.pipeline.return_value
            pipe.execute = AsyncMock(return_value=[0, 5, 1, True])
            
            info = await redis_limiter.check_rate_limit("key:redis", limit=10)
            
            assert isinstance(info, RateLimitInfo)
            assert info.limit == 10
            assert info.remaining == 4
            
            # Verify Redis calls were made via pipeline
            pipe.zremrangebyscore.assert_called_once()
            pipe.zcard.assert_called_once()

        async def test_redis_check_exceeded(self, redis_limiter, mock_redis):
            """Test Redis-backed rate limiting blocks exceeded requests."""
            # Setup mock for zcard to return 10 (at limit)
            pipe = mock_redis.pipeline.return_value
            pipe.execute = AsyncMock(return_value=[0, 10, 1, True])
            
            with pytest.raises(HTTPException) as exc_info:
                await redis_limiter.check_rate_limit("key:redis", limit=10)
            
            assert exc_info.value.status_code == 429
            assert exc_info.value.headers["X-RateLimit-Remaining"] == "0"

        async def test_redis_check_with_cost(self, redis_limiter, mock_redis):
            """Test Redis-backed rate limiting with cost."""
            # Setup mock for zcard to return 3
            pipe = mock_redis.pipeline.return_value
            pipe.execute = AsyncMock(return_value=[0, 3, 1, True])
            
            info = await redis_limiter.check_rate_limit("key:redis", limit=10, cost=5)
            
            assert info.remaining == 2  # 10 - 3 - 5 = 2

    class TestRateLimitHeaders:
        """Test rate limit header generation."""

        async def test_headers_allowed(self, memory_limiter):
            """Test headers for allowed request."""
            info = await memory_limiter.check_rate_limit("key:hdr", limit=100)
            
            headers = memory_limiter.get_headers(info)
            assert headers["X-RateLimit-Limit"] == "100"
            assert headers["X-RateLimit-Remaining"] == "99"
            assert "X-RateLimit-Reset" in headers

        async def test_headers_exceeded(self, memory_limiter):
            """Test headers for exceeded request."""
            # Max out the limit
            for i in range(10):
                await memory_limiter.check_rate_limit("key:hdr2", limit=10)
            
            with pytest.raises(HTTPException) as exc_info:
                await memory_limiter.check_rate_limit("key:hdr2", limit=10)
            
            headers = exc_info.value.headers
            assert headers["X-RateLimit-Limit"] == "10"
            assert headers["X-RateLimit-Remaining"] == "0"

    class TestGetHeaders:
        """Test get_headers method."""

        def test_get_headers(self, memory_limiter):
            """Test get_headers returns correct headers."""
            info = RateLimitInfo(limit=100, remaining=50, reset=1700000000, window=60)
            headers = memory_limiter.get_headers(info)
            
            assert headers["X-RateLimit-Limit"] == "100"
            assert headers["X-RateLimit-Remaining"] == "50"
            assert headers["X-RateLimit-Reset"] == "1700000000"

        def test_get_headers_zero_remaining(self, memory_limiter):
            """Test get_headers with zero remaining."""
            info = RateLimitInfo(limit=100, remaining=0, reset=1700000000, window=60)
            headers = memory_limiter.get_headers(info)
            
            assert headers["X-RateLimit-Remaining"] == "0"

        def test_get_headers_negative_remaining(self, memory_limiter):
            """Test get_headers clamps negative remaining to zero."""
            info = RateLimitInfo(limit=100, remaining=-5, reset=1700000000, window=60)
            headers = memory_limiter.get_headers(info)
            
            assert headers["X-RateLimit-Remaining"] == "0"


class TestRateLimitInfo:
    """Test RateLimitInfo model."""

    def test_creation(self):
        """Test creating RateLimitInfo."""
        info = RateLimitInfo(limit=100, remaining=50, reset=1700000000)
        
        assert info.limit == 100
        assert info.remaining == 50
        assert info.reset == 1700000000
        assert info.window == 60  # default

    def test_creation_with_window(self):
        """Test creating RateLimitInfo with custom window."""
        info = RateLimitInfo(limit=100, remaining=50, reset=1700000000, window=120)
        
        assert info.window == 120


class TestRateLimitMiddleware:
    """Test RateLimitMiddleware."""

    @pytest.fixture
    def middleware(self, memory_limiter):
        """Create rate limit middleware."""
        return RateLimitMiddleware(memory_limiter, default_rpm=60, default_tpm=10000)

    @pytest.fixture
    def mock_request(self):
        """Create a mock request."""
        request = MagicMock()
        return request

    async def test_check_with_defaults(self, middleware, mock_request):
        """Test check with default limits."""
        info = await middleware.check(mock_request, key_hash="key:test")
        
        assert isinstance(info, RateLimitInfo)
        assert info.limit == 60  # default_rpm

    async def test_check_with_custom_limits(self, middleware, mock_request):
        """Test check with custom limits."""
        info = await middleware.check(
            mock_request,
            key_hash="key:test",
            rpm=100,
            tpm=50000
        )
        
        assert info.limit == 100

    async def test_check_exceeds_request_limit(self, middleware, mock_request):
        """Test check when request limit exceeded."""
        # Make 60 requests (default limit)
        for i in range(60):
            await middleware.check(mock_request, key_hash="key:limited")
        
        # Next request should fail
        with pytest.raises(HTTPException) as exc_info:
            await middleware.check(mock_request, key_hash="key:limited")
        
        assert exc_info.value.status_code == 429

    async def test_check_with_token_limit(self, middleware, mock_request):
        """Test check respects token limit."""
        # Use a small token limit for testing
        middleware.default_tpm = 1000
        
        # First request with 500 tokens
        await middleware.check(
            mock_request,
            key_hash="key:tokens",
            tpm=1000,
            estimated_tokens=500
        )
        
        # Second request with 500 tokens
        await middleware.check(
            mock_request,
            key_hash="key:tokens",
            tpm=1000,
            estimated_tokens=500
        )
        
        # Third request should fail (would exceed 1000)
        with pytest.raises(HTTPException) as exc_info:
            await middleware.check(
                mock_request,
                key_hash="key:tokens",
                tpm=1000,
                estimated_tokens=100
            )
        
        assert exc_info.value.status_code == 429


class TestMultiLevelRateLimiting:
    """Test multi-level rate limiting scenarios."""

    async def test_global_limit(self, memory_limiter):
        """Test global rate limiting."""
        # Simulate global limit
        for i in range(1000):
            await memory_limiter.check_rate_limit("global:requests", limit=1000)
        
        with pytest.raises(HTTPException):
            await memory_limiter.check_rate_limit("global:requests", limit=1000)

    async def test_per_key_limit(self, memory_limiter):
        """Test per-API-key rate limiting."""
        key = "sk-abc123"
        
        for i in range(100):
            await memory_limiter.check_rate_limit(f"ratelimit:requests:{key}", limit=100)
        
        with pytest.raises(HTTPException):
            await memory_limiter.check_rate_limit(f"ratelimit:requests:{key}", limit=100)

    async def test_per_model_limit(self, memory_limiter):
        """Test per-model rate limiting."""
        model = "gpt-4o"
        
        for i in range(50):
            await memory_limiter.check_rate_limit(f"ratelimit:tokens:{model}", limit=50)
        
        with pytest.raises(HTTPException):
            await memory_limiter.check_rate_limit(f"ratelimit:tokens:{model}", limit=50)

    async def test_combined_limits(self, memory_limiter):
        """Test that multiple limit levels work together."""
        # A request might check multiple limits
        checks = [
            ("global:requests", 10000),
            ("key:api_key_123", 100),
            ("user:user_456", 1000),
            ("model:gpt-4o", 500),
        ]
        
        # All should pass initially
        for key, limit in checks:
            info = await memory_limiter.check_rate_limit(key, limit=limit)
            assert isinstance(info, RateLimitInfo), f"Failed for {key}"
