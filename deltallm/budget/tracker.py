"""Budget tracking and spend accumulation.

This module tracks spending and accumulates costs at organization,
team, and API key levels.
"""

import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import APIKey, Organization, SpendLog, Team

logger = logging.getLogger(__name__)


class BudgetTracker:
    """Tracks and accumulates spending at all levels.
    
    This class provides methods to:
    - Record spend logs for each request
    - Update accumulated spend at org/team/key level
    - Query current spend for budget checking
    """
    
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
    
    async def record_spend(
        self,
        request_id: str,
        api_key_id: Optional[UUID],
        user_id: Optional[UUID],
        team_id: Optional[UUID],
        org_id: Optional[UUID],
        model: str,
        endpoint_type: str = "chat",
        provider: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        audio_seconds: Optional[float] = None,
        audio_characters: Optional[int] = None,
        image_count: Optional[int] = None,
        image_size: Optional[str] = None,
        rerank_searches: Optional[int] = None,
        cost: Decimal = Decimal("0"),
        latency_ms: Optional[float] = None,
        status: str = "success",
        error_message: Optional[str] = None,
        request_tags: Optional[list] = None,
        metadata: Optional[dict] = None,
    ) -> SpendLog:
        """Record a spend log entry and update accumulated budgets.
        
        Args:
            request_id: Unique request identifier
            api_key_id: API key used for the request
            user_id: User who made the request
            team_id: Team context
            org_id: Organization context
            model: Model used
            endpoint_type: Type of endpoint (chat, embedding, audio_speech, etc.)
            provider: Provider that served the request
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            total_tokens: Total tokens used
            audio_seconds: Audio duration in seconds (for STT)
            audio_characters: Character count (for TTS)
            image_count: Number of images generated
            image_size: Image size (e.g., 1024x1024)
            rerank_searches: Number of rerank searches
            cost: Cost in USD
            latency_ms: Request latency in milliseconds
            status: Request status
            error_message: Error message if failed
            request_tags: Tags for the request
            metadata: Additional metadata
            
        Returns:
            The created SpendLog entry
        """
        # Create spend log entry
        spend_log = SpendLog(
            request_id=request_id,
            api_key_id=api_key_id,
            user_id=user_id,
            team_id=team_id,
            org_id=org_id,
            model=model,
            endpoint_type=endpoint_type,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            audio_seconds=audio_seconds,
            audio_characters=audio_characters,
            image_count=image_count,
            image_size=image_size,
            rerank_searches=rerank_searches,
            spend=cost,
            latency_ms=latency_ms,
            status=status,
            error_message=error_message,
            request_tags=request_tags or [],
            request_metadata=metadata or {},
        )
        
        self.db.add(spend_log)
        
        # Update accumulated spend at all levels
        await self._accumulate_spend(api_key_id, team_id, org_id, cost)
        
        await self.db.commit()
        
        logger.info(
            f"Recorded spend: request={request_id}, cost=${cost:.12f}, "
            f"model={model}, tokens={total_tokens}"
        )
        
        return spend_log
    
    async def _accumulate_spend(
        self,
        api_key_id: Optional[UUID],
        team_id: Optional[UUID],
        org_id: Optional[UUID],
        cost: Decimal,
    ) -> None:
        """Accumulate spend at API key, team, and organization levels.
        
        Args:
            api_key_id: API key to update
            team_id: Team to update
            org_id: Organization to update
            cost: Cost to add
        """
        # Update API key spend
        if api_key_id:
            await self.db.execute(
                update(APIKey)
                .where(APIKey.id == api_key_id)
                .values(spend=APIKey.spend + cost)
            )
        
        # Update team spend
        if team_id:
            await self.db.execute(
                update(Team)
                .where(Team.id == team_id)
                .values(spend=Team.spend + cost)
            )
        
        # Update org spend
        if org_id:
            await self.db.execute(
                update(Organization)
                .where(Organization.id == org_id)
                .values(spend=Organization.spend + cost)
            )
    
    async def get_org_spend(self, org_id: UUID) -> Decimal:
        """Get current spend for an organization.
        
        Args:
            org_id: Organization ID
            
        Returns:
            Current accumulated spend
        """
        result = await self.db.execute(
            select(Organization.spend).where(Organization.id == org_id)
        )
        return result.scalar_one_or_none() or Decimal("0")
    
    async def get_team_spend(self, team_id: UUID) -> Decimal:
        """Get current spend for a team.
        
        Args:
            team_id: Team ID
            
        Returns:
            Current accumulated spend
        """
        result = await self.db.execute(
            select(Team.spend).where(Team.id == team_id)
        )
        return result.scalar_one_or_none() or Decimal("0")
    
    async def get_key_spend(self, key_id: UUID) -> Decimal:
        """Get current spend for an API key.
        
        Args:
            key_id: API Key ID
            
        Returns:
            Current accumulated spend
        """
        result = await self.db.execute(
            select(APIKey.spend).where(APIKey.id == key_id)
        )
        return result.scalar_one_or_none() or Decimal("0")
    
    async def get_spend_logs(
        self,
        org_id: Optional[UUID] = None,
        team_id: Optional[UUID] = None,
        api_key_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        model: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SpendLog]:
        """Query spend logs with filters.
        
        Args:
            org_id: Filter by organization
            team_id: Filter by team
            api_key_id: Filter by API key
            user_id: Filter by user
            model: Filter by model
            start_time: Filter by start time (ISO format)
            end_time: Filter by end time (ISO format)
            limit: Maximum results to return
            offset: Results to skip
            
        Returns:
            List of SpendLog entries
        """
        from datetime import datetime
        
        query = select(SpendLog)
        
        if org_id:
            query = query.where(SpendLog.org_id == org_id)
        if team_id:
            query = query.where(SpendLog.team_id == team_id)
        if api_key_id:
            query = query.where(SpendLog.api_key_id == api_key_id)
        if user_id:
            query = query.where(SpendLog.user_id == user_id)
        if model:
            query = query.where(SpendLog.model == model)
        if start_time:
            query = query.where(SpendLog.created_at >= datetime.fromisoformat(start_time))
        if end_time:
            query = query.where(SpendLog.created_at <= datetime.fromisoformat(end_time))
        
        query = query.order_by(SpendLog.created_at.desc())
        query = query.limit(limit).offset(offset)
        
        result = await self.db.execute(query)
        return result.scalars().all()
    
    async def reset_key_spend(self, key_id: UUID) -> None:
        """Reset spend for an API key (admin only).
        
        Args:
            key_id: API Key ID to reset
        """
        await self.db.execute(
            update(APIKey)
            .where(APIKey.id == key_id)
            .values(spend=Decimal("0"))
        )
        await self.db.commit()
    
    async def reset_team_spend(self, team_id: UUID) -> None:
        """Reset spend for a team (admin only).
        
        Args:
            team_id: Team ID to reset
        """
        await self.db.execute(
            update(Team)
            .where(Team.id == team_id)
            .values(spend=Decimal("0"))
        )
        await self.db.commit()
    
    async def reset_org_spend(self, org_id: UUID) -> None:
        """Reset spend for an organization (admin only).
        
        Args:
            org_id: Organization ID to reset
        """
        await self.db.execute(
            update(Organization)
            .where(Organization.id == org_id)
            .values(spend=Decimal("0"))
        )
        await self.db.commit()
