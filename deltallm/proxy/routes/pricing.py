"""Pricing management API routes.

This module provides REST API endpoints for managing LLM pricing configurations,
including viewing, setting, and testing pricing for different models.
"""

import io
import json
import logging
from decimal import Decimal
from typing import Annotated, Optional
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from deltallm.db.models import User
from deltallm.db.session import get_db_session
from deltallm.pricing.calculator import CostCalculator
from deltallm.pricing.manager import PricingManager
from deltallm.pricing.models import PricingConfig
from deltallm.proxy.dependencies import require_user
from deltallm.proxy.schemas import ErrorResponse
from deltallm.proxy.schemas_pricing import (
    CostBreakdownResponse,
    CostCalculationRequest,
    PricingCreateRequest,
    PricingExportResponse,
    PricingFilterParams,
    PricingImportResponse,
    PricingListResponse,
    PricingResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/pricing", tags=["Pricing"])


# ========== Dependencies ==========


def get_pricing_manager(request: Request) -> PricingManager:
    """Get the pricing manager from app state."""
    return request.app.state.pricing_manager


def get_cost_calculator(request: Request) -> CostCalculator:
    """Get the cost calculator from app state."""
    return request.app.state.cost_calculator


# ========== Helper Functions ==========


def pricing_config_to_response(
    model: str,
    config: PricingConfig,
    source: str = "yaml",
    org_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
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


def cost_breakdown_to_response(breakdown) -> CostBreakdownResponse:
    """Convert CostBreakdown to CostBreakdownResponse."""
    return CostBreakdownResponse(
        total_cost=str(breakdown.total_cost),
        currency=breakdown.currency,
        input_cost=str(breakdown.input_cost),
        output_cost=str(breakdown.output_cost),
        cache_creation_cost=str(breakdown.cache_creation_cost) if breakdown.cache_creation_cost else None,
        cache_read_cost=str(breakdown.cache_read_cost) if breakdown.cache_read_cost else None,
        image_cost=str(breakdown.image_cost) if breakdown.image_cost else None,
        audio_cost=str(breakdown.audio_cost) if breakdown.audio_cost else None,
        rerank_cost=str(breakdown.rerank_cost) if breakdown.rerank_cost else None,
        batch_discount=str(breakdown.batch_discount) if breakdown.batch_discount else None,
        discount_percent=breakdown.discount_percent,
        original_cost=str(breakdown.original_cost) if breakdown.batch_discount else None,
    )


# ========== Pricing CRUD ==========


@router.get(
    "/models",
    response_model=PricingListResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
    },
)
async def list_priced_models(
    filters: Annotated[PricingFilterParams, Depends()],
    pricing_manager: Annotated[PricingManager, Depends(get_pricing_manager)],
    current_user: Annotated[User, Depends(require_user)],
) -> PricingListResponse:
    """List all models with pricing configured.
    
    Supports filtering by mode, provider, and source. Results are paginated.
    """
    # Get all models from pricing manager
    all_models = pricing_manager.list_models(include_overrides=True)
    
    # Apply filters
    filtered_models = {}
    for model_name, config in all_models.items():
        # Mode filter
        if filters.mode and config.mode != filters.mode:
            continue
        
        # Search filter (case-insensitive)
        if filters.search and filters.search.lower() not in model_name.lower():
            continue
        
        # Provider filter (check model prefix)
        if filters.provider:
            provider_prefix = filters.provider.lower()
            if not model_name.lower().startswith(provider_prefix):
                continue
        
        filtered_models[model_name] = config
    
    # Convert to list and sort
    model_list = list(filtered_models.items())
    model_list.sort(key=lambda x: x[0])
    
    # Pagination
    total = len(model_list)
    start_idx = (filters.page - 1) * filters.page_size
    end_idx = start_idx + filters.page_size
    paginated_models = model_list[start_idx:end_idx]
    
    # Build response
    items = []
    for model_name, config in paginated_models:
        # Determine source based on key format - DB-stored = "custom", else "default"
        source = "default"
        if model_name.startswith("team:"):
            source = "custom"
        elif model_name.startswith("org:"):
            source = "custom"
        elif model_name in pricing_manager._db_overrides:
            source = "custom"

        items.append(pricing_config_to_response(model_name, config, source))
    
    return PricingListResponse(
        total=total,
        page=filters.page,
        page_size=filters.page_size,
        items=items,
    )





# ========== Cost Calculation ==========


@router.post(
    "/test-calculate",
    response_model=CostBreakdownResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
    },
)
async def test_cost_calculation(
    request: CostCalculationRequest,
    org_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    calculator: Annotated[CostCalculator, Depends(get_cost_calculator)] = None,
    current_user: Annotated[User, Depends(require_user)] = None,
) -> CostBreakdownResponse:
    """Test cost calculation without making a request.
    
    Example:
    ```json
    {
        "model": "gpt-4o",
        "endpoint": "/v1/chat/completions",
        "params": {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "cached_tokens": 200
        }
    }
    ```
    """
    try:
        breakdown = calculator.estimate_cost(
            endpoint=request.endpoint,
            model=request.model,
            org_id=org_id,
            team_id=team_id,
            **request.params
        )
        return cost_breakdown_to_response(breakdown)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Error calculating cost: {str(e)}",
        )


