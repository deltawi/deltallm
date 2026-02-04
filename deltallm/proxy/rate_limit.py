"""Rate limiting for the proxy server."""

import time
from typing import Optional
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


@dataclass
class RateLimitInfo:
    """Rate limit information."""
    
    limit: int
    remaining: int
    reset: int  # Unix timestamp
    window: int = 60  # seconds


class RateLimiter:
    """Rate limiter with sliding window."""
    
    def __init__(
        self,
        redis_client: Optional[any] = None,
        window_size: int = 60,
    ):
        """Initialize the rate limiter.
        
        Args:
            redis_client: Optional Redis client for distributed rate limiting
            window_size: Rate limit window in seconds
        """
        self.redis = redis_client
        self.window_size = window_size
        
        # In-memory storage (for non-distributed mode)
        self._windows: dict[str, list[float]] = {}
    
    def _get_key(self, identifier: str, limit_type: str = "requests") -> str:
        """Get rate limit key.
        
        Args:
            identifier: Rate limit identifier (e.g., api_key_hash)
            limit_type: Type of limit (requests, tokens)
            
        Returns:
            Key string
        """
        return f"ratelimit:{limit_type}:{identifier}"
    
    def _clean_window(self, timestamps: list[float]) -> list[float]:
        """Clean old entries from a window.
        
        Args:
            timestamps: List of timestamps
            
        Returns:
            Cleaned list
        """
        cutoff = time.time() - self.window_size
        return [t for t in timestamps if t > cutoff]
    
    async def check_rate_limit(
        self,
        identifier: str,
        limit: int,
        cost: int = 1,
        limit_type: str = "requests",
    ) -> RateLimitInfo:
        """Check if a request is within rate limits.
        
        Args:
            identifier: Rate limit identifier
            limit: Maximum allowed in window
            cost: Cost of this request
            limit_type: Type of limit
            
        Returns:
            Rate limit info
            
        Raises:
            HTTPException: If rate limit exceeded
        """
        key = self._get_key(identifier, limit_type)
        now = time.time()
        
        if self.redis:
            # Use Redis for distributed rate limiting
            # This is a simplified implementation
            # Production should use Redis sorted sets or Lua scripts
            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(key, 0, now - self.window_size)
            pipe.zcard(key)
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, self.window_size)
            _, current_count, _, _ = await pipe.execute()
            
            if current_count + cost > limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded for {limit_type}",
                    headers={
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(int(now + self.window_size)),
                    },
                )
            
            return RateLimitInfo(
                limit=limit,
                remaining=max(0, limit - current_count - cost),
                reset=int(now + self.window_size),
                window=self.window_size,
            )
        
        else:
            # Use in-memory storage
            if key not in self._windows:
                self._windows[key] = []
            
            # Clean old entries
            self._windows[key] = self._clean_window(self._windows[key])
            
            # Check limit
            current_count = len(self._windows[key])
            if current_count + cost > limit:
                reset_time = int(now + self.window_size)
                if self._windows[key]:
                    reset_time = int(self._windows[key][0] + self.window_size)
                
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded for {limit_type}",
                    headers={
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset_time),
                    },
                )
            
            # Record request
            for _ in range(cost):
                self._windows[key].append(now)
            
            return RateLimitInfo(
                limit=limit,
                remaining=limit - current_count - cost,
                reset=int(now + self.window_size),
                window=self.window_size,
            )
    
    def get_headers(self, info: RateLimitInfo) -> dict[str, str]:
        """Get rate limit headers.
        
        Args:
            info: Rate limit info
            
        Returns:
            Headers dictionary
        """
        return {
            "X-RateLimit-Limit": str(info.limit),
            "X-RateLimit-Remaining": str(max(0, info.remaining)),
            "X-RateLimit-Reset": str(info.reset),
        }


class RateLimitMiddleware:
    """Rate limiting middleware."""
    
    def __init__(
        self,
        rate_limiter: RateLimiter,
        default_rpm: int = 60,
        default_tpm: int = 10000,
    ):
        """Initialize the middleware.
        
        Args:
            rate_limiter: The rate limiter
            default_rpm: Default requests per minute
            default_tpm: Default tokens per minute
        """
        self.rate_limiter = rate_limiter
        self.default_rpm = default_rpm
        self.default_tpm = default_tpm
    
    async def check(
        self,
        request: Request,
        key_hash: str,
        rpm: Optional[int] = None,
        tpm: Optional[int] = None,
        estimated_tokens: int = 1000,
    ) -> RateLimitInfo:
        """Check rate limits for a request.
        
        Args:
            request: The request
            key_hash: API key hash
            rpm: Requests per minute limit
            tpm: Tokens per minute limit
            estimated_tokens: Estimated token usage
            
        Returns:
            Rate limit info
        """
        rpm = rpm or self.default_rpm
        tpm = tpm or self.default_tpm
        
        # Check request rate limit
        info = await self.rate_limiter.check_rate_limit(
            key_hash,
            limit=rpm,
            cost=1,
            limit_type="requests",
        )
        
        # Check token rate limit (if estimate provided)
        if estimated_tokens > 0:
            await self.rate_limiter.check_rate_limit(
                key_hash,
                limit=tpm,
                cost=min(estimated_tokens, tpm),  # Cap at limit
                limit_type="tokens",
            )
        
        return info
