"""In-memory cache implementation."""

import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Optional

from deltallm.cache.base import CacheManager, CacheEntry, CacheConfig
from deltallm.types import CompletionResponse


class InMemoryCache(CacheManager):
    """In-memory cache with LRU eviction."""
    
    def __init__(self, config: Optional[CacheConfig] = None) -> None:
        """Initialize the in-memory cache.
        
        Args:
            config: Cache configuration
        """
        super().__init__(config)
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._last_cleanup = time.time()
        self._cleanup_interval = 60  # Cleanup every 60 seconds
    
    def _cleanup_expired(self) -> None:
        """Remove expired entries from cache."""
        now = datetime.utcnow()
        expired_keys = [
            key for key, entry in self._cache.items()
            if entry.expires_at < now
        ]
        for key in expired_keys:
            del self._cache[key]
    
    def _maybe_cleanup(self) -> None:
        """Run cleanup if interval has passed."""
        now = time.time()
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup_expired()
            self._last_cleanup = now
    
    def _evict_if_needed(self) -> None:
        """Evict oldest entries if cache is at max size."""
        if self.config.max_size is None:
            return
        
        while len(self._cache) >= self.config.max_size:
            # Remove oldest entry (FIFO)
            self._cache.popitem(last=False)
    
    async def get(self, key: str) -> Optional[CacheEntry]:
        """Get entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cache entry or None
        """
        self._maybe_cleanup()
        
        entry = self._cache.get(key)
        
        if entry is None:
            self._misses += 1
            return None
        
        if entry.is_expired():
            del self._cache[key]
            self._misses += 1
            return None
        
        # Move to end (most recently used)
        self._cache.move_to_end(key)
        self._hits += 1
        
        return entry
    
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
        self._maybe_cleanup()
        self._evict_if_needed()
        
        now = datetime.utcnow()
        expires = now + timedelta(seconds=self.config.ttl)
        
        entry = CacheEntry(
            key=key,
            response=response,
            created_at=now,
            expires_at=expires,
            model=model,
            tokens=tokens,
        )
        
        self._cache[key] = entry
        self._cache.move_to_end(key)
    
    async def delete(self, key: str) -> bool:
        """Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if deleted
        """
        if key in self._cache:
            del self._cache[key]
            return True
        return False
    
    async def clear(self) -> None:
        """Clear all entries from cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
    
    async def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Statistics dictionary
        """
        total_requests = self._hits + self._misses
        hit_rate = self._hits / total_requests if total_requests > 0 else 0
        
        # Count non-expired entries
        now = datetime.utcnow()
        active_entries = sum(
            1 for entry in self._cache.values()
            if entry.expires_at > now
        )
        
        return {
            "backend": "memory",
            "size": len(self._cache),
            "active_entries": active_entries,
            "max_size": self.config.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": hit_rate,
            "ttl": self.config.ttl,
        }
