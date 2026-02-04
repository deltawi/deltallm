"""Model deployment management API routes.

This module provides REST API endpoints for managing model deployments,
which map public model names to specific provider configurations.
"""

import logging
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi import Request

from deltallm.db.models import ModelDeployment, ModelPricing, Organization, ProviderConfig, User
from deltallm.utils.encryption import encrypt_api_key
from deltallm.utils.model_type_detector import suggest_model_type, get_all_model_types
from deltallm.db.session import get_db_session
from deltallm.pricing.manager import PricingManager
from deltallm.proxy.dependencies import check_org_member, get_user_org_ids, require_org_admin, require_user
from deltallm.proxy.schemas import (
    ErrorResponse,
    ModelDeploymentCreate,
    ModelDeploymentListResponse,
    ModelDeploymentResponse,
    ModelDeploymentUpdate,
    ModelDeploymentWithProvider,
    PaginationParams,
    ProviderConfigResponse,
)
from deltallm.proxy.schemas_pricing import PricingCreateRequest, PricingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/deployments", tags=["Deployments"])


def _deployment_to_response(
    deployment: ModelDeployment,
    include_provider: bool = False,
) -> ModelDeploymentResponse | ModelDeploymentWithProvider:
    """Convert deployment model to response schema."""
    # Determine provider type and name (deployment-level takes precedence for type)
    provider_name = deployment.provider_config.name if deployment.provider_config else None
    # Use deployment-level provider_type if set, otherwise fall back to provider config
    provider_type = deployment.provider_type or (deployment.provider_config.provider_type if deployment.provider_config else None)
    
    base_data = {
        "id": deployment.id,
        "created_at": deployment.created_at,
        "updated_at": deployment.updated_at,
        "model_name": deployment.model_name,
        "provider_model": deployment.provider_model,
        "provider_config_id": deployment.provider_config_id,
        "provider_type": provider_type,
        "model_type": deployment.model_type,
        "api_base": deployment.api_base,
        "org_id": deployment.org_id,
        "is_active": deployment.is_active,
        "priority": deployment.priority,
        "tpm_limit": deployment.tpm_limit,
        "rpm_limit": deployment.rpm_limit,
        "timeout": float(deployment.timeout) if deployment.timeout else None,
        "settings": deployment.settings,
        "provider_name": provider_name,
    }

    if include_provider and deployment.provider_config:
        return ModelDeploymentWithProvider(
            **base_data,
            provider=ProviderConfigResponse.model_validate(deployment.provider_config),
        )

    return ModelDeploymentResponse(**base_data)


# ========== Deployment CRUD ==========


