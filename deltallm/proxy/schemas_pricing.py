"""Pydantic schemas for Pricing API.

This module defines request and response models for the pricing management API.
"""

from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ========== Pricing Schemas ==========

class PricingConfigBase(BaseModel):
    """Base pricing configuration fields."""
    
    mode: Literal[
        "chat", "embedding", "image_generation",
        "audio_speech", "audio_transcription",
        "rerank", "moderation", "batch"
    ] = Field(..., description="Pricing mode for this model")
    
    # Token-based pricing
    input_cost_per_token: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Cost per input token (USD)"
    )
    output_cost_per_token: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Cost per output token (USD)"
    )
    
    # Prompt caching
    cache_creation_input_token_cost: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Cost for creating prompt cache (USD per token)"
    )
    cache_read_input_token_cost: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Cost for reading from prompt cache (USD per token)"
    )
    
    # Image generation
    image_cost_per_image: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Cost per image (flat rate)"
    )
    image_sizes: Optional[dict[str, Decimal]] = Field(
        default=None,
        description="Cost per image size (e.g., {'1024x1024': 0.04})"
    )
    quality_pricing: Optional[dict[str, float]] = Field(
        default=None,
        description="Quality multipliers (e.g., {'standard': 1.0, 'hd': 2.0})"
    )
    
    # Audio
    audio_cost_per_character: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Cost per character for TTS (USD)"
    )
    audio_cost_per_minute: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Cost per minute for STT (USD)"
    )
    
    # Rerank
    rerank_cost_per_search: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Cost per rerank search (USD)"
    )
    
    # Batch
    batch_discount_percent: Optional[float] = Field(
        default=None,
        ge=0,
        le=100,
        description="Batch discount percentage"
    )
    base_model: Optional[str] = Field(
        default=None,
        description="Base model for batch pricing"
    )
    
    # Limits
    max_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description="Maximum tokens allowed"
    )
    max_input_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description="Maximum input tokens allowed"
    )
    max_output_tokens: Optional[int] = Field(
        default=None,
        ge=1,
        description="Maximum output tokens allowed"
    )


class PricingCreateRequest(PricingConfigBase):
    """Request to create/update pricing for a model."""
    
    pass


class PricingResponse(BaseModel):
    """Pricing response model."""
    
    model_config = ConfigDict(from_attributes=True)
    
    model: str = Field(..., description="Model name")
    mode: str = Field(..., description="Pricing mode")
    
    # Token-based pricing
    input_cost_per_token: Decimal = Field(default=Decimal("0"))
    output_cost_per_token: Decimal = Field(default=Decimal("0"))
    
    # Prompt caching
    cache_creation_input_token_cost: Optional[Decimal] = None
    cache_read_input_token_cost: Optional[Decimal] = None
    
    # Image generation
    image_cost_per_image: Optional[Decimal] = None
    image_sizes: dict[str, str] = Field(default_factory=dict)
    quality_pricing: dict[str, float] = Field(default_factory=dict)
    
    # Audio
    audio_cost_per_character: Optional[Decimal] = None
    audio_cost_per_minute: Optional[Decimal] = None
    
    # Rerank
    rerank_cost_per_search: Optional[Decimal] = None
    
    # Batch
    batch_discount_percent: float = Field(default=50.0)
    base_model: Optional[str] = None
    
    # Limits
    max_tokens: Optional[int] = None
    max_input_tokens: Optional[int] = None
    max_output_tokens: Optional[int] = None
    
    # Source information
    source: str = Field(..., description="Source: yaml, db_global, db_org, db_team")
    org_id: Optional[UUID] = None
    team_id: Optional[UUID] = None


class PricingListResponse(BaseModel):
    """Paginated list of pricing configurations."""
    
    total: int = Field(description="Total number of models")
    page: int = Field(description="Current page number")
    page_size: int = Field(description="Items per page")
    items: list[PricingResponse] = Field(description="Pricing configurations")


class PricingFilterParams(BaseModel):
    """Query parameters for filtering pricing list."""
    
    mode: Optional[str] = Field(
        default=None,
        description="Filter by pricing mode (chat, embedding, etc.)"
    )
    provider: Optional[str] = Field(
        default=None,
        description="Filter by provider (openai, anthropic, etc.)"
    )
    source: Optional[str] = Field(
        default=None,
        description="Filter by source (yaml, db_global, db_org, db_team)"
    )
    search: Optional[str] = Field(
        default=None,
        description="Search by model name"
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


# ========== Cost Calculation Schemas ==========

class CostCalculationRequest(BaseModel):
    """Request to calculate cost for a hypothetical request."""
    
    model: str = Field(..., description="Model name")
    endpoint: str = Field(
        ...,
        description="Endpoint path (/v1/chat/completions, /v1/images/generations, etc.)"
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Endpoint-specific parameters"
    )


class CostBreakdownResponse(BaseModel):
    """Cost breakdown response."""
    
    total_cost: str = Field(description="Total cost in USD")
    currency: str = Field(default="USD")
    
    # Component costs
    input_cost: str = Field(default="0")
    output_cost: str = Field(default="0")
    cache_creation_cost: Optional[str] = None
    cache_read_cost: Optional[str] = None
    image_cost: Optional[str] = None
    audio_cost: Optional[str] = None
    rerank_cost: Optional[str] = None
    
    # Discounts
    batch_discount: Optional[str] = None
    discount_percent: Optional[float] = None
    original_cost: Optional[str] = None


# ========== Import/Export Schemas ==========

class PricingImportResponse(BaseModel):
    """Response from pricing import."""
    
    success: bool
    imported_count: int = Field(description="Number of models imported")
    errors: list[str] = Field(default_factory=list, description="Any import errors")
    dry_run: bool = Field(description="Whether this was a dry run")


class PricingExportResponse(BaseModel):
    """Response from pricing export."""
    
    version: str = Field(default="1.0")
    exported_at: str = Field(description="Export timestamp")
    pricing: dict[str, Any] = Field(description="Pricing configuration")
