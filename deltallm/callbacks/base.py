"""Base callback interface for observability."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from enum import Enum


class RequestStatus(Enum):
    """Request status enum."""
    STARTED = "started"
    SUCCESS = "success"
    ERROR = "error"


@dataclass
class RequestLog:
    """Request log entry.
    
    Attributes:
        request_id: Unique request ID
        timestamp: Request timestamp
        api_key: Hashed API key
        user_id: User ID (optional)
        team_id: Team ID (optional)
        model: Model name
        model_group: Model group/routing group
        messages: Request messages (truncated for logging)
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        total_tokens: Total tokens used
        spend: Cost of the request
        latency_ms: Total latency in milliseconds
        ttft_ms: Time to first token in milliseconds (for streaming)
        status: Request status
        error_type: Error type if failed
        error_message: Error message if failed
        request_tags: Tags for the request
        metadata: Additional metadata
        provider: Provider name
        cache_hit: Whether this was a cache hit
    """
    request_id: str
    timestamp: datetime
    api_key: str  # Hashed
    model: str
    
    # Optional fields
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    model_group: Optional[str] = None
    messages: Optional[list] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    spend: float = 0.0
    latency_ms: float = 0.0
    ttft_ms: Optional[float] = None
    status: RequestStatus = RequestStatus.STARTED
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    request_tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provider: Optional[str] = None
    cache_hit: bool = False


class Callback(ABC):
    """Base class for observability callbacks."""
    
    @abstractmethod
    async def on_request_start(self, log: RequestLog) -> None:
        """Called when a request starts.
        
        Args:
            log: Request log entry
        """
        pass
    
    @abstractmethod
    async def on_request_end(self, log: RequestLog, response: Optional[Any] = None) -> None:
        """Called when a request completes successfully.
        
        Args:
            log: Request log entry
            response: Response object (optional)
        """
        pass
    
    @abstractmethod
    async def on_request_error(self, log: RequestLog, error: Exception) -> None:
        """Called when a request fails.
        
        Args:
            log: Request log entry
            error: Exception that occurred
        """
        pass


class CallbackManager:
    """Manager for multiple callbacks."""
    
    def __init__(self) -> None:
        """Initialize the callback manager."""
        self._callbacks: list[Callback] = []
    
    def register(self, callback: Callback) -> None:
        """Register a callback.
        
        Args:
            callback: Callback to register
        """
        self._callbacks.append(callback)
    
    def unregister(self, callback: Callback) -> None:
        """Unregister a callback.
        
        Args:
            callback: Callback to unregister
        """
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    async def on_request_start(self, log: RequestLog) -> None:
        """Notify all callbacks of request start.
        
        Args:
            log: Request log entry
        """
        for callback in self._callbacks:
            try:
                await callback.on_request_start(log)
            except Exception:
                # Don't let callback errors break the request
                pass
    
    async def on_request_end(self, log: RequestLog, response: Optional[Any] = None) -> None:
        """Notify all callbacks of request end.
        
        Args:
            log: Request log entry
            response: Response object (optional)
        """
        for callback in self._callbacks:
            try:
                await callback.on_request_end(log, response)
            except Exception:
                # Don't let callback errors break the request
                pass
    
    async def on_request_error(self, log: RequestLog, error: Exception) -> None:
        """Notify all callbacks of request error.
        
        Args:
            log: Request log entry
            error: Exception that occurred
        """
        for callback in self._callbacks:
            try:
                await callback.on_request_error(log, error)
            except Exception:
                # Don't let callback errors break the request
                pass
