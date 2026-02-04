"""Provider configuration management API routes.

This module provides REST API endpoints for managing LLM provider configurations,
including CRUD operations, health checks, and connectivity testing.
"""

import asyncio
import logging
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from deltallm.db.models import ModelDeployment, Organization, ProviderConfig, User
from deltallm.db.session import get_db_session
from deltallm.proxy.dependencies import check_org_member, get_user_org_ids, require_org_admin, require_user
from deltallm.proxy.schemas import (
    ErrorResponse,
    PaginationParams,
    ProviderConfigCreate,
    ProviderConfigListResponse,
    ProviderConfigResponse,
    ProviderConfigUpdate,
    ProviderHealthResponse,
    ProviderTestResponse,
)
from deltallm.utils.encryption import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/providers", tags=["Providers"])


# ========== Provider CRUD ==========


@router.post(
    "",
    response_model=ProviderConfigResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        409: {"model": ErrorResponse, "description": "Provider name already exists"},
    },
)
async def create_provider(
    data: ProviderConfigCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ProviderConfigResponse:
    """Create a new provider configuration.

    Only superusers can create global providers (org_id=null).
    Org admins can create org-scoped providers.
    """
    # Check permissions
    if data.org_id is None:
        # Global provider - requires superuser
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can create global providers",
            )
    else:
        # Org-scoped provider - verify org exists and user has access
        org = await db.get(Organization, data.org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization not found: {data.org_id}",
            )
        # Check org admin permission
        await require_org_admin(current_user, data.org_id, db)

    # Check for duplicate name (within same scope)
    result = await db.execute(
        select(ProviderConfig).where(
            ProviderConfig.name == data.name,
            ProviderConfig.org_id == data.org_id,
        )
    )
    if result.scalar_one_or_none():
        scope = f"organization {data.org_id}" if data.org_id else "global"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Provider with name '{data.name}' already exists in {scope} scope",
        )

    # Encrypt API key if provided
    api_key_encrypted = None
    if data.api_key:
        api_key_encrypted = encrypt_api_key(data.api_key)

    # Create provider config
    provider = ProviderConfig(
        name=data.name,
        provider_type=data.provider_type,
        api_key_encrypted=api_key_encrypted,
        api_base=data.api_base,
        org_id=data.org_id,
        is_active=data.is_active,
        tpm_limit=data.tpm_limit,
        rpm_limit=data.rpm_limit,
        settings=data.settings or {},
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)

    logger.info(
        f"Created provider config: {provider.name} (type={provider.provider_type}, "
        f"org_id={provider.org_id})"
    )

    return ProviderConfigResponse.model_validate(provider)


