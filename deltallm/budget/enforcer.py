"""Budget enforcement for organization, team, and API key levels.

This module enforces spending limits hierarchically:
- Organization level (highest)
- Team level (middle)
- API Key level (lowest)

If any level exceeds its budget, the request is rejected.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import APIKey, Organization, Team

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when a budget limit is exceeded."""
    
    def __init__(
        self,
        level: str,
        limit: Decimal,
        current: Decimal,
        entity_id: Optional[str] = None,
    ):
        self.level = level
        self.limit = limit
        self.current = current
        self.entity_id = entity_id
        
        entity_str = f" ({entity_id})" if entity_id else ""
        message = (
            f"Budget exceeded for {level}{entity_str}: "
            f"${current:.4f} spent of ${limit:.4f} limit"
        )
        super().__init__(message)


@dataclass
class BudgetCheckResult:
    """Result of a budget check."""
    
    allowed: bool
    level: Optional[str] = None
    limit: Optional[Decimal] = None
    current: Optional[Decimal] = None
    entity_id: Optional[str] = None
    message: Optional[str] = None


class BudgetEnforcer:
    """Enforces budget limits at org/team/key level.
    
    This class provides methods to check and enforce spending limits
    hierarchically. It queries the database for current spend and
    compares against configured limits.
    """
    
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
    
    async def check_all_budgets(
        self,
        key_id: UUID,
        estimated_cost: Optional[Decimal] = None,
    ) -> BudgetCheckResult:
        """Check all budget levels for an API key.
        
        Checks in order: key -> team -> org. Returns the first
        budget violation found.
        
        Args:
            key_id: The API key ID to check
            estimated_cost: Optional estimated cost for the request
            
        Returns:
            BudgetCheckResult indicating if the request is allowed
        """
        # Get API key with related entities
        result = await self.db.execute(
            select(APIKey).where(APIKey.id == key_id)
        )
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            return BudgetCheckResult(
                allowed=False,
                message="API key not found",
            )
        
        # Check key-level budget first
        if api_key.max_budget is not None:
            if api_key.spend >= api_key.max_budget:
                return BudgetCheckResult(
                    allowed=False,
                    level="api_key",
                    limit=api_key.max_budget,
                    current=api_key.spend,
                    entity_id=str(api_key.id),
                    message=f"API key budget exceeded: ${api_key.spend:.4f} of ${api_key.max_budget:.4f}",
                )
            
            # Check if estimated cost would exceed budget
            if estimated_cost and (api_key.spend + estimated_cost) > api_key.max_budget:
                return BudgetCheckResult(
                    allowed=False,
                    level="api_key",
                    limit=api_key.max_budget,
                    current=api_key.spend,
                    entity_id=str(api_key.id),
                    message=f"API key budget would be exceeded by this request",
                )
        
        # Check team-level budget
        if api_key.team_id:
            team_result = await self.check_team_budget(
                api_key.team_id,
                estimated_cost,
            )
            if not team_result.allowed:
                return team_result
        
        # Check org-level budget
        if api_key.org_id:
            org_result = await self.check_org_budget(
                api_key.org_id,
                estimated_cost,
            )
            if not org_result.allowed:
                return org_result
        
        return BudgetCheckResult(allowed=True)
    
    async def check_org_budget(
        self,
        org_id: UUID,
        estimated_cost: Optional[Decimal] = None,
    ) -> BudgetCheckResult:
        """Check organization budget.
        
        Args:
            org_id: Organization ID to check
            estimated_cost: Optional estimated cost for the request
            
        Returns:
            BudgetCheckResult
        """
        result = await self.db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        
        if not org:
            return BudgetCheckResult(
                allowed=False,
                message="Organization not found",
            )
        
        if org.max_budget is None:
            return BudgetCheckResult(allowed=True)
        
        if org.spend >= org.max_budget:
            return BudgetCheckResult(
                allowed=False,
                level="organization",
                limit=org.max_budget,
                current=org.spend,
                entity_id=str(org.id),
                message=f"Organization budget exceeded: ${org.spend:.4f} of ${org.max_budget:.4f}",
            )
        
        if estimated_cost and (org.spend + estimated_cost) > org.max_budget:
            return BudgetCheckResult(
                allowed=False,
                level="organization",
                limit=org.max_budget,
                current=org.spend,
                entity_id=str(org.id),
                message=f"Organization budget would be exceeded by this request",
            )
        
        return BudgetCheckResult(allowed=True)
    
    async def check_team_budget(
        self,
        team_id: UUID,
        estimated_cost: Optional[Decimal] = None,
    ) -> BudgetCheckResult:
        """Check team budget.
        
        Args:
            team_id: Team ID to check
            estimated_cost: Optional estimated cost for the request
            
        Returns:
            BudgetCheckResult
        """
        result = await self.db.execute(
            select(Team).where(Team.id == team_id)
        )
        team = result.scalar_one_or_none()
        
        if not team:
            return BudgetCheckResult(
                allowed=False,
                message="Team not found",
            )
        
        if team.max_budget is None:
            return BudgetCheckResult(allowed=True)
        
        if team.spend >= team.max_budget:
            return BudgetCheckResult(
                allowed=False,
                level="team",
                limit=team.max_budget,
                current=team.spend,
                entity_id=str(team.id),
                message=f"Team budget exceeded: ${team.spend:.4f} of ${team.max_budget:.4f}",
            )
        
        if estimated_cost and (team.spend + estimated_cost) > team.max_budget:
            return BudgetCheckResult(
                allowed=False,
                level="team",
                limit=team.max_budget,
                current=team.spend,
                entity_id=str(team.id),
                message=f"Team budget would be exceeded by this request",
            )
        
        return BudgetCheckResult(allowed=True)
    
    async def check_key_budget(
        self,
        key_id: UUID,
        estimated_cost: Optional[Decimal] = None,
    ) -> BudgetCheckResult:
        """Check API key budget.
        
        Args:
            key_id: API Key ID to check
            estimated_cost: Optional estimated cost for the request
            
        Returns:
            BudgetCheckResult
        """
        result = await self.db.execute(
            select(APIKey).where(APIKey.id == key_id)
        )
        api_key = result.scalar_one_or_none()
        
        if not api_key:
            return BudgetCheckResult(
                allowed=False,
                message="API key not found",
            )
        
        if api_key.max_budget is None:
            return BudgetCheckResult(allowed=True)
        
        if api_key.spend >= api_key.max_budget:
            return BudgetCheckResult(
                allowed=False,
                level="api_key",
                limit=api_key.max_budget,
                current=api_key.spend,
                entity_id=str(api_key.id),
                message=f"API key budget exceeded: ${api_key.spend:.4f} of ${api_key.max_budget:.4f}",
            )
        
        if estimated_cost and (api_key.spend + estimated_cost) > api_key.max_budget:
            return BudgetCheckResult(
                allowed=False,
                level="api_key",
                limit=api_key.max_budget,
                current=api_key.spend,
                entity_id=str(api_key.id),
                message=f"API key budget would be exceeded by this request",
            )
        
        return BudgetCheckResult(allowed=True)
    
    async def enforce_budgets(
        self,
        key_id: UUID,
        estimated_cost: Optional[Decimal] = None,
    ) -> None:
        """Enforce all budget levels, raising exception if exceeded.
        
        Args:
            key_id: The API key ID to check
            estimated_cost: Optional estimated cost for the request
            
        Raises:
            BudgetExceededError: If any budget limit is exceeded
        """
        result = await self.check_all_budgets(key_id, estimated_cost)
        
        if not result.allowed:
            if result.level:
                raise BudgetExceededError(
                    level=result.level,
                    limit=result.limit or Decimal("0"),
                    current=result.current or Decimal("0"),
                    entity_id=result.entity_id,
                )
            else:
                raise BudgetExceededError(
                    level="unknown",
                    limit=Decimal("0"),
                    current=Decimal("0"),
                    message=result.message,
                )
