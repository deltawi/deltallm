"""Spend tracking integration for proxy routes.

This module provides utilities for recording spend logs from API endpoints.
It handles both synchronous and asynchronous spend recording.
"""

import logging
import time
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.budget.tracker import BudgetTracker
from deltallm.db.models import APIKey
from deltallm.db.session import get_session
from deltallm.proxy.dependencies import AuthContext

logger = logging.getLogger(__name__)


class SpendTrackingContext:
    """Context manager for tracking spend in API endpoints.
    
    Usage:
        async with SpendTrackingContext(
            request=request,
            auth_context=auth_context,
            model="gpt-4o",
            endpoint_type="chat",
        ) as tracker:
            # ... process request ...
            tracker.record_success(
                prompt_tokens=100,
                completion_tokens=50,
                cost=Decimal("0.015"),
            )
    """
    
    def __init__(
        self,
        request: Request,
        auth_context: AuthContext,
        model: str,
        endpoint_type: str,
        db_session: Optional[AsyncSession] = None,
    ):
        """Initialize the spend tracking context.
        
        Args:
            request: FastAPI request object
            auth_context: Authentication context
            model: Model name
            endpoint_type: Type of endpoint (chat, audio_speech, etc.)
            db_session: Database session (optional, will get from request state if not provided)
        """
        self.request = request
        self.auth_context = auth_context
        self.model = model
        self.endpoint_type = endpoint_type
        self.db_session = db_session
        
        self.request_id = str(uuid4())
        self.start_time: Optional[float] = None
        self.key_info = auth_context.key_info
        
        # Get API key ID from database model if available (for DB-backed keys)
        # For in-memory keys (sk-proxy-*), this will be None
        self.api_key_id = None
        if auth_context.api_key_model and hasattr(auth_context.api_key_model, 'id'):
            self.api_key_id = auth_context.api_key_model.id
        
    async def __aenter__(self):
        """Enter the context."""
        self.start_time = time.time()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the context, recording failure if an exception occurred."""
        if exc_val is not None:
            # Record the failure
            try:
                await self.record_failure(
                    error_message=str(exc_val),
                    cost=Decimal("0"),
                )
            except Exception as e:
                logger.exception(f"Failed to record failure spend log: {e}")
        return False  # Don't suppress exceptions
    
    def _get_db_session(self) -> Optional[AsyncSession]:
        """Get database session from context."""
        if self.db_session:
            return self.db_session
        return None
    
    def _get_session_context(self):
        """Get session context manager - either wraps existing session or creates new one."""
        db = self._get_db_session()
        if db:
            # Use existing session - wrap it so we don't manage its lifecycle
            class ExistingSessionWrapper:
                def __init__(self, session):
                    self._session = session
                async def __aenter__(self):
                    return self._session
                async def __aexit__(self, *args):
                    # Don't close existing session
                    pass
            return ExistingSessionWrapper(db)
        else:
            # Create new session using context manager
            return get_session()
    
    def _get_latency_ms(self) -> float:
        """Calculate latency in milliseconds."""
        if self.start_time is None:
            return 0.0
        return (time.time() - self.start_time) * 1000
    
    async def record_success(
        self,
        cost: Decimal,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        audio_seconds: Optional[float] = None,
        audio_characters: Optional[int] = None,
        image_count: Optional[int] = None,
        image_size: Optional[str] = None,
        rerank_searches: Optional[int] = None,
        provider: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Record a successful request.
        
        Args:
            cost: Cost of the request
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            total_tokens: Total tokens used
            audio_seconds: Audio duration in seconds (for STT)
            audio_characters: Character count (for TTS)
            image_count: Number of images generated
            image_size: Image size (e.g., 1024x1024)
            rerank_searches: Number of rerank searches
            provider: Provider that served the request
            metadata: Additional metadata
        """
        logger.info(f"record_success called: model={self.model}, cost={cost}, tokens={prompt_tokens}/{completion_tokens}")
        try:
            async with self._get_session_context() as db:
                logger.info(f"Database session acquired: {db is not None}")
                tracker = BudgetTracker(db)
                
                logger.info(f"Calling tracker.record_spend with request_id={self.request_id}")
                await tracker.record_spend(
                    request_id=self.request_id,
                    api_key_id=self.api_key_id,
                    user_id=None,  # TODO: Get from auth_context when available
                    team_id=self.key_info.team_id if self.key_info else None,
                    org_id=self.key_info.org_id if self.key_info else None,
                    model=self.model,
                    endpoint_type=self.endpoint_type,
                    provider=provider,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    audio_seconds=audio_seconds,
                    audio_characters=audio_characters,
                    image_count=image_count,
                    image_size=image_size,
                    rerank_searches=rerank_searches,
                    cost=cost,
                    latency_ms=self._get_latency_ms(),
                    status="success",
                    metadata=metadata,
                )
                
                logger.info(
                    f"Recorded successful spend: request_id={self.request_id}, "
                    f"cost=${float(cost):.12f}, model={self.model}"
                )
                
        except Exception as e:
            logger.exception(f"Failed to record spend log: {e}")
            # Still try to update accumulated spend
            await self._update_accumulated_spend(cost)
    
    async def record_failure(
        self,
        error_message: str,
        cost: Decimal = Decimal("0"),
        provider: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Record a failed request.
        
        Args:
            error_message: Error message
            cost: Cost of the request (usually 0 for failures)
            provider: Provider that served the request
            metadata: Additional metadata
        """
        try:
            async with self._get_session_context() as db:
                tracker = BudgetTracker(db)
                
                await tracker.record_spend(
                    request_id=self.request_id,
                    api_key_id=self.api_key_id,
                    user_id=None,
                    team_id=self.key_info.team_id if self.key_info else None,
                    org_id=self.key_info.org_id if self.key_info else None,
                    model=self.model,
                    endpoint_type=self.endpoint_type,
                    provider=provider,
                    cost=cost,
                    latency_ms=self._get_latency_ms(),
                    status="failure",
                    error_message=error_message,
                    metadata=metadata,
                )
                
                logger.info(
                    f"Recorded failed spend: request_id={self.request_id}, "
                    f"error={error_message[:50]}..."
                )
                
        except Exception as e:
            logger.exception(f"Failed to record error spend log: {e}")
    
    async def _update_accumulated_spend(self, cost: Decimal) -> None:
        """Update accumulated spend via key manager (fallback)."""
        if self.key_info and hasattr(self.request.app.state, 'key_manager'):
            try:
                self.request.app.state.key_manager.update_spend(
                    self.key_info.key_hash,
                    float(cost)
                )
            except Exception as e:
                logger.exception(f"Failed to update accumulated spend: {e}")


async def record_spend_from_endpoint(
    request: Request,
    auth_context: AuthContext,
    model: str,
    endpoint_type: str,
    cost: Decimal,
    db_session: Optional[AsyncSession] = None,
    **kwargs
) -> None:
    """Convenience function to record spend from an endpoint.
    
    This is a simpler alternative to the context manager for basic usage.
    
    Args:
        request: FastAPI request object
        auth_context: Authentication context
        model: Model name
        endpoint_type: Type of endpoint
        cost: Cost of the request
        db_session: Database session (optional)
        **kwargs: Additional fields (prompt_tokens, completion_tokens, etc.)
    """
    key_info = auth_context.key_info
    
    # Get API key ID from database model if available
    api_key_id = None
    if auth_context.api_key_model and hasattr(auth_context.api_key_model, 'id'):
        api_key_id = auth_context.api_key_model.id
    
    # Use provided session or create new one
    if db_session:
        try:
            tracker = BudgetTracker(db_session)
            
            await tracker.record_spend(
                request_id=str(uuid4()),
                api_key_id=api_key_id,
                user_id=None,
                team_id=key_info.team_id if key_info else None,
                org_id=key_info.org_id if key_info else None,
                model=model,
                endpoint_type=endpoint_type,
                cost=cost,
                **kwargs
            )
            return
        except Exception as e:
            logger.exception(f"Failed to record spend with provided session: {e}")
    
    # Create new session using context manager
    try:
        async with get_session() as db:
            tracker = BudgetTracker(db)
            
            await tracker.record_spend(
                request_id=str(uuid4()),
                api_key_id=api_key_id,
                user_id=None,
                team_id=key_info.team_id if key_info else None,
                org_id=key_info.org_id if key_info else None,
                model=model,
                endpoint_type=endpoint_type,
                cost=cost,
                **kwargs
            )
            
    except Exception as e:
        logger.exception(f"Failed to record spend: {e}")
        # Fallback to key manager
        if key_info and hasattr(request.app.state, 'key_manager'):
            try:
                request.app.state.key_manager.update_spend(
                    key_info.key_hash,
                    float(cost)
                )
            except Exception as km_error:
                logger.exception(f"Failed to update accumulated spend: {km_error}")