@router.get(
    "",
    response_model=ProviderConfigListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def list_providers(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[User, Depends(require_user)],
    org_id: UUID | None = Query(default=None, description="Filter by organization"),
    provider_type: str | None = Query(default=None, description="Filter by provider type"),
    is_active: bool | None = Query(default=None, description="Filter by active status"),
) -> ProviderConfigListResponse:
    """List provider configurations.

    Superusers can see all providers.
    Regular users see global providers + their org's providers.
    """
    # Build query
    query = select(ProviderConfig)

    # Filter by organization
    if org_id is not None:
        query = query.where(ProviderConfig.org_id == org_id)
    elif not current_user.is_superuser:
        # Non-superusers see global providers + their org's providers
        user_org_ids = await get_user_org_ids(current_user, db)
        if user_org_ids:
            from sqlalchemy import or_
            query = query.where(
                or_(
                    ProviderConfig.org_id.is_(None),
                    ProviderConfig.org_id.in_(user_org_ids),
                )
            )
        else:
            query = query.where(ProviderConfig.org_id.is_(None))

    # Filter by provider type
    if provider_type:
        query = query.where(ProviderConfig.provider_type == provider_type)

    # Filter by active status
    if is_active is not None:
        query = query.where(ProviderConfig.is_active == is_active)

    # Execute query
    result = await db.execute(query)
    providers = result.scalars().all()
    total = len(providers)

    # Apply pagination
    offset = (pagination.page - 1) * pagination.page_size
    paginated_providers = providers[offset : offset + pagination.page_size]

    pages = (total + pagination.page_size - 1) // pagination.page_size

    return ProviderConfigListResponse(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
        items=[ProviderConfigResponse.model_validate(p) for p in paginated_providers],
    )


@router.get(
    "/{provider_id}",
    response_model=ProviderConfigResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Provider not found"},
    },
)
async def get_provider(
    provider_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ProviderConfigResponse:
    """Get provider configuration by ID."""
    provider = await db.get(ProviderConfig, provider_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    # Check access
    if provider.org_id and not current_user.is_superuser:
        # Verify user is member of the org
        if not await check_org_member(current_user, provider.org_id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this provider",
            )

    return ProviderConfigResponse.model_validate(provider)


@router.patch(
    "/{provider_id}",
    response_model=ProviderConfigResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Provider not found"},
        409: {"model": ErrorResponse, "description": "Name conflict"},
    },
)
async def update_provider(
    provider_id: UUID,
    data: ProviderConfigUpdate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ProviderConfigResponse:
    """Update a provider configuration.

    Only superusers can update global providers.
    Org admins can update org-scoped providers.
    """
    provider = await db.get(ProviderConfig, provider_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    # Check permissions
    if provider.org_id is None:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can update global providers",
            )
    else:
        # Org-scoped provider - require org admin
        await require_org_admin(current_user, provider.org_id, db)

    # Check for name conflict if updating name
    if data.name and data.name != provider.name:
        result = await db.execute(
            select(ProviderConfig).where(
                ProviderConfig.name == data.name,
                ProviderConfig.org_id == provider.org_id,
                ProviderConfig.id != provider_id,
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Provider with name '{data.name}' already exists",
            )

    # Update fields
    if data.name is not None:
        provider.name = data.name
    if data.api_key is not None:
        provider.api_key_encrypted = encrypt_api_key(data.api_key)
    if data.api_base is not None:
        provider.api_base = data.api_base
    if data.is_active is not None:
        provider.is_active = data.is_active
    if data.tpm_limit is not None:
        provider.tpm_limit = data.tpm_limit
    if data.rpm_limit is not None:
        provider.rpm_limit = data.rpm_limit
    if data.settings is not None:
        provider.settings = data.settings

    await db.commit()
    await db.refresh(provider)

    logger.info(f"Updated provider config: {provider.name} (id={provider.id})")

    return ProviderConfigResponse.model_validate(provider)


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Provider not found"},
        409: {"model": ErrorResponse, "description": "Provider has active deployments"},
    },
)
async def delete_provider(
    provider_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
    force: bool = Query(default=False, description="Force delete even with deployments"),
) -> None:
    """Delete a provider configuration.

    Only superusers can delete global providers.
    Will fail if provider has active deployments unless force=true.
    """
    provider = await db.get(
        ProviderConfig, provider_id, options=[selectinload(ProviderConfig.deployments)]
    )

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    # Check permissions
    if provider.org_id is None:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can delete global providers",
            )
    else:
        # Org-scoped provider - require org admin
        await require_org_admin(current_user, provider.org_id, db)

    # Check for active deployments
    if provider.deployments and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Provider has {len(provider.deployments)} deployment(s). "
            "Use force=true to delete anyway (will cascade delete deployments).",
        )

    await db.delete(provider)
    await db.commit()

    logger.info(f"Deleted provider config: {provider.name} (id={provider_id})")


# ========== Provider Health & Testing ==========


