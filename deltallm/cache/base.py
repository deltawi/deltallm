"""Base cache interface."""

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Any, Optional

from deltallm.types import CompletionRequest, CompletionResponse


@dataclass
class CacheConfig:
    """Cache configuration.
    
    Attributes:
        ttl: Time to live in seconds
        max_size: Maximum number of entries
        similarity_threshold: Threshold for semantic cache (0-1)
        enabled: Whether caching is enabled
        cache_streaming: Whether to cache streaming responses
        excluded_models: Models to exclude from caching
    """
    ttl: int = 3600  # 1 hour
    max_size: Optional[int] = None
    similarity_threshold: float = 0.95
    enabled: bool = True
    cache_streaming: bool = False
    excluded_models: list[str] = None
    
    def __post_init__(self):
        if self.excluded_models is None:
            self.excluded_models = []


@dataclass
class CacheEntry:
    """Cache entry.
    
    Attributes:
        key: Cache key
        response: Cached response
        created_at: Creation timestamp
        expires_at: Expiration timestamp
        model: Model name
        tokens: Token count
    """
    key: str
    response: CompletionResponse
    created_at: datetime
    expires_at: datetime
    model: str
    tokens: int = 0
    
    def is_expired(self) -> bool:
        """Check if entry is expired.
        
        Returns:
            True if expired
        """
        return datetime.utcnow() > self.expires_at
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary.
        
        Returns:
            Dictionary representation
        """
        return {
            "key": self.key,
            "response": self.response.model_dump(),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "model": self.model,
            "tokens": self.tokens,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CacheEntry":
        """Create from dictionary.
        
        Args:
            data: Dictionary data
            
        Returns:
            CacheEntry instance
        """
        return cls(
            key=data["key"],
            response=CompletionResponse(**data["response"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            model=data["model"],
            tokens=data.get("tokens", 0),
        )


class CacheManager(ABC):
    """Abstract base class for cache managers."""
    
    def __init__(self, config: Optional[CacheConfig] = None) -> None:
        """Initialize the cache manager.
        
        Args:
            config: Cache configuration
        """
        self.config = config or CacheConfig()
    
    def _generate_cache_key(
        self,
        request: CompletionRequest,
        *,
        include_temperature: bool = True,
        include_max_tokens: bool = True,
    ) -> str:
        """Generate cache key from request.
        
        Args:
            request: Completion request
            include_temperature: Whether to include temperature in key
            include_max_tokens: Whether to include max_tokens in key
            
        Returns:
            Cache key string
        """
        # Build cacheable data
        data = {
            "model": request.model,
            "messages": [msg.model_dump() for msg in request.messages],
        }
        
        # Include optional parameters
        if include_temperature and request.temperature is not None:
            data["temperature"] = request.temperature
        
        if include_max_tokens and request.max_tokens is not None:
            data["max_tokens"] = request.max_tokens
        
        if request.top_p is not None:
            data["top_p"] = request.top_p
        
        if request.tools:
            data["tools"] = [tool.model_dump() for tool in request.tools]
        
        if request.response_format:
            data["response_format"] = request.response_format.model_dump()
        
        # Generate hash
        json_str = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(json_str.encode()).hexdigest()
    
    def _should_cache(self, request: CompletionRequest) -> bool:
        """Determine if request should be cached.
        
        Args:
            request: Completion request
            
        Returns:
            True if should cache
        """
        if not self.config.enabled:
            return False
        
        if request.model in self.config.excluded_models:
            return False
        
        if request.stream and not self.config.cache_streaming:
            return False
        
        return True
    
    @abstractmethod
    async def get(self, key: str) -> Optional[CacheEntry]:
        """Get entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cache entry or None
        """
        pass
    
    @abstractmethod
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
        pass
    
    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if deleted
        """
        pass
    
    @abstractmethod
    async def clear(self) -> None:
        """Clear all entries from cache."""
        pass
    
    async def get_response(
        self,
        request: CompletionRequest,
    ) -> Optional[CompletionResponse]:
        """Get cached response for request.
        
        Args:
            request: Completion request
            
        Returns:
            Cached response or None
        """
        if not self._should_cache(request):
            return None
        
        key = self._generate_cache_key(request)
        entry = await self.get(key)
        
        if entry and not entry.is_expired():
            return entry.response
        
        return None
    
    async def cache_response(
        self,
        request: CompletionRequest,
        response: CompletionResponse,
    ) -> None:
        """Cache response for request.
        
        Args:
            request: Completion request
            response: Response to cache
        """
        if not self._should_cache(request):
            return
        
        key = self._generate_cache_key(request)
        tokens = response.usage.total_tokens if response.usage else 0
        
        await self.set(key, response, request.model, tokens)
    
    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Statistics dictionary
        """
        pass
