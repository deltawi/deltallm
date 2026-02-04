"""Logging callback implementation."""

import json
import structlog
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TextIO

from deltallm.callbacks.base import Callback, RequestLog, RequestStatus

logger = structlog.get_logger()


class LoggingCallback(Callback):
    """Callback for logging requests to various destinations."""
    
    def __init__(
        self,
        *,
        file_path: Optional[str | Path] = None,
        console: bool = True,
        truncate_messages: bool = True,
        max_message_length: int = 1000,
    ) -> None:
        """Initialize the logging callback.
        
        Args:
            file_path: Path to log file (optional)
            console: Whether to log to console
            truncate_messages: Whether to truncate messages
            max_message_length: Maximum message length before truncation
        """
        self.file_path = Path(file_path) if file_path else None
        self.console = console
        self.truncate_messages = truncate_messages
        self.max_message_length = max_message_length
        self._file_handle: Optional[TextIO] = None
        
        # Open file if path provided
        if self.file_path:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            self._file_handle = open(self.file_path, "a")
    
    def _truncate_messages(self, messages: Optional[list]) -> Optional[list]:
        """Truncate messages for logging.
        
        Args:
            messages: List of messages
            
        Returns:
            Truncated messages
        """
        if not messages or not self.truncate_messages:
            return messages
        
        truncated = []
        for msg in messages:
            if isinstance(msg, dict) and "content" in msg:
                content = msg["content"]
                if isinstance(content, str) and len(content) > self.max_message_length:
                    msg = msg.copy()
                    msg["content"] = content[:self.max_message_length] + "... [truncated]"
            truncated.append(msg)
        
        return truncated
    
    def _log_to_dict(self, log: RequestLog) -> dict[str, Any]:
        """Convert log entry to dictionary.
        
        Args:
            log: Request log entry
            
        Returns:
            Dictionary representation
        """
        data = {
            "request_id": log.request_id,
            "timestamp": log.timestamp.isoformat(),
            "api_key": log.api_key,
            "model": log.model,
            "status": log.status.value,
            "prompt_tokens": log.prompt_tokens,
            "completion_tokens": log.completion_tokens,
            "total_tokens": log.total_tokens,
            "spend": log.spend,
            "latency_ms": log.latency_ms,
        }
        
        # Add optional fields
        if log.user_id:
            data["user_id"] = log.user_id
        if log.team_id:
            data["team_id"] = log.team_id
        if log.model_group:
            data["model_group"] = log.model_group
        if log.provider:
            data["provider"] = log.provider
        if log.ttft_ms is not None:
            data["ttft_ms"] = log.ttft_ms
        if log.error_type:
            data["error_type"] = log.error_type
        if log.error_message:
            data["error_message"] = log.error_message
        if log.request_tags:
            data["request_tags"] = log.request_tags
        if log.cache_hit:
            data["cache_hit"] = True
        
        # Add truncated messages if present
        if log.messages:
            data["messages"] = self._truncate_messages(log.messages)
        
        # Add metadata
        if log.metadata:
            data["metadata"] = log.metadata
        
        return data
    
    async def on_request_start(self, log: RequestLog) -> None:
        """Log request start.
        
        Args:
            log: Request log entry
        """
        data = self._log_to_dict(log)
        data["event"] = "request_start"
        
        if self.console:
            logger.info(
                "request_started",
                request_id=log.request_id,
                model=log.model,
                api_key=log.api_key[:8] + "..." if len(log.api_key) > 8 else log.api_key,
            )
        
        if self._file_handle:
            self._file_handle.write(json.dumps(data, default=str) + "\n")
            self._file_handle.flush()
    
    async def on_request_end(self, log: RequestLog, response: Optional[Any] = None) -> None:
        """Log request completion.
        
        Args:
            log: Request log entry
            response: Response object (optional)
        """
        data = self._log_to_dict(log)
        data["event"] = "request_end"
        
        if self.console:
            logger.info(
                "request_completed",
                request_id=log.request_id,
                model=log.model,
                tokens=log.total_tokens,
                spend=log.spend,
                latency_ms=log.latency_ms,
            )
        
        if self._file_handle:
            self._file_handle.write(json.dumps(data, default=str) + "\n")
            self._file_handle.flush()
    
    async def on_request_error(self, log: RequestLog, error: Exception) -> None:
        """Log request error.
        
        Args:
            log: Request log entry
            error: Exception that occurred
        """
        data = self._log_to_dict(log)
        data["event"] = "request_error"
        data["error_type"] = type(error).__name__
        data["error_message"] = str(error)
        
        if self.console:
            logger.error(
                "request_failed",
                request_id=log.request_id,
                model=log.model,
                error=type(error).__name__,
                error_message=str(error),
            )
        
        if self._file_handle:
            self._file_handle.write(json.dumps(data, default=str) + "\n")
            self._file_handle.flush()
    
    def close(self) -> None:
        """Close the log file."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
    
    def __del__(self) -> None:
        """Cleanup on deletion."""
        self.close()