@router.post(
    "",
    response_model=ModelDeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Provider not found"},
        409: {"model": ErrorResponse, "description": "Deployment already exists"},
    },
)
async def create_deployment(
    data: ModelDeploymentCreate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ModelDeploymentResponse:
    """Create a new model deployment.

    Supports two modes:
    1. Linked mode: Links to an existing provider configuration
    2. Standalone mode: Stores API key directly on the deployment (LiteLLM-style)
    """
    # Determine mode and validate
    is_standalone = data.provider_config_id is None
    provider = None
    
    if not is_standalone:
        # Linked mode: Verify provider exists
        provider = await db.get(ProviderConfig, data.provider_config_id)
        if not provider:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Provider configuration not found: {data.provider_config_id}",
            )
    else:
        # Standalone mode: Validate required fields already done by schema validator
        pass

    # Check permissions
    if data.org_id is None:
        # Global deployment - requires superuser
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can create global deployments",
            )
    else:
        # Org-scoped deployment - verify org exists
        org = await db.get(Organization, data.org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization not found: {data.org_id}",
            )
        # Check org admin permission
        await require_org_admin(current_user, data.org_id, db)

    # Check for duplicate (same model + provider combination)
    if not is_standalone:
        result = await db.execute(
            select(ModelDeployment).where(
                ModelDeployment.model_name == data.model_name,
                ModelDeployment.provider_config_id == data.provider_config_id,
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Deployment for model '{data.model_name}' with this provider already exists",
            )

    # Create deployment
    deployment = ModelDeployment(
        model_name=data.model_name,
        provider_model=data.provider_model,
        provider_config_id=data.provider_config_id,
        provider_type=data.provider_type if is_standalone else None,
        model_type=data.model_type,
        api_key_encrypted=encrypt_api_key(data.api_key) if is_standalone and data.api_key else None,
        api_base=data.api_base if is_standalone else None,
        org_id=data.org_id,
        is_active=data.is_active,
        priority=data.priority,
        tpm_limit=data.tpm_limit,
        rpm_limit=data.rpm_limit,
        timeout=data.timeout,
        settings=data.settings or {},
    )
    db.add(deployment)
    await db.flush()  # Get deployment ID before creating pricing

    # Auto-create default pricing record if none exists for this model/org
    existing_pricing = await db.execute(
        select(ModelPricing).where(
            ModelPricing.model_name == data.model_name,
            ModelPricing.org_id == data.org_id if data.org_id else ModelPricing.org_id.is_(None),
            ModelPricing.team_id.is_(None),
        )
    )
    pricing_record = existing_pricing.scalar_one_or_none()

    if not pricing_record:
        # Create default pricing with $0 (user can update later)
        pricing_record = ModelPricing(
            model_name=data.model_name,
            org_id=data.org_id,
            mode=data.model_type,
            input_cost_per_token=Decimal("0"),
            output_cost_per_token=Decimal("0"),
        )
        db.add(pricing_record)
        await db.flush()  # Get the pricing ID
        logger.info(f"Auto-created default pricing for {data.model_name}")

    # Link deployment to pricing
    deployment.pricing_id = pricing_record.id

    await db.commit()

    # Refresh with provider relationship
    await db.refresh(deployment, ["provider_config", "pricing"])

    mode_str = "standalone" if is_standalone else f"linked to {provider.name if provider else 'unknown'}"
    logger.info(
        f"Created model deployment: {deployment.model_name} -> {deployment.provider_model} "
        f"(mode={mode_str}, org_id={deployment.org_id})"
    )

    return _deployment_to_response(deployment)


@router.get(
    "",
    response_model=ModelDeploymentListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def list_deployments(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pagination: Annotated[PaginationParams, Depends()],
    current_user: Annotated[User, Depends(require_user)],
    model_name: str | None = Query(default=None, description="Filter by model name"),
    provider_id: UUID | None = Query(default=None, description="Filter by provider"),
    org_id: UUID | None = Query(default=None, description="Filter by organization"),
    model_type: str | None = Query(default=None, description="Filter by model type (chat, embedding, image_generation, etc.)"),
    is_active: bool | None = Query(default=None, description="Filter by active status"),
) -> ModelDeploymentListResponse:
    """List model deployments.

    Superusers can see all deployments.
    Regular users see global deployments + their org's deployments.
    """
    # Build query with provider relationship
    query = select(ModelDeployment).options(selectinload(ModelDeployment.provider_config))

    # Filter by model name
    if model_name:
        query = query.where(ModelDeployment.model_name == model_name)

    # Filter by provider
    if provider_id:
        query = query.where(ModelDeployment.provider_config_id == provider_id)

    # Filter by organization
    if org_id is not None:
        query = query.where(ModelDeployment.org_id == org_id)
    elif not current_user.is_superuser:
        # Non-superusers see global deployments + their org's deployments
        user_org_ids = await get_user_org_ids(current_user, db)
        if user_org_ids:
            from sqlalchemy import or_
            query = query.where(
                or_(
                    ModelDeployment.org_id.is_(None),
                    ModelDeployment.org_id.in_(user_org_ids),
                )
            )
        else:
            query = query.where(ModelDeployment.org_id.is_(None))

    # Filter by active status
    if is_active is not None:
        query = query.where(ModelDeployment.is_active == is_active)

    # Filter by model type
    if model_type:
        query = query.where(ModelDeployment.model_type == model_type)

    # Order by priority (higher first), then by created_at
    query = query.order_by(ModelDeployment.priority.desc(), ModelDeployment.created_at)

    # Execute query
    result = await db.execute(query)
    deployments = result.scalars().all()
    total = len(deployments)

    # Apply pagination
    offset = (pagination.page - 1) * pagination.page_size
    paginated_deployments = deployments[offset : offset + pagination.page_size]

    pages = (total + pagination.page_size - 1) // pagination.page_size

    return ModelDeploymentListResponse(
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
        pages=pages,
        items=[_deployment_to_response(d) for d in paginated_deployments],
    )


@router.get(
    "/{deployment_id}",
    response_model=ModelDeploymentWithProvider,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Deployment not found"},
    },
)
async def get_deployment(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ModelDeploymentWithProvider:
    """Get model deployment by ID with full provider details."""
    result = await db.execute(
        select(ModelDeployment)
        .where(ModelDeployment.id == deployment_id)
        .options(selectinload(ModelDeployment.provider_config))
    )
    deployment = result.scalar_one_or_none()

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )

    # Check access
    if deployment.org_id and not current_user.is_superuser:
        # Verify user is member of the org
        if not await check_org_member(current_user, deployment.org_id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this deployment",
            )

    return _deployment_to_response(deployment, include_provider=True)


@router.patch(
    "/{deployment_id}",
    response_model=ModelDeploymentResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Deployment not found"},
    },
)
async def update_deployment(
    deployment_id: UUID,
    data: ModelDeploymentUpdate,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ModelDeploymentResponse:
    """Update a model deployment.

    Only superusers can update global deployments.
    Org admins can update org-scoped deployments.
    """
    result = await db.execute(
        select(ModelDeployment)
        .where(ModelDeployment.id == deployment_id)
        .options(selectinload(ModelDeployment.provider_config))
    )
    deployment = result.scalar_one_or_none()

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )

    # Check permissions
    if deployment.org_id is None:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can update global deployments",
            )
    else:
        # Org-scoped deployment - require org admin
        await require_org_admin(current_user, deployment.org_id, db)

    # Update fields
    if data.model_name is not None:
        deployment.model_name = data.model_name
    if data.provider_model is not None:
        deployment.provider_model = data.provider_model
    if data.is_active is not None:
        deployment.is_active = data.is_active
    if data.priority is not None:
        deployment.priority = data.priority
    if data.tpm_limit is not None:
        deployment.tpm_limit = data.tpm_limit
    if data.rpm_limit is not None:
        deployment.rpm_limit = data.rpm_limit
    if data.timeout is not None:
        deployment.timeout = data.timeout
    if data.settings is not None:
        deployment.settings = data.settings
    
    # Handle mode switching and standalone fields
    if data.provider_config_id is not None:
        # Switching to linked mode or updating linked provider
        if data.provider_config_id != deployment.provider_config_id:
            provider = await db.get(ProviderConfig, data.provider_config_id)
            if not provider:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Provider configuration not found: {data.provider_config_id}",
                )
            deployment.provider_config_id = data.provider_config_id
            # Clear standalone fields
            deployment.provider_type = None
            deployment.api_key_encrypted = None
            deployment.api_base = None
    elif data.provider_config_id is None and deployment.provider_config_id is not None:
        # Switching to standalone mode - requires provider_type and api_key
        if not data.provider_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="provider_type is required when switching to standalone mode",
            )
        deployment.provider_config_id = None
        deployment.provider_type = data.provider_type
        if data.api_key:
            deployment.api_key_encrypted = encrypt_api_key(data.api_key)
        if data.api_base is not None:
            deployment.api_base = data.api_base
    elif deployment.provider_config_id is None:
        # Already in standalone mode - update standalone fields
        if data.provider_type is not None:
            deployment.provider_type = data.provider_type
        if data.api_key:
            deployment.api_key_encrypted = encrypt_api_key(data.api_key)
        if data.api_base is not None:
            deployment.api_base = data.api_base

    await db.commit()
    await db.refresh(deployment)

    logger.info(f"Updated deployment: {deployment.model_name} (id={deployment.id})")

    return _deployment_to_response(deployment)


