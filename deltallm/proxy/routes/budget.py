"""Budget and spend tracking routes.

This module provides API endpoints for:
- Viewing spend logs
- Checking budget status
- Managing budget limits
"""

import logging
from typing import Annotated, Optional
from uuid import UUID
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from deltallm.db.session import get_db_session
from deltallm.db.models import Organization, SpendLog, Team, APIKey, User
from deltallm.proxy.auth import get_current_user_optional
from deltallm.proxy.dependencies import require_user
from deltallm.budget.tracker import BudgetTracker
from deltallm.budget.enforcer import BudgetEnforcer
from deltallm.rbac.exceptions import PermissionDeniedError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["budget"])


# ========== Schemas ==========

class SpendLogResponse(BaseModel):
    """Spend log entry response."""
    id: str
    request_id: str
    api_key_id: Optional[str] = None
    user_id: Optional[str] = None
    team_id: Optional[str] = None
    org_id: Optional[str] = None
    model: str
    provider: Optional[str] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    spend: float
    latency_ms: Optional[float] = None
    status: str
    error_message: Optional[str] = None
    request_tags: list[str] = []
    created_at: datetime


class SpendLogListResponse(BaseModel):
    """List of spend logs response."""
    total: int
    logs: list[SpendLogResponse]


class BudgetStatusResponse(BaseModel):
    """Budget status for an entity."""
    entity_type: str
    entity_id: str
    entity_name: Optional[str] = None
    max_budget: Optional[float] = None
    current_spend: float
    remaining_budget: Optional[float] = None
    budget_utilization_percent: Optional[float] = None
    is_exceeded: bool = False


class OrganizationBudgetResponse(BaseModel):
    """Organization budget with team breakdown."""
    org_budget: BudgetStatusResponse
    team_budgets: list[BudgetStatusResponse]


class SetBudgetRequest(BaseModel):
    """Request to set a budget limit."""
    max_budget: float = Field(gt=0, description="Maximum budget limit in USD")


class SpendSummaryResponse(BaseModel):
    """Summary of spend over a time period."""
    total_spend: float
    total_requests: int
    total_tokens: int
    successful_requests: int
    failed_requests: int
    avg_latency_ms: Optional[float] = None
    top_models: list[dict]
    daily_breakdown: list[dict]


# ========== Helper Functions ==========

def format_budget_status(
    entity_type: str,
    entity_id: str,
    entity_name: Optional[str],
    max_budget: Optional[Decimal],
    current_spend: Decimal,
) -> BudgetStatusResponse:
    """Format budget status for response."""
    max_budget_float = float(max_budget) if max_budget else None
    current_spend_float = float(current_spend)
    
    remaining = None
    utilization = None
    is_exceeded = False
    
    if max_budget is not None:
        remaining = max_budget_float - current_spend_float
        utilization = (current_spend_float / max_budget_float) * 100 if max_budget_float > 0 else 0
        is_exceeded = current_spend >= max_budget
    
    return BudgetStatusResponse(
        entity_type=entity_type,
        entity_id=str(entity_id),
        entity_name=entity_name,
        max_budget=max_budget_float,
        current_spend=current_spend_float,
        remaining_budget=remaining,
        budget_utilization_percent=utilization,
        is_exceeded=is_exceeded,
    )


# ========== Routes ==========

@router.get("/budget/logs", response_model=SpendLogListResponse)
async def list_spend_logs(
    org_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    api_key_id: Optional[UUID] = None,
    model: Optional[str] = None,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    current_user: Annotated[User, Depends(get_current_user_optional)] = None,
):
    """List spend logs with filtering.
    
    Users can only view logs for organizations they are members of.
    """
    # Calculate date range
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)
    
    # Build query
    from sqlalchemy import select, func
    
    query = select(SpendLog)
    
    # Apply filters
    if org_id:
        query = query.where(SpendLog.org_id == org_id)
    if team_id:
        query = query.where(SpendLog.team_id == team_id)
    if api_key_id:
        query = query.where(SpendLog.api_key_id == api_key_id)
    if model:
        query = query.where(SpendLog.model == model)
    
    query = query.where(SpendLog.created_at >= start_time)
    query = query.where(SpendLog.created_at <= end_time)
    
    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()
    
    # Get paginated results
    query = query.order_by(SpendLog.created_at.desc())
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()
    
    return SpendLogListResponse(
        total=total,
        logs=[
            SpendLogResponse(
                id=str(log.id),
                request_id=log.request_id,
                api_key_id=str(log.api_key_id) if log.api_key_id else None,
                user_id=str(log.user_id) if log.user_id else None,
                team_id=str(log.team_id) if log.team_id else None,
                org_id=str(log.org_id) if log.org_id else None,
                model=log.model,
                provider=log.provider,
                prompt_tokens=log.prompt_tokens or 0,
                completion_tokens=log.completion_tokens or 0,
                total_tokens=log.total_tokens or 0,
                spend=float(log.spend),
                latency_ms=float(log.latency_ms) if log.latency_ms else None,
                status=log.status,
                error_message=log.error_message,
                request_tags=log.request_tags or [],
                created_at=log.created_at,
            )
            for log in logs
        ],
    )


