"""Caching system for ProxyLLM."""

from deltallm.cache.base import CacheConfig, CacheEntry, CacheManager
from deltallm.cache.memory import InMemoryCache

# Optional Redis import
try:
    from deltallm.cache.redis_cache import RedisCache
except ImportError:
    RedisCache = None  # type: ignore

__all__ = [
    "CacheConfig",
    "CacheEntry",
    "CacheManager",
    "InMemoryCache",
    "RedisCache",
]
