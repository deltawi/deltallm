"""Organization management API routes.

This module provides REST API endpoints for organization CRUD operations,
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

from deltallm.db.models import OrgMember, Organization, Team, User
from deltallm.db.session import get_db_session
from deltallm.proxy.dependencies import require_user, get_current_user_optional
from deltallm.proxy.schemas import (
    ErrorResponse,
    OrgMemberCreate,
    OrgMemberListResponse,
    OrgMemberResponse,
    OrgMemberUpdate,
    OrganizationCreate,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationUpdate,
    PaginationParams,
    TeamListResponse,
    TeamResponse,
)
from deltallm.rbac.exceptions import PermissionDeniedError
from deltallm.rbac.manager import RBACManager
from deltallm.rbac.permissions import PermissionChecker

router = APIRouter(prefix="/org", tags=["Organizations"])


# ========== Organization CRUD ==========

@router.post(
    "/create",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        409: {"model": ErrorResponse, "description": "Slug already exists"},
    },
)
async def create_organization(
    data: OrganizationCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> OrganizationResponse:
    """Create a new organization.
    
    The creating user automatically becomes the organization owner.
    """
    # Check if slug already exists
    result = await db.execute(
        select(Organization).where(Organization.slug == data.slug)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization with slug '{data.slug}' already exists",
        )
    
    # Create organization
    org = Organization(
        name=data.name,
        slug=data.slug,
        description=data.description,
        max_budget=data.max_budget,
    )
    db.add(org)
    await db.flush()
    
    # Add current user as owner
    owner_membership = OrgMember(
        user_id=current_user.id,
        org_id=org.id,
        role="owner",
    )
    db.add(owner_membership)
    
    await db.commit()
    await db.refresh(org)
    
    return OrganizationResponse.model_validate(org)


@router.get(
    "/list",
    response_model=OrganizationListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def list_organizations(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[User, Depends(require_user)],
) -> OrganizationListResponse:
    """List organizations the current user is a member of.
    
    Superusers can see all organizations.
    """
    # Superusers see all organizations
    if current_user.is_superuser:
        result = await db.execute(select(Organization))
        orgs = result.scalars().all()
    else:
        # Regular users see only their organizations
        result = await db.execute(
            select(Organization)
            .join(OrgMember)
            .where(OrgMember.user_id == current_user.id)
        )
        orgs = result.scalars().all()
    
    total = len(orgs)
    
    # Apply pagination
    offset = (pagination.page - 1) * pagination.page_size
    paginated_orgs = orgs[offset:offset + pagination.page_size]
    
    pages = (total + pagination.page_size - 1) // pagination.page_size
    
    return OrganizationListResponse(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
        items=[OrganizationResponse.model_validate(o) for o in paginated_orgs],
    )


@router.get(
    "/{org_id}",
    response_model=OrganizationResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Not a member"},
        404: {"model": ErrorResponse, "description": "Organization not found"},
    },
)
async def get_organization(
    org_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> OrganizationResponse:
    """Get organization details by ID.
    
    User must be a member of the organization or a superuser.
    """
    org = await db.get(Organization, org_id)
    
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {org_id}",
        )
    
    # Check if user is member or superuser
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
                detail="You are not a member of this organization",
            )
    
    return OrganizationResponse.model_validate(org)


@router.post(
    "/{org_id}/update",
    response_model=OrganizationResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Organization not found"},
    },
)
async def update_organization(
    org_id: UUID,
    data: OrganizationUpdate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> OrganizationResponse:
    """Update organization details.
    
    Requires org:update permission.
    """
    org = await db.get(Organization, org_id)
    
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {org_id}",
        )
    
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, org_id, "org", "update"
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to update this organization",
        )
    
    # Update fields
    if data.name is not None:
        org.name = data.name
    if data.description is not None:
        org.description = data.description
    if data.max_budget is not None:
        org.max_budget = data.max_budget
    if data.settings is not None:
        org.settings = data.settings
    
    await db.commit()
    await db.refresh(org)
    
    return OrganizationResponse.model_validate(org)


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Organization not found"},
    },
)
async def delete_organization(
    org_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> None:
    """Delete an organization.
    
    Requires org:delete permission (typically only owners).
    This is a destructive operation that cannot be undone.
    """
    org = await db.get(Organization, org_id)
    
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {org_id}",
        )
    
    # Check permission (only owners or superusers can delete)
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, org_id, "org", "delete"
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization owners can delete the organization",
        )
    
    await db.delete(org)
    await db.commit()


# ========== Organization Members ==========

@router.post(
    "/{org_id}/member/add",
    response_model=OrgMemberResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Organization or user not found"},
        409: {"model": ErrorResponse, "description": "User already a member"},
    },
)
async def add_organization_member(
    org_id: UUID,
    data: OrgMemberCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> OrgMemberResponse:
    """Add a member to an organization.
    
    Requires org:manage_members permission.
    """
    # Verify organization exists
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {org_id}",
        )
    
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, org_id, "org", "manage_members"
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage organization members",
        )
    
    # Find user by email
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User not found with email: {data.email}",
        )
    
    # Check if already a member
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.user_id == user.id,
            OrgMember.org_id == org_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User {data.email} is already a member of this organization",
        )
    
    # Create membership
    membership = OrgMember(
        user_id=user.id,
        org_id=org_id,
        role=data.role,
    )
    db.add(membership)
    await db.flush()
    
    # Load user relationship
    await db.refresh(membership, ["user"])
    
    return OrgMemberResponse.model_validate(membership)


@router.get(
    "/{org_id}/members",
    response_model=OrgMemberListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Not a member"},
        404: {"model": ErrorResponse, "description": "Organization not found"},
    },
)
async def list_organization_members(
    org_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[User, Depends(require_user)],
) -> OrgMemberListResponse:
    """List all members of an organization.
    
    User must be a member of the organization.
    """
    try:
        # Verify organization exists
        org = await db.get(Organization, org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization not found: {org_id}",
            )
        
        # Check if user is member or superuser
        if not current_user.is_superuser:
            is_member = await db.execute(
                select(OrgMember).where(
                    OrgMember.user_id == current_user.id,
                    OrgMember.org_id == org_id,
                )
            )
            if not is_member.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You are not a member of this organization",
                )
        
        # Get members with user info
        result = await db.execute(
            select(OrgMember)
            .where(OrgMember.org_id == org_id)
            .options(selectinload(OrgMember.user))
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
                items.append(OrgMemberResponse.model_validate(m))
            except ValidationError as ve:
                logger.error(f"Failed to validate member {m.id}: {ve}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to serialize member data: {ve}"
                )
        
        return OrgMemberListResponse(
            total=total,
            page=pagination.page,
            page_size=pagination.page_size,
            pages=pages,
            items=items,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error listing organization members: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list organization members: {str(e)}"
        )


@router.post(
    "/{org_id}/member/{user_id}/update",
    response_model=OrgMemberResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Member not found"},
    },
)
async def update_organization_member(
    org_id: UUID,
    user_id: UUID,
    data: OrgMemberUpdate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> OrgMemberResponse:
    """Update a member's role in the organization.
    
    Requires org:manage_members permission.
    """
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, org_id, "org", "manage_members"
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage organization members",
        )
    
    result = await db.execute(
        select(OrgMember)
        .where(
            OrgMember.org_id == org_id,
            OrgMember.user_id == user_id,
        )
        .options(selectinload(OrgMember.user))
    )
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this organization",
        )
    
    member.role = data.role
    await db.commit()
    await db.refresh(member)
    
    return OrgMemberResponse.model_validate(member)


@router.delete(
    "/{org_id}/member/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Member not found"},
    },
)
async def remove_organization_member(
    org_id: UUID,
    user_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> None:
    """Remove a member from the organization.
    
    Requires org:manage_members permission.
    Owners cannot be removed by other owners - must transfer ownership first.
    """
    # Check permission
    rbac = RBACManager(db)
    has_permission = await rbac.check_permission(
        current_user.id, org_id, "org", "manage_members"
    )
    
    if not has_permission and not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to manage organization members",
        )
    
    result = await db.execute(
        select(OrgMember).where(
            OrgMember.org_id == org_id,
            OrgMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this organization",
        )
    
    await db.delete(member)
    await db.commit()


# ========== Organization Teams ==========

@router.get(
    "/{org_id}/teams",
    response_model=TeamListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Not a member"},
        404: {"model": ErrorResponse, "description": "Organization not found"},
    },
)
async def list_organization_teams(
    org_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[User, Depends(require_user)],
) -> TeamListResponse:
    """List all teams within an organization.
    
    User must be a member of the organization.
    """
    # Verify organization exists
    org = await db.get(Organization, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Organization not found: {org_id}",
        )
    
    # Check if user is member or superuser
    if not current_user.is_superuser:
        is_member = await db.execute(
            select(OrgMember).where(
                OrgMember.user_id == current_user.id,
                OrgMember.org_id == org_id,
            )
        )
        if not is_member.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this organization",
            )
    
    # Get teams
    result = await db.execute(
        select(Team).where(Team.org_id == org_id)
    )
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