# ========== Import/Export ==========


@router.post(
    "/import",
    response_model=PricingImportResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
    },
)
async def import_pricing(
    file: Annotated[bytes, File()],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    dry_run: bool = False,
    pricing_manager: Annotated[PricingManager, Depends(get_pricing_manager)] = None,
    current_user: Annotated[User, Depends(require_user)] = None,
) -> PricingImportResponse:
    """Import pricing from YAML/JSON file.

    The file should have the same format as config/pricing.yaml:
    ```yaml
    version: "1.0"
    pricing:
      model-name:
        mode: chat
        input_cost_per_token: 0.000001
        ...
    ```

    Set dry_run=true to validate without importing.
    Imported pricing is persisted to the database.
    """
    # Check permissions
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can import pricing",
        )

    # Parse file
    try:
        content = file.decode('utf-8')
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid YAML: {str(e)}",
        )
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be valid UTF-8 encoded text",
        )

    if not isinstance(data, dict) or "pricing" not in data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid format: missing 'pricing' section",
        )

    # Validate and import
    imported_count = 0
    errors = []

    for model_name, model_data in data["pricing"].items():
        try:
            config = PricingConfig.from_dict(model_name, model_data)

            if not dry_run:
                # Save to database AND memory
                await pricing_manager.save_to_database(db, model_name, config)

            imported_count += 1
        except Exception as e:
            errors.append(f"{model_name}: {str(e)}")

    return PricingImportResponse(
        success=len(errors) == 0,
        imported_count=imported_count,
        errors=errors,
        dry_run=dry_run,
    )


@router.get(
    "/export",
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
    },
)
async def export_pricing(
    format: str = Query(default="yaml", pattern="^(yaml|json)$"),
    pricing_manager: Annotated[PricingManager, Depends(get_pricing_manager)] = None,
    current_user: Annotated[User, Depends(require_user)] = None,
) -> Response:
    """Export current pricing configuration.
    
    Returns all pricing configs (defaults + custom overrides) in YAML or JSON format.
    """
    # Get all pricing configs
    all_models = pricing_manager.list_models(include_overrides=True)
    
    # Build export data
    pricing_data = {}
    for model_name, config in all_models.items():
        # Skip internal override keys (they have special prefixes)
        if model_name.startswith(("team:", "org:")):
            continue
        pricing_data[model_name] = config.to_dict()
    
    export_data = {
        "version": "1.0",
        "exported_at": str(logger.manager and logger.manager or ""),  # Placeholder
        "pricing": pricing_data,
    }
    
    if format == "json":
        content = json.dumps(export_data, indent=2)
        media_type = "application/json"
        filename = "pricing.json"
    else:
        content = yaml.dump(export_data, default_flow_style=False, sort_keys=True)
        media_type = "application/x-yaml"
        filename = "pricing.yaml"
    
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ========== Utility Endpoints ==========


@router.post(
    "/reload",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        401: {"model": ErrorResponse, "description": "Authentication required"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
    },
)
async def reload_pricing(
    pricing_manager: Annotated[PricingManager, Depends(get_pricing_manager)] = None,
    current_user: Annotated[User, Depends(require_user)] = None,
) -> None:
    """Force reload of pricing configuration from YAML file.
    
    Useful when hot reload is disabled or not working.
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only superusers can reload pricing",
        )
    
    pricing_manager.reload()
