"""Redis cache backend for distributed caching."""

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from deltallm.types import CompletionRequest, CompletionResponse
from deltallm.cache.base import CacheConfig, CacheEntry, CacheManager


class RedisCache(CacheManager):
    """Redis-based cache manager for distributed caching.
    
    This cache manager uses Redis for storing cached responses,
    enabling shared caching across multiple proxy instances.
    
    Example:
        ```python
        from deltallm.cache import RedisCache
        
        cache = RedisCache(
            redis_url="redis://localhost:6379/0",
            config=CacheConfig(ttl=3600, max_size=10000)
        )
        
        # Use with router
        from deltallm import Router
        router = Router(cache=cache)
        ```
    
    Attributes:
        redis: Redis client instance
        config: Cache configuration
        key_prefix: Prefix for all cache keys
    """
    
    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        config: Optional[CacheConfig] = None,
        key_prefix: str = "deltallm:cache:",
    ) -> None:
        """Initialize Redis cache.
        
        Args:
            redis_url: Redis connection URL
            config: Cache configuration
            key_prefix: Key prefix for cache entries
        """
        super().__init__(config)
        self.key_prefix = key_prefix
        self._redis_url = redis_url
        self._redis = None
        self._stats = {"hits": 0, "misses": 0, "sets": 0}
    
    def _get_redis(self):
        """Get or create Redis connection."""
        if self._redis is None:
            try:
                import redis.asyncio as redis
                self._redis = redis.from_url(self._redis_url, decode_responses=True)
            except ImportError:
                raise ImportError(
                    "Redis support requires 'redis' package. "
                    "Install with: pip install redis"
                )
        return self._redis
    
    def _make_key(self, key: str) -> str:
        """Create full cache key with prefix."""
        return f"{self.key_prefix}{key}"
    
    async def get(self, key: str) -> Optional[CacheEntry]:
        """Get entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cache entry or None
        """
        try:
            redis = self._get_redis()
            full_key = self._make_key(key)
            data = await redis.get(full_key)
            
            if data:
                self._stats["hits"] += 1
                entry_data = json.loads(data)
                return CacheEntry.from_dict(entry_data)
            else:
                self._stats["misses"] += 1
                return None
                
        except Exception:
            self._stats["misses"] += 1
            return None
    
    async def set(
        self,
        key: str,
        response: CompletionResponse,
        model: str,
        tokens: int = 0,
    ) -> None:
        """Set entry in cache.
        
        Args:
            key: Cache key
            response: Response to cache
            model: Model name
            tokens: Token count
        """
        try:
            redis = self._get_redis()
            full_key = self._make_key(key)
            
            now = datetime.utcnow()
            expires_at = now + timedelta(seconds=self.config.ttl)
            
            entry = CacheEntry(
                key=key,
                response=response,
                created_at=now,
                expires_at=expires_at,
                model=model,
                tokens=tokens,
            )
            
            # Store with TTL
            data = json.dumps(entry.to_dict())
            await redis.setex(full_key, self.config.ttl, data)
            self._stats["sets"] += 1
            
            # Manage max_size if set
            if self.config.max_size:
                await self._enforce_max_size()
                
        except Exception:
            pass  # Fail silently for cache operations
    
    async def _enforce_max_size(self) -> None:
        """Enforce maximum cache size by removing oldest entries."""
        try:
            redis = self._get_redis()
            pattern = f"{self.key_prefix}*"
            
            # Get all keys with their TTLs
            keys = []
            async for key in redis.scan_iter(match=pattern):
                ttl = await redis.ttl(key)
                keys.append((key, ttl))
            
            # Sort by TTL (ascending - keys with less TTL remaining are older)
            keys.sort(key=lambda x: x[1])
            
            # Remove oldest entries if over limit
            if len(keys) > self.config.max_size:
                to_remove = keys[:len(keys) - self.config.max_size]
                for key, _ in to_remove:
                    await redis.delete(key)
                    
        except Exception:
            pass
    
    async def delete(self, key: str) -> bool:
        """Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if deleted
        """
        try:
            redis = self._get_redis()
            full_key = self._make_key(key)
            result = await redis.delete(full_key)
            return result > 0
        except Exception:
            return False
    
    async def clear(self) -> None:
        """Clear all entries from cache."""
        try:
            redis = self._get_redis()
            pattern = f"{self.key_prefix}*"
            
            keys = []
            async for key in redis.scan_iter(match=pattern):
                keys.append(key)
            
            if keys:
                await redis.delete(*keys)
                
        except Exception:
            pass
    
    async def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Statistics dictionary
        """
        try:
            redis = self._get_redis()
            pattern = f"{self.key_prefix}*"
            
            count = 0
            async for _ in redis.scan_iter(match=pattern):
                count += 1
            
            total_requests = self._stats["hits"] + self._stats["misses"]
            hit_rate = (
                self._stats["hits"] / total_requests * 100
                if total_requests > 0
                else 0
            )
            
            return {
                "backend": "redis",
                "entries": count,
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "sets": self._stats["sets"],
                "hit_rate": round(hit_rate, 2),
                "config": {
                    "ttl": self.config.ttl,
                    "max_size": self.config.max_size,
                    "key_prefix": self.key_prefix,
                },
            }
        except Exception as e:
            return {
                "backend": "redis",
                "error": str(e),
                **self._stats,
            }
    
    async def health_check(self) -> dict[str, Any]:
        """Check Redis connection health.
        
        Returns:
            Health status dictionary
        """
        try:
            redis = self._get_redis()
            await redis.ping()
            info = await redis.info()
            
            return {
                "status": "healthy",
                "redis_version": info.get("redis_version"),
                "connected_clients": info.get("connected_clients"),
                "used_memory_human": info.get("used_memory_human"),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
            }