@router.delete(
    "/{deployment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Deployment not found"},
    },
)
async def delete_deployment(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> None:
    """Delete a model deployment.

    Only superusers can delete global deployments.
    """
    deployment = await db.get(ModelDeployment, deployment_id)

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )

    # Check permissions
    if deployment.org_id is None:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can delete global deployments",
            )
    else:
        # Org-scoped deployment - require org admin
        await require_org_admin(current_user, deployment.org_id, db)

    model_name = deployment.model_name
    await db.delete(deployment)
    await db.commit()

    logger.info(f"Deleted deployment: {model_name} (id={deployment_id})")


# ========== Deployment Enable/Disable ==========


@router.post(
    "/{deployment_id}/enable",
    response_model=ModelDeploymentResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Deployment not found"},
    },
)
async def enable_deployment(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ModelDeploymentResponse:
    """Enable a model deployment."""
    result = await db.execute(
        select(ModelDeployment)
        .where(ModelDeployment.id == deployment_id)
        .options(selectinload(ModelDeployment.provider_config))
    )
    deployment = result.scalar_one_or_none()

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )

    # Check permissions
    if deployment.org_id is None:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can modify global deployments",
            )
    else:
        # Org-scoped deployment - require org admin
        await require_org_admin(current_user, deployment.org_id, db)

    deployment.is_active = True
    await db.commit()
    await db.refresh(deployment)

    logger.info(f"Enabled deployment: {deployment.model_name} (id={deployment.id})")

    return _deployment_to_response(deployment)


