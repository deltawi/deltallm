"""Audit logging API routes.

This module provides REST API endpoints for querying audit logs.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import AuditLog, OrgMember, User
from deltallm.db.session import get_db_session
from deltallm.proxy.auth import get_current_active_superuser
from deltallm.proxy.dependencies import require_user
from deltallm.proxy.schemas import (
    ErrorResponse,
    PaginationParams,
)
from deltallm.rbac.manager import RBACManager

router = APIRouter(prefix="/audit", tags=["Audit Logs"])


@router.get(
    "/logs",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
    },
)
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
    pagination: Annotated[PaginationParams, Depends()],
    org_id: UUID | None = None,
    user_id: UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> dict:
    """List audit logs with filtering.
    
    Regular users can only view logs for organizations they are members of.
    Superusers can view all logs.
    
    Query Parameters:
        org_id: Filter by organization
        user_id: Filter by user who performed the action
        action: Filter by action type (e.g., "org_create", "member_add")
        resource_type: Filter by resource type (e.g., "organization", "team")
        resource_id: Filter by specific resource
        start_date: Filter logs after this date
        end_date: Filter logs before this date
    """
    # Build query
    query = select(AuditLog)
    
    # Apply filters
    if org_id:
        # Check if user can access this org's logs
        if not current_user.is_superuser:
            rbac = RBACManager(db)
            is_member = await db.execute(
                select(OrgMember).where(
                    OrgMember.user_id == current_user.id,
                    OrgMember.org_id == org_id,
                )
            )
            if not is_member.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You don't have permission to view logs for this organization",
                )
        query = query.where(AuditLog.org_id == org_id)
    elif not current_user.is_superuser:
        # Non-superusers can only see logs for orgs they belong to
        result = await db.execute(
            select(OrgMember.org_id).where(OrgMember.user_id == current_user.id)
        )
        org_ids = [row[0] for row in result.all()]
        if org_ids:
            query = query.where(AuditLog.org_id.in_(org_ids))
        else:
            # User has no orgs, return empty
            return {
                "total": 0,
                "page": pagination.page,
                "page_size": pagination.page_size,
                "pages": 0,
                "items": [],
            }
    
    if user_id:
        query = query.where(AuditLog.user_id == user_id)
    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if resource_id:
        query = query.where(AuditLog.resource_id == resource_id)
    if start_date:
        query = query.where(AuditLog.created_at >= start_date)
    if end_date:
        query = query.where(AuditLog.created_at <= end_date)
    
    # Order by created_at desc (newest first)
    query = query.order_by(AuditLog.created_at.desc())
    
    # Get total count
    result = await db.execute(query)
    logs = result.scalars().all()
    total = len(logs)
    
    # Apply pagination
    offset = (pagination.page - 1) * pagination.page_size
    paginated_logs = logs[offset:offset + pagination.page_size]
    
    pages = (total + pagination.page_size - 1) // pagination.page_size
    
    # Format response
    items = []
    for log in paginated_logs:
        items.append({
            "id": str(log.id),
            "org_id": str(log.org_id) if log.org_id else None,
            "user_id": str(log.user_id) if log.user_id else None,
            "action": log.action,
            "resource_type": log.resource_type,
            "resource_id": str(log.resource_id) if log.resource_id else None,
            "old_values": log.old_values,
            "new_values": log.new_values,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })
    
    return {
        "total": total,
        "page": pagination.page,
        "page_size": pagination.page_size,
        "pages": pages,
        "items": items,
    }


@router.get(
    "/logs/{log_id}",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Log not found"},
    },
)
async def get_audit_log(
    log_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> dict:
    """Get a specific audit log entry.
    
    Users can only view logs for organizations they are members of.
    """
    log = await db.get(AuditLog, log_id)
    
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit log not found: {log_id}",
        )
    
    # Check permissions
    if not current_user.is_superuser and log.org_id:
        is_member = await db.execute(
            select(OrgMember).where(
                OrgMember.user_id == current_user.id,
                OrgMember.org_id == log.org_id,
            )
        )
        if not is_member.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to view this log entry",
            )
    
    return {
        "id": str(log.id),
        "org_id": str(log.org_id) if log.org_id else None,
        "user_id": str(log.user_id) if log.user_id else None,
        "action": log.action,
        "resource_type": log.resource_type,
        "resource_id": str(log.resource_id) if log.resource_id else None,
        "old_values": log.old_values,
        "new_values": log.new_values,
        "ip_address": log.ip_address,
        "user_agent": log.user_agent,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


@router.get(
    "/actions",
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def list_audit_actions(
    current_user: Annotated[User, Depends(require_user)],
) -> list[str]:
    """List all available audit action types."""
    # Return list of common audit actions
    return [
        "org_create",
        "org_update",
        "org_delete",
        "team_create",
        "team_update",
        "team_delete",
        "org_member_add",
        "org_member_update",
        "org_member_remove",
        "team_member_add",
        "team_member_update",
        "team_member_remove",
        "role_assign",
        "permission_denied",
    ]