@router.get("/budget/org/{org_id}", response_model=BudgetStatusResponse)
async def get_org_budget(
    org_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
):
    """Get budget status for an organization.
    
    Requires org:view_budget permission.
    """
    try:
        # Check permission
        from deltallm.rbac.manager import RBACManager
        rbac = RBACManager(db)
        await rbac.require_permission(
            current_user.id, org_id, "org", "view_budget"
        )
        
        # Get organization
        result = await db.execute(
            select(Organization).where(Organization.id == org_id)
        )
        org = result.scalar_one_or_none()
        
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organization not found",
            )
        
        return format_budget_status(
            entity_type="organization",
            entity_id=str(org.id),
            entity_name=org.name,
            max_budget=org.max_budget,
            current_spend=org.spend,
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error in get_org_budget: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get organization budget: {str(e)}"
        )


@router.get("/budget/org/{org_id}/full", response_model=OrganizationBudgetResponse)
async def get_org_budget_full(
    org_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
):
    """Get full budget breakdown for organization including teams.
    
    Requires org:view_budget permission.
    """
    try:
        # Check permission
        from deltallm.rbac.manager import RBACManager
        rbac = RBACManager(db)
        await rbac.require_permission(
            current_user.id, org_id, "org", "view_budget"
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.exception(f"Error checking permission in get_org_budget_full: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permission check failed"
        )
    
    # Get organization with teams
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    
    # Get team budgets
    result = await db.execute(
        select(Team).where(Team.org_id == org_id)
    )
    teams = result.scalars().all()
    
    org_budget = format_budget_status(
        entity_type="organization",
        entity_id=str(org.id),
        entity_name=org.name,
        max_budget=org.max_budget,
        current_spend=org.spend,
    )
    
    team_budgets = [
        format_budget_status(
            entity_type="team",
            entity_id=str(team.id),
            entity_name=team.name,
            max_budget=team.max_budget,
            current_spend=team.spend,
        )
        for team in teams
    ]
    
    return OrganizationBudgetResponse(
        org_budget=org_budget,
        team_budgets=team_budgets,
    )


@router.post("/budget/org/{org_id}/set", response_model=BudgetStatusResponse)
async def set_org_budget(
    org_id: UUID,
    data: SetBudgetRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
):
    """Set budget limit for an organization.
    
    Requires org:manage_budget permission.
    """
    try:
        # Check permission
        from deltallm.rbac.manager import RBACManager
        rbac = RBACManager(db)
        await rbac.require_permission(
            current_user.id, org_id, "org", "manage_budget"
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.exception(f"Error checking permission in set_org_budget: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permission check failed"
        )
    
    # Get organization
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    org = result.scalar_one_or_none()
    
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    
    old_budget = org.max_budget
    org.max_budget = Decimal(str(data.max_budget))
    
    await db.commit()
    
    # Log action (non-critical - continue even if audit logging fails)
    try:
        from deltallm.rbac.audit import AuditLogger
        audit = AuditLogger(db, current_user.id)
        await audit.log(
            action="budget:set_org_limit",
            resource_type="organization",
            resource_id=str(org_id),
            org_id=org_id,
            changes={
                "old_max_budget": str(old_budget) if old_budget else None,
                "new_max_budget": str(org.max_budget),
            },
        )
    except Exception as e:
        logger.warning(f"Failed to log budget change audit: {e}")
    
    return format_budget_status(
        entity_type="organization",
        entity_id=str(org.id),
        entity_name=org.name,
        max_budget=org.max_budget,
        current_spend=org.spend,
    )


@router.get("/budget/team/{team_id}", response_model=BudgetStatusResponse)
async def get_team_budget(
    team_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
):
    """Get budget status for a team.
    
    Requires team:view_budget permission.
    """
    # Get team
    result = await db.execute(
        select(Team).where(Team.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )
    
    # Check permission
    try:
        from deltallm.rbac.manager import RBACManager
        rbac = RBACManager(db)
        await rbac.require_permission(
            current_user.id, team.org_id, "team", "view_budget"
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.exception(f"Error checking permission in get_team_budget: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permission check failed"
        )
    
    return format_budget_status(
        entity_type="team",
        entity_id=str(team.id),
        entity_name=team.name,
        max_budget=team.max_budget,
        current_spend=team.spend,
    )


@router.post("/budget/team/{team_id}/set", response_model=BudgetStatusResponse)
async def set_team_budget(
    team_id: UUID,
    data: SetBudgetRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
):
    """Set budget limit for a team.
    
    Requires team:manage_budget permission.
    """
    # Get team
    result = await db.execute(
        select(Team).where(Team.id == team_id)
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team not found",
        )
    
    # Check permission
    try:
        from deltallm.rbac.manager import RBACManager
        rbac = RBACManager(db)
        await rbac.require_permission(
            current_user.id, team.org_id, "team", "manage_budget"
        )
    except PermissionDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    except Exception as e:
        logger.exception(f"Error checking permission in set_team_budget: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Permission check failed"
        )
    
    old_budget = team.max_budget
    team.max_budget = Decimal(str(data.max_budget))
    
    await db.commit()
    
    # Log action (non-critical - continue even if audit logging fails)
    try:
        from deltallm.rbac.audit import AuditLogger
        audit = AuditLogger(db, current_user.id)
        await audit.log(
            action="budget:set_team_limit",
            resource_type="team",
            resource_id=str(team_id),
            org_id=team.org_id,
            changes={
                "old_max_budget": str(old_budget) if old_budget else None,
                "new_max_budget": str(team.max_budget),
            },
        )
    except Exception as e:
        logger.warning(f"Failed to log team budget change audit: {e}")
    
    return format_budget_status(
        entity_type="team",
        entity_id=str(team.id),
        entity_name=team.name,
        max_budget=team.max_budget,
        current_spend=team.spend,
    )


@router.get("/budget/summary", response_model=SpendSummaryResponse)
async def get_spend_summary(
    org_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    days: int = Query(default=30, ge=1, le=365),
    db: Annotated[AsyncSession, Depends(get_db_session)] = None,
    current_user: Annotated[User, Depends(get_current_user_optional)] = None,
):
    """Get spend summary over a time period.
    
    Requires appropriate permissions if org_id or team_id is specified.
    """
    try:
        # Calculate date range
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        
        # Build base query
        query = select(SpendLog).where(SpendLog.created_at >= start_time)
        
        if org_id:
            query = query.where(SpendLog.org_id == org_id)
        if team_id:
            query = query.where(SpendLog.team_id == team_id)
        
        # Get totals
        from sqlalchemy import case
        stats_query = select(
            func.coalesce(func.sum(SpendLog.spend), 0).label("total_spend"),
            func.count().label("total_requests"),
            func.coalesce(func.sum(SpendLog.total_tokens), 0).label("total_tokens"),
            func.sum(case((SpendLog.status == "success", 1), else_=0)).label("successful"),
            func.sum(case((SpendLog.status != "success", 1), else_=0)).label("failed"),
            func.avg(SpendLog.latency_ms).label("avg_latency"),
        ).select_from(query.subquery())
        
        result = await db.execute(stats_query)
        stats = result.one()
        
        # Get top models
        model_query = select(
            SpendLog.model,
            func.count().label("count"),
            func.sum(SpendLog.spend).label("spend"),
        ).select_from(query.subquery())
        model_query = model_query.group_by(SpendLog.model).order_by(func.sum(SpendLog.spend).desc()).limit(5)
        
        model_result = await db.execute(model_query)
        top_models = [
            {"model": row.model, "requests": row.count, "spend": float(row.spend)}
            for row in model_result
        ]
        
        # Get daily breakdown
        daily_query = select(
            func.date(SpendLog.created_at).label("date"),
            func.sum(SpendLog.spend).label("spend"),
            func.count().label("requests"),
        ).select_from(query.subquery())
        daily_query = daily_query.group_by(func.date(SpendLog.created_at)).order_by("date")
        
        daily_result = await db.execute(daily_query)
        daily_breakdown = [
            {"date": str(row.date), "spend": float(row.spend), "requests": row.requests}
            for row in daily_result
        ]
        
        return SpendSummaryResponse(
            total_spend=float(stats.total_spend),
            total_requests=stats.total_requests,
            total_tokens=int(stats.total_tokens),
            successful_requests=stats.successful or 0,
            failed_requests=stats.failed or 0,
            avg_latency_ms=float(stats.avg_latency) if stats.avg_latency else None,
            top_models=top_models,
            daily_breakdown=daily_breakdown,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error getting spend summary: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get spend summary: {str(e)}"
        )