@router.post(
    "/{deployment_id}/disable",
    response_model=ModelDeploymentResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Deployment not found"},
    },
)
async def disable_deployment(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
) -> ModelDeploymentResponse:
    """Disable a model deployment."""
    result = await db.execute(
        select(ModelDeployment)
        .where(ModelDeployment.id == deployment_id)
        .options(selectinload(ModelDeployment.provider_config))
    )
    deployment = result.scalar_one_or_none()

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )

    # Check permissions
    if deployment.org_id is None:
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can modify global deployments",
            )
    else:
        # Org-scoped deployment - require org admin
        await require_org_admin(current_user, deployment.org_id, db)

    deployment.is_active = False
    await db.commit()
    await db.refresh(deployment)

    logger.info(f"Disabled deployment: {deployment.model_name} (id={deployment.id})")

    return _deployment_to_response(deployment)


# ========== Convenience Endpoints ==========


@router.get(
    "/model/{model_name}",
    response_model=list[ModelDeploymentResponse],
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def get_deployments_for_model(
    model_name: str,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(require_user)],
    only_active: bool = Query(default=True, description="Only return active deployments"),
) -> list[ModelDeploymentResponse]:
    """Get all deployments for a specific model name.

    Useful for understanding routing options for a model.
    """
    query = (
        select(ModelDeployment)
        .where(ModelDeployment.model_name == model_name)
        .options(selectinload(ModelDeployment.provider_config))
        .order_by(ModelDeployment.priority.desc())
    )

    if only_active:
        query = query.where(ModelDeployment.is_active == True)

    if not current_user.is_superuser:
        # Non-superusers see global deployments only
        query = query.where(ModelDeployment.org_id.is_(None))

    result = await db.execute(query)
    deployments = result.scalars().all()

    return [_deployment_to_response(d) for d in deployments]


@router.get(
    "/suggest-type",
    response_model=dict,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def suggest_deployment_type(
    model_name: str = Query(..., description="Model name to analyze"),
    provider_model: str | None = Query(default=None, description="Provider model ID"),
    current_user: Annotated[User, Depends(require_user)] = None,
) -> dict:
    """Suggest model type based on naming patterns.

    Analyzes model name and provider model patterns to suggest
    the most likely model type (chat, embedding, etc.).
    """
    return suggest_model_type(model_name, provider_model)


@router.get(
    "/model-types",
    response_model=list[dict],
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
    },
)
async def list_model_types(
    current_user: Annotated[User, Depends(require_user)] = None,
) -> list[dict]:
    """List all available model types with descriptions."""
    return get_all_model_types()


# ========== Dependencies ==========


def get_pricing_manager(request: Request) -> PricingManager:
    """Get the pricing manager from app state."""
    return request.app.state.pricing_manager


# ========== Pricing by Deployment ==========


def _pricing_config_to_response(
    model: str,
    config,
    source: str = "yaml",
    org_id=None,
    team_id=None,
) -> PricingResponse:
    """Convert PricingConfig to PricingResponse."""
    return PricingResponse(
        model=model,
        mode=config.mode,
        input_cost_per_token=config.input_cost_per_token,
        output_cost_per_token=config.output_cost_per_token,
        cache_creation_input_token_cost=config.cache_creation_input_token_cost,
        cache_read_input_token_cost=config.cache_read_input_token_cost,
        image_cost_per_image=config.image_cost_per_image,
        image_sizes={k: str(v) for k, v in config.image_sizes.items()},
        quality_pricing=config.quality_pricing,
        audio_cost_per_character=config.audio_cost_per_character,
        audio_cost_per_minute=config.audio_cost_per_minute,
        rerank_cost_per_search=config.rerank_cost_per_search,
        batch_discount_percent=config.batch_discount_percent,
        base_model=config.base_model,
        max_tokens=config.max_tokens,
        max_input_tokens=config.max_input_tokens,
        max_output_tokens=config.max_output_tokens,
        source=source,
        org_id=org_id,
        team_id=team_id,
    )


@router.get(
    "/{deployment_id}/pricing",
    response_model=PricingResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        404: {"model": ErrorResponse, "description": "Deployment not found"},
    },
)
async def get_deployment_pricing(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pricing_manager: Annotated[PricingManager, Depends(get_pricing_manager)],
    current_user: Annotated[User, Depends(require_user)],
) -> PricingResponse:
    """Get pricing for a deployment by its ID.

    This endpoint resolves the model name from the deployment and fetches
    the associated pricing configuration, avoiding URL encoding issues
    with model names that contain special characters like '/'.
    """
    # Fetch deployment
    deployment = await db.get(ModelDeployment, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )

    # Check access
    if deployment.org_id and not current_user.is_superuser:
        if not await check_org_member(current_user, deployment.org_id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have access to this deployment",
            )

    # Get pricing using model_name
    config = pricing_manager.get_pricing(deployment.model_name, deployment.org_id)

    # Determine source - check hierarchy: org-specific -> global -> yaml/default
    source = "default"
    if deployment.org_id:
        org_key = f"org:{deployment.org_id}:{deployment.model_name}"
        if org_key in pricing_manager._db_overrides:
            source = "custom"
        elif deployment.model_name in pricing_manager._db_overrides:
            # Global override applies even with org_id
            source = "custom"
    elif deployment.model_name in pricing_manager._db_overrides:
        source = "custom"

    return _pricing_config_to_response(
        deployment.model_name, config, source, deployment.org_id
    )


