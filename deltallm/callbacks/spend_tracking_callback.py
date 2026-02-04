"""Spend tracking callback for persisting request logs to database.

This callback integrates with the BudgetTracker to record spend logs
for every request, enabling comprehensive cost tracking and analytics.
"""

import logging
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from deltallm.callbacks.base import Callback, RequestLog, RequestStatus
from deltallm.budget.tracker import BudgetTracker

logger = logging.getLogger(__name__)


class SpendTrackingCallback(Callback):
    """Callback for tracking spend in the database.
    
    This callback records every request to the spend_logs table,
    including cost, usage, and metadata for analytics.
    """
    
    def __init__(self, budget_tracker: BudgetTracker) -> None:
        """Initialize the spend tracking callback.
        
        Args:
            budget_tracker: The BudgetTracker instance to use for recording spend
        """
        self.budget_tracker = budget_tracker
    
    def _parse_endpoint_type(self, model: str, metadata: Optional[dict] = None) -> str:
        """Determine endpoint type from model and metadata.
        
        Args:
            model: Model name
            metadata: Request metadata
            
        Returns:
            Endpoint type string
        """
        # Check metadata first
        if metadata:
            endpoint_type = metadata.get("endpoint_type")
            if endpoint_type:
                return endpoint_type
        
        # Infer from model name
        model_lower = model.lower()
        
        # TTS models
        if model_lower.startswith("tts-"):
            return "audio_speech"
        
        # STT models
        if model_lower.startswith("whisper-"):
            return "audio_transcription"
        
        # Embedding models
        if "embedding" in model_lower:
            return "embedding"
        
        # Image generation models
        if model_lower.startswith("dall-") or "image" in model_lower:
            return "image"
        
        # Rerank models
        if "rerank" in model_lower:
            return "rerank"
        
        # Moderation models
        if "moderation" in model_lower:
            return "moderation"
        
        # Default to chat
        return "chat"
    
    def _parse_uuid(self, value: Optional[str]) -> Optional[UUID]:
        """Parse a UUID string to UUID object.
        
        Args:
            value: UUID string
            
        Returns:
            UUID object or None
        """
        if not value:
            return None
        try:
            return UUID(value)
        except (ValueError, TypeError):
            return None
    
    async def on_request_start(self, log: RequestLog) -> None:
        """Called when a request starts.
        
        We don't record spend on start - wait for completion.
        
        Args:
            log: Request log entry
        """
        pass
    
    async def on_request_end(self, log: RequestLog, response: Optional[Any] = None) -> None:
        """Record spend log when a request completes.
        
        Args:
            log: Request log entry
            response: Response object (optional)
        """
        try:
            # Determine endpoint type
            endpoint_type = self._parse_endpoint_type(log.model, log.metadata)
            
            # Parse UUIDs
            api_key_id = self._parse_uuid(log.metadata.get("api_key_id")) if log.metadata else None
            user_id = self._parse_uuid(log.user_id)
            team_id = self._parse_uuid(log.team_id)
            org_id = self._parse_uuid(log.metadata.get("org_id")) if log.metadata else None
            
            # Extract endpoint-specific fields from metadata
            audio_seconds = log.metadata.get("audio_seconds") if log.metadata else None
            audio_characters = log.metadata.get("audio_characters") if log.metadata else None
            image_count = log.metadata.get("image_count") if log.metadata else None
            image_size = log.metadata.get("image_size") if log.metadata else None
            rerank_searches = log.metadata.get("rerank_searches") if log.metadata else None
            
            # Convert audio_seconds to float if present
            if audio_seconds is not None:
                try:
                    audio_seconds = float(audio_seconds)
                except (ValueError, TypeError):
                    audio_seconds = None
            
            # Convert audio_characters to int if present
            if audio_characters is not None:
                try:
                    audio_characters = int(audio_characters)
                except (ValueError, TypeError):
                    audio_characters = None
            
            # Convert image_count to int if present
            if image_count is not None:
                try:
                    image_count = int(image_count)
                except (ValueError, TypeError):
                    image_count = None
            
            # Convert rerank_searches to int if present
            if rerank_searches is not None:
                try:
                    rerank_searches = int(rerank_searches)
                except (ValueError, TypeError):
                    rerank_searches = None
            
            # Record the spend
            await self.budget_tracker.record_spend(
                request_id=log.request_id,
                api_key_id=api_key_id,
                user_id=user_id,
                team_id=team_id,
                org_id=org_id,
                model=log.model,
                endpoint_type=endpoint_type,
                provider=log.provider,
                prompt_tokens=log.prompt_tokens if log.prompt_tokens > 0 else None,
                completion_tokens=log.completion_tokens if log.completion_tokens > 0 else None,
                total_tokens=log.total_tokens if log.total_tokens > 0 else None,
                audio_seconds=audio_seconds,
                audio_characters=audio_characters,
                image_count=image_count,
                image_size=image_size,
                rerank_searches=rerank_searches,
                cost=Decimal(str(log.spend)) if log.spend else Decimal("0"),
                latency_ms=log.latency_ms if log.latency_ms > 0 else None,
                status="success" if log.status == RequestStatus.SUCCESS else "failure",
                error_message=log.error_message,
                request_tags=log.request_tags if log.request_tags else None,
                metadata=log.metadata,
            )
            
            logger.debug(
                f"Recorded spend log: request_id={log.request_id}, "
                f"model={log.model}, endpoint={endpoint_type}, "
                f"cost=${log.spend:.6f}"
            )
            
        except Exception as e:
            # Don't let spend tracking errors break the request
            logger.exception(f"Failed to record spend log: {e}")
    
    async def on_request_error(self, log: RequestLog, error: Exception) -> None:
        """Record spend log when a request fails.
        
        Args:
            log: Request log entry
            error: Exception that occurred
        """
        try:
            # Determine endpoint type
            endpoint_type = self._parse_endpoint_type(log.model, log.metadata)
            
            # Parse UUIDs
            api_key_id = self._parse_uuid(log.metadata.get("api_key_id")) if log.metadata else None
            user_id = self._parse_uuid(log.user_id)
            team_id = self._parse_uuid(log.team_id)
            org_id = self._parse_uuid(log.metadata.get("org_id")) if log.metadata else None
            
            # Record the failed request
            await self.budget_tracker.record_spend(
                request_id=log.request_id,
                api_key_id=api_key_id,
                user_id=user_id,
                team_id=team_id,
                org_id=org_id,
                model=log.model,
                endpoint_type=endpoint_type,
                provider=log.provider,
                prompt_tokens=log.prompt_tokens if log.prompt_tokens > 0 else None,
                completion_tokens=log.completion_tokens if log.completion_tokens > 0 else None,
                total_tokens=log.total_tokens if log.total_tokens > 0 else None,
                cost=Decimal("0"),  # No cost for failed requests
                latency_ms=log.latency_ms if log.latency_ms > 0 else None,
                status="failure",
                error_message=log.error_message or str(error),
                request_tags=log.request_tags if log.request_tags else None,
                metadata=log.metadata,
            )
            
            logger.debug(
                f"Recorded failed request spend log: request_id={log.request_id}, "
                f"error={type(error).__name__}"
            )
            
        except Exception as e:
            # Don't let spend tracking errors break the request
            logger.exception(f"Failed to record error spend log: {e}")
