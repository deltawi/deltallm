"""Team management API routes.

This module provides REST API endpoints for team CRUD operations,
member management, and related functionality.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from deltallm.db.models import OrgMember, Organization, Team, TeamMember, User
from deltallm.db.session import get_db_session
from deltallm.proxy.dependencies import require_user
from deltallm.proxy.schemas import (
    ErrorResponse,
    PaginationParams,
    TeamCreate,
    TeamListResponse,
    TeamMemberCreate,
    TeamMemberListResponse,
    TeamMemberResponse,
    TeamMemberUpdate,
    TeamResponse,
    TeamUpdate,
    UserResponse,
)
from deltallm.rbac.manager import RBACManager

router = APIRouter(prefix="/team", tags=["Teams"])


# ========== Team CRUD ==========

@router.post(
    "/create",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Organization not found"},
        409: {"model": ErrorResponse, "description": "Slug already exists in organization"},
    },
)
async def create_team(
    data: TeamCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> TeamResponse:
    """Create a new team within an organization.
    
    Requires team:create permission in the organization.
    """
    # Verify organization exists
    org = await db.get(Organization, data.org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {data.org_id}",
        )
    
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, data.org_id, "team", "create"
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to create teams in this organization",
        )
    
    # Check if slug already exists in this org
    result = await db.execute(
        select(Team).where(
            Team.org_id == data.org_id,
            Team.slug == data.slug,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Team with slug '{data.slug}' already exists in this organization",
        )
    
    # Create team
    team = Team(
        org_id=data.org_id,
        name=data.name,
        slug=data.slug,
        description=data.description,
        max_budget=data.max_budget,
    )
    db.add(team)
    await db.flush()
    
    # Add current user as team admin
    admin_membership = TeamMember(
        user_id=current_user.id,
        team_id=team.id,
        role="admin",
    )
    db.add(admin_membership)
    
    await db.commit()
    await db.refresh(team)
    
    return TeamResponse.model_validate(team)


@router.get(
    "/list",
    response_model=TeamListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def list_teams(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[User, Depends(require_user)],
    org_id: UUID | None = Query(default=None, description="Filter by organization ID"),
) -> TeamListResponse:
    """List teams the current user has access to.
    
    Superusers can see all teams. Regular users see teams they are members of
    or teams within organizations they belong to.
    
    Optionally filter by organization ID.
    """
    from sqlalchemy.orm import selectinload
    
    # Build query based on user permissions
    if current_user.is_superuser:
        query = select(Team).options(selectinload(Team.organization))
        if org_id:
            query = query.where(Team.org_id == org_id)
    else:
        # Get teams where user is a member via TeamMember
        query = (
            select(Team)
            .join(TeamMember, Team.id == TeamMember.team_id)
            .where(TeamMember.user_id == current_user.id)
            .options(selectinload(Team.organization))
        )
        if org_id:
            query = query.where(Team.org_id == org_id)
    
    result = await db.execute(query)
    teams = result.scalars().all()
    total = len(teams)
    
    # Apply pagination
    offset = (pagination.page - 1) * pagination.page_size
    paginated_teams = teams[offset:offset + pagination.page_size]
    
    pages = (total + pagination.page_size - 1) // pagination.page_size
    
    return TeamListResponse(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
        items=[TeamResponse.model_validate(t) for t in paginated_teams],
    )


@router.get(
    "/{team_id}",
    response_model=TeamResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Not a member"},
        404: {"model": ErrorResponse, "description": "Team not found"},
    },
)
async def get_team(
    team_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> TeamResponse:
    """Get team details by ID.
    
    User must be a member of the team or organization.
    """
    from sqlalchemy.orm import selectinload
    
    # Load team with organization relationship
    result = await db.execute(
        select(Team)
        .where(Team.id == team_id)
        .options(selectinload(Team.organization))
    )
    team = result.scalar_one_or_none()
    
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: {team_id}",
        )
    
    # Check if user is team member, org member, or superuser
    if not current_user.is_superuser:
        is_team_member = await db.execute(
            select(TeamMember).where(
                TeamMember.user_id == current_user.id,
                TeamMember.team_id == team_id,
            )
        )
        is_org_member = await db.execute(
            select(OrgMember).where(
                OrgMember.user_id == current_user.id,
                OrgMember.org_id == team.org_id,
            )
        )
        
        if not is_team_member.scalar_one_or_none() and not is_org_member.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this team",
            )
    
    return TeamResponse.model_validate(team)


@router.post(
    "/{team_id}/update",
    response_model=TeamResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Team not found"},
    },
)
async def update_team(
    team_id: UUID,
    data: TeamUpdate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> TeamResponse:
    """Update team details.
    
    Requires team:update permission.
    """
    team = await db.get(Team, team_id)
    
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: {team_id}",
        )
    
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, team.org_id, "team", "update", team_id=team_id
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this team",
        )
    
    # Update fields
    if data.name is not None:
        team.name = data.name
    if data.description is not None:
        team.description = data.description
    if data.max_budget is not None:
        team.max_budget = data.max_budget
    if data.settings is not None:
        team.settings = data.settings
    
    await db.commit()
    await db.refresh(team)
    
    return TeamResponse.model_validate(team)


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Team not found"},
    },
)
async def delete_team(
    team_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> None:
    """Delete a team.
    
    Requires team:delete permission.
    This is a destructive operation that cannot be undone.
    """
    team = await db.get(Team, team_id)
    
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: {team_id}",
        )
    
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, team.org_id, "team", "delete", team_id=team_id
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to delete this team",
        )
    
    await db.delete(team)
    await db.commit()


# ========== Team Members ==========

@router.post(
    "/{team_id}/member/add",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "User not in organization"},
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Team or user not found"},
        409: {"model": ErrorResponse, "description": "User already a member"},
    },
)
async def add_team_member(
    team_id: UUID,
    data: TeamMemberCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> TeamMemberResponse:
    """Add a member to a team.
    
    Requires team:manage_members permission.
    The user must already be a member of the parent organization.
    """
    # Verify team exists and get org_id
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: {team_id}",
        )
    
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, team.org_id, "team", "manage_members", team_id=team_id
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage team members",
        )
    
    # Verify user exists
    user = await db.get(User, data.user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found: {data.user_id}",
        )
    
    # Verify user is in the organization
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.user_id == data.user_id,
            OrgMember.org_id == team.org_id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User must be a member of the organization before joining a team",
        )
    
    # Check if already a member
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.user_id == data.user_id,
            TeamMember.team_id == team_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User is already a member of this team",
        )
    
    # Create membership
    membership = TeamMember(
        user_id=data.user_id,
        team_id=team_id,
        role=data.role,
    )
    db.add(membership)
    await db.flush()
    
    # Load user relationship
    await db.refresh(membership, ["user"])
    
    return TeamMemberResponse.model_validate(membership)


@router.get(
    "/{team_id}/members",
    response_model=TeamMemberListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Not a member"},
        404: {"model": ErrorResponse, "description": "Team not found"},
    },
)
async def list_team_members(
    team_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[User, Depends(require_user)],
) -> TeamMemberListResponse:
    """List all members of a team.
    
    User must be a member of the team or organization.
    """
    try:
        # Verify team exists
        team = await db.get(Team, team_id)
        if not team:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Team not found: {team_id}",
            )
        
        # Check if user is team member, org member, or superuser
        if not current_user.is_superuser:
            is_team_member = await db.execute(
                select(TeamMember).where(
                    TeamMember.user_id == current_user.id,
                    TeamMember.team_id == team_id,
                )
            )
            is_org_member = await db.execute(
                select(OrgMember).where(
                    OrgMember.user_id == current_user.id,
                    OrgMember.org_id == team.org_id,
                )
            )
            
            if not is_team_member.scalar_one_or_none() and not is_org_member.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not a member of this team",
                )
        
        # Get members with user info
        result = await db.execute(
            select(TeamMember)
            .where(TeamMember.team_id == team_id)
            .options(selectinload(TeamMember.user))
        )
        members = result.scalars().all()
        total = len(members)
        
        # Apply pagination
        offset = (pagination.page - 1) * pagination.page_size
        paginated_members = members[offset:offset + pagination.page_size]
        
        pages = (total + pagination.page_size - 1) // pagination.page_size
        
        # Validate and serialize members
        items = []
        for m in paginated_members:
            try:
                items.append(TeamMemberResponse.model_validate(m))
            except ValidationError as ve:
                logger.error(f"Failed to validate team member {m.id}: {ve}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to serialize team member data: {ve}"
                )
        
        return TeamMemberListResponse(
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            pages=pages,
            items=items,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error listing team members: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list team members: {str(e)}"
        )


@router.post(
    "/{team_id}/member/{user_id}/update",
    response_model=TeamMemberResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Member not found"},
    },
)
async def update_team_member(
    team_id: UUID,
    user_id: UUID,
    data: TeamMemberUpdate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> TeamMemberResponse:
    """Update a member's role in the team.
    
    Requires team:manage_members permission.
    """
    # Get team for org_id
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: {team_id}",
        )
    
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, team.org_id, "team", "manage_members", team_id=team_id
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage team members",
        )
    
    result = await db.execute(
        select(TeamMember)
        .where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
        .options(selectinload(TeamMember.user))
    )
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )
    
    member.role = data.role
    await db.commit()
    await db.refresh(member)
    
    return TeamMemberResponse.model_validate(member)


@router.delete(
    "/{team_id}/member/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Member not found"},
    },
)
async def remove_team_member(
    team_id: UUID,
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> None:
    """Remove a member from the team.
    
    Requires team:manage_members permission.
    """
    # Get team for org_id
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: {team_id}",
        )
    
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, team.org_id, "team", "manage_members", team_id=team_id
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage team members",
        )
    
    result = await db.execute(
        select(TeamMember).where(
            TeamMember.team_id == team_id,
            TeamMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this team",
        )
    
    await db.delete(member)
    await db.commit()


# ========== User's Teams ==========

@router.get(
    "/user/{user_id}/teams",
    response_model=TeamListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
    },
)
async def list_user_teams(
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[User, Depends(require_user)],
    org_id: UUID | None = None,
) -> TeamListResponse:
    """List all teams a user belongs to.
    
    Users can only view their own teams unless they are superusers.
    Optionally filter by organization.
    """
    # Users can only view their own teams unless superuser
    if str(current_user.id) != str(user_id) and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own teams",
        )
    
    # Build query
    query = (
        select(Team)
        .join(TeamMember)
        .where(TeamMember.user_id == user_id)
    )
    
    if org_id:
        query = query.where(Team.org_id == org_id)
    
    result = await db.execute(query)
    teams = result.scalars().all()
    total = len(teams)
    
    # Apply pagination
    offset = (pagination.page - 1) * pagination.page_size
    paginated_teams = teams[offset:offset + pagination.page_size]
    
    pages = (total + pagination.page_size - 1) // pagination.page_size
    
    return TeamListResponse(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
        items=[TeamResponse.model_validate(t) for t in paginated_teams],
    )