@router.post(
    "/{deployment_id}/pricing",
    response_model=PricingResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Deployment not found"},
    },
)
async def set_deployment_pricing(
    deployment_id: UUID,
    request: PricingCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pricing_manager: Annotated[PricingManager, Depends(get_pricing_manager)],
    current_user: Annotated[User, Depends(require_user)],
) -> PricingResponse:
    """Set pricing for a deployment by its ID.

    This endpoint resolves the model name from the deployment and sets
    the pricing configuration, avoiding URL encoding issues with model
    names that contain special characters like '/'.

    Pricing is scoped to the deployment's organization if set,
    otherwise it's a global override (requires superuser).
    """
    # Fetch deployment
    deployment = await db.get(ModelDeployment, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )

    # Check permissions
    if deployment.org_id is None:
        # Global pricing - requires superuser
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can set global pricing",
            )
    else:
        # Org-scoped pricing - require org admin
        await require_org_admin(current_user, deployment.org_id, db)

    # Build PricingConfig from request
    from deltallm.pricing.models import PricingConfig

    # Debug logging to trace pricing values
    logger.debug(f"Pricing request - input_cost: {request.input_cost_per_token!r} (type: {type(request.input_cost_per_token)})")
    logger.debug(f"Pricing request - output_cost: {request.output_cost_per_token!r} (type: {type(request.output_cost_per_token)})")

    config = PricingConfig(
        model=deployment.model_name,
        mode=request.mode,
        input_cost_per_token=request.input_cost_per_token or Decimal("0"),
        output_cost_per_token=request.output_cost_per_token or Decimal("0"),
        cache_creation_input_token_cost=request.cache_creation_input_token_cost,
        cache_read_input_token_cost=request.cache_read_input_token_cost,
        image_cost_per_image=request.image_cost_per_image,
        image_sizes=request.image_sizes or {},
        quality_pricing=request.quality_pricing or {},
        audio_cost_per_character=request.audio_cost_per_character,
        audio_cost_per_minute=request.audio_cost_per_minute,
        rerank_cost_per_search=request.rerank_cost_per_search,
        batch_discount_percent=request.batch_discount_percent or 50.0,
        base_model=request.base_model,
        max_tokens=request.max_tokens,
        max_input_tokens=request.max_input_tokens,
        max_output_tokens=request.max_output_tokens,
    )

    # Log config values before saving
    logger.info(f"Saving pricing for {deployment.model_name} - input_cost: {config.input_cost_per_token!r}, output_cost: {config.output_cost_per_token!r}")

    # Save to database AND memory
    await pricing_manager.save_to_database(
        db, deployment.model_name, config, deployment.org_id
    )

    logger.info(f"Pricing saved successfully for {deployment.model_name}")

    return _pricing_config_to_response(
        deployment.model_name, config, "custom", deployment.org_id
    )


@router.delete(
    "/{deployment_id}/pricing",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Deployment not found"},
    },
)
async def delete_deployment_pricing(
    deployment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    pricing_manager: Annotated[PricingManager, Depends(get_pricing_manager)],
    current_user: Annotated[User, Depends(require_user)],
) -> None:
    """Delete pricing for a deployment by its ID.

    This endpoint resolves the model name from the deployment and deletes
    the pricing configuration, avoiding URL encoding issues with model
    names that contain special characters like '/'.
    """
    # Fetch deployment
    deployment = await db.get(ModelDeployment, deployment_id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment not found: {deployment_id}",
        )

    # Check permissions
    if deployment.org_id is None:
        # Global pricing - requires superuser
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only superusers can delete global pricing",
            )
    else:
        # Org-scoped pricing - require org admin
        await require_org_admin(current_user, deployment.org_id, db)

    # Delete from database AND memory
    removed = await pricing_manager.delete_from_database(
        db, deployment.model_name, deployment.org_id
    )

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Custom pricing not found for this deployment",
        )