@router.post(
    "/{provider_id}/test",
    response_model=ProviderTestResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Provider not found"},
    },
)
async def test_provider_connectivity(
    provider_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ProviderTestResponse:
    """Test provider connectivity by making a simple API call.

    Attempts to list models or make a minimal request to verify credentials.
    """
    provider = await db.get(ProviderConfig, provider_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    # Decrypt API key
    api_key = None
    if provider.api_key_encrypted:
        try:
            api_key = decrypt_api_key(provider.api_key_encrypted)
        except Exception as e:
            return ProviderTestResponse(
                success=False,
                latency_ms=None,
                error_message=f"Failed to decrypt API key: {e}",
            )

    # Test connectivity based on provider type
    start_time = time.time()
    try:
        # Import provider and test
        from deltallm.providers.registry import ProviderRegistry

        # Get provider class
        provider_class = ProviderRegistry.get_by_type(provider.provider_type)

        if not provider_class:
            return ProviderTestResponse(
                success=False,
                latency_ms=None,
                error_message=f"Unknown provider type: {provider.provider_type}",
            )

        # Create provider instance
        provider_kwargs = {"api_key": api_key}
        if provider.api_base:
            provider_kwargs["api_base"] = provider.api_base

        provider_instance = provider_class(**provider_kwargs)

        # Try to list models or make a health check
        models = None
        if hasattr(provider_instance, "list_models"):
            models = await provider_instance.list_models()
        elif hasattr(provider_instance, "health_check"):
            await provider_instance.health_check()

        latency_ms = (time.time() - start_time) * 1000

        return ProviderTestResponse(
            success=True,
            latency_ms=latency_ms,
            model_list=models,
        )

    except Exception as e:
        latency_ms = (time.time() - start_time) * 1000
        logger.warning(f"Provider test failed for {provider.name}: {e}")

        return ProviderTestResponse(
            success=False,
            latency_ms=latency_ms,
            error_message=str(e),
        )


@router.get(
    "/{provider_id}/health",
    response_model=ProviderHealthResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        404: {"model": ErrorResponse, "description": "Provider not found"},
    },
)
async def get_provider_health(
    provider_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ProviderHealthResponse:
    """Get provider health status.

    Returns cached health status if available, otherwise performs a health check.
    """
    provider = await db.get(ProviderConfig, provider_id)

    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    # For now, perform a quick connectivity test
    # In production, this would use cached health metrics
    test_result = await test_provider_connectivity(provider_id, db, current_user)

    return ProviderHealthResponse(
        provider_id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        is_active=provider.is_active,
        is_healthy=test_result.success,
        latency_ms=test_result.latency_ms,
        last_check=provider.updated_at,
        error_message=test_result.error_message,
    )


# ========== Team-Provider Access Management ==========


@router.post(
    "/{provider_id}/teams/{team_id}",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Provider or team not found"},
        409: {"model": ErrorResponse, "description": "Access already granted"},
    },
)
async def grant_team_provider_access(
    provider_id: UUID,
    team_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> dict:
    """Grant a team access to a provider.

    Only org admins can grant team access to providers within their org.
    The team and provider must belong to the same organization.
    """
    from deltallm.db.models import Team, TeamProviderAccess

    # Get provider
    provider = await db.get(ProviderConfig, provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    # Get team
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: {team_id}",
        )

    # Verify team and provider are in the same org (or provider is global)
    if provider.org_id and provider.org_id != team.org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provider and team must belong to the same organization",
        )

    # Check permission - must be org admin of the team's org
    await require_org_admin(current_user, team.org_id, db)

    # Check if access already granted
    result = await db.execute(
        select(TeamProviderAccess).where(
            TeamProviderAccess.team_id == team_id,
            TeamProviderAccess.provider_config_id == provider_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Team already has access to this provider",
        )

    # Grant access
    access = TeamProviderAccess(
        team_id=team_id,
        provider_config_id=provider_id,
        granted_by=current_user.id,
    )
    db.add(access)
    await db.commit()

    logger.info(
        f"Granted team {team.name} access to provider {provider.name} "
        f"(granted_by={current_user.email})"
    )

    return {"message": f"Access granted to team {team.name}"}


@router.delete(
    "/{provider_id}/teams/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Access not found"},
    },
)
async def revoke_team_provider_access(
    provider_id: UUID,
    team_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> None:
    """Revoke a team's access to a provider.

    Only org admins can revoke team access.
    """
    from deltallm.db.models import Team, TeamProviderAccess

    # Get team (needed for org admin check)
    team = await db.get(Team, team_id)
    if not team:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Team not found: {team_id}",
        )

    # Check permission - must be org admin of the team's org
    await require_org_admin(current_user, team.org_id, db)

    # Find and delete access
    result = await db.execute(
        select(TeamProviderAccess).where(
            TeamProviderAccess.team_id == team_id,
            TeamProviderAccess.provider_config_id == provider_id,
        )
    )
    access = result.scalar_one_or_none()

    if not access:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team does not have access to this provider",
        )

    await db.delete(access)
    await db.commit()

    logger.info(
        f"Revoked team {team.name} access to provider {provider_id} "
        f"(revoked_by={current_user.email})"
    )


@router.get(
    "/{provider_id}/teams",
    response_model=dict,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Provider not found"},
    },
)
async def list_provider_teams(
    provider_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> dict:
    """List all teams with access to a provider.

    Org admins can see which teams have access to org-scoped providers.
    Superusers can see all team access.
    """
    from deltallm.db.models import Team, TeamProviderAccess

    # Get provider
    provider = await db.get(ProviderConfig, provider_id)
    if not provider:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Provider not found: {provider_id}",
        )

    # Check permission
    if provider.org_id and not current_user.is_superuser:
        await require_org_admin(current_user, provider.org_id, db)

    # Query team access with team details
    result = await db.execute(
        select(TeamProviderAccess, Team)
        .join(Team, TeamProviderAccess.team_id == Team.id)
        .where(TeamProviderAccess.provider_config_id == provider_id)
    )
    rows = result.all()

    items = []
    for access, team in rows:
        items.append({
            "id": str(access.id),
            "team_id": str(access.team_id),
            "provider_config_id": str(access.provider_config_id),
            "granted_by": str(access.granted_by) if access.granted_by else None,
            "granted_at": access.granted_at.isoformat(),
            "team_name": team.name,
            "team_slug": team.slug,
        })

    return {"items": items, "total": len(items)}
