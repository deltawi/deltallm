"""Pydantic schemas for ProxyLLM API.

This module defines request and response models for the REST API,
including organization, team, and RBAC management.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

from ..types.common import ModelType


# ========== Common Schemas ==========

class BaseResponse(BaseModel):
    """Base response with common fields."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    created_at: datetime
    updated_at: datetime | None = None


class PaginationParams(BaseModel):
    """Pagination query parameters."""
    
    page: int = Field(default=1, ge=1, description="Page number")
    page_size: int = Field(default=20, ge=1, le=100, description="Items per page")


class PaginatedResponse(BaseModel):
    """Paginated response wrapper."""
    
    total: int = Field(description="Total number of items")
    page: int = Field(description="Current page number")
    page_size: int = Field(description="Items per page")
    pages: int = Field(description="Total number of pages")


# ========== Organization Schemas ==========

class OrganizationCreate(BaseModel):
    """Request to create an organization."""
    
    name: str = Field(..., min_length=1, max_length=255, description="Organization name")
    slug: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9-]+$",
        description="URL-friendly identifier (lowercase, alphanumeric, hyphens)",
    )
    description: str | None = Field(default=None, max_length=1000, description="Organization description")
    max_budget: Decimal | None = Field(
        default=None,
        ge=0,
        description="Maximum budget limit (USD)",
    )


class OrganizationUpdate(BaseModel):
    """Request to update an organization."""
    
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    max_budget: Decimal | None = Field(default=None, ge=0)
    settings: dict[str, Any] | None = Field(default=None, description="Organization settings")


class OrganizationResponse(BaseResponse):
    """Organization response model."""
    
    name: str
    slug: str
    description: str | None
    max_budget: Decimal | None
    spend: Decimal
    settings: dict[str, Any]


class OrganizationListResponse(PaginatedResponse):
    """Paginated list of organizations."""
    
    items: list[OrganizationResponse]


# ========== Team Schemas ==========

class TeamCreate(BaseModel):
    """Request to create a team."""
    
    org_id: UUID = Field(..., description="Parent organization ID")
    name: str = Field(..., min_length=1, max_length=255, description="Team name")
    slug: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9-]+$",
        description="URL-friendly identifier (unique within org)",
    )
    description: str | None = Field(default=None, max_length=1000, description="Team description")
    max_budget: Decimal | None = Field(default=None, ge=0, description="Maximum budget limit (USD)")


class TeamUpdate(BaseModel):
    """Request to update a team."""
    
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    max_budget: Decimal | None = Field(default=None, ge=0)
    settings: dict[str, Any] | None = Field(default=None, description="Team settings")


class TeamResponse(BaseResponse):
    """Team response model."""
    
    name: str
    slug: str
    org_id: UUID
    description: str | None
    max_budget: Decimal | None
    spend: Decimal
    settings: dict[str, Any]
    organization: OrganizationResponse | None = None


class TeamListResponse(PaginatedResponse):
    """Paginated list of teams."""
    
    items: list[TeamResponse]


# ========== User Schemas ==========

class UserCreate(BaseModel):
    """Request to create a user."""
    
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=8, max_length=128, description="User password")
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)


class UserUpdate(BaseModel):
    """Request to update a user."""
    
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = Field(default=None)


class UserResponse(BaseResponse):
    """User response model (excludes sensitive data)."""
    
    email: str  # Using str instead of EmailStr to support all email formats including .local
    first_name: str | None
    last_name: str | None
    is_superuser: bool
    is_active: bool
    last_login_at: datetime | None
    
    @property
    def full_name(self) -> str:
        """Return user's full name."""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name or self.last_name or self.email


class UserListResponse(PaginatedResponse):
    """Paginated list of users."""
    
    items: list[UserResponse]


# ========== Membership Schemas ==========

class OrgMemberCreate(BaseModel):
    """Request to add a member to an organization."""
    
    email: EmailStr = Field(..., description="User email to invite")
    role: str = Field(
        default="member",
        pattern=r"^(owner|admin|member|viewer)$",
        description="Organization role",
    )


class OrgMemberUpdate(BaseModel):
    """Request to update an org member's role."""
    
    role: str = Field(
        ...,
        pattern=r"^(owner|admin|member|viewer)$",
        description="New organization role",
    )


class OrgMemberResponse(BaseModel):
    """Organization member response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    user_id: UUID
    org_id: UUID
    role: str
    joined_at: datetime
    user: UserResponse


class OrgMemberListResponse(PaginatedResponse):
    """Paginated list of org members."""
    
    items: list[OrgMemberResponse]


class TeamMemberCreate(BaseModel):
    """Request to add a member to a team."""
    
    user_id: UUID = Field(..., description="User ID to add")
    role: str = Field(
        default="member",
        pattern=r"^(admin|member)$",
        description="Team role",
    )


class TeamMemberUpdate(BaseModel):
    """Request to update a team member's role."""
    
    role: str = Field(
        ...,
        pattern=r"^(admin|member)$",
        description="New team role",
    )


class TeamMemberResponse(BaseModel):
    """Team member response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    user_id: UUID
    team_id: UUID
    role: str
    joined_at: datetime
    user: UserResponse


class TeamMemberListResponse(PaginatedResponse):
    """Paginated list of team members."""
    
    items: list[TeamMemberResponse]


# ========== Role & Permission Schemas ==========

class PermissionResponse(BaseModel):
    """Permission response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    resource: str
    action: str
    description: str | None
    
    @property
    def full_name(self) -> str:
        return f"{self.resource}:{self.action}"


class RoleCreate(BaseModel):
    """Request to create a custom role."""
    
    name: str = Field(..., min_length=1, max_length=100, description="Role name")
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(
        default_factory=list,
        description="List of permission strings (e.g., ['api_key:create', 'model:use'])",
    )


class RoleUpdate(BaseModel):
    """Request to update a custom role."""
    
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = Field(default=None)


class RoleResponse(BaseModel):
    """Role response."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    name: str
    description: str | None
    org_id: UUID | None
    is_system: bool
    created_at: datetime
    permissions: list[PermissionResponse]


class RoleListResponse(PaginatedResponse):
    """Paginated list of roles."""
    
    items: list[RoleResponse]


# ========== API Key Schemas ==========

class APIKeyScope(BaseModel):
    """API key scope information."""
    
    org_id: UUID | None = Field(default=None, description="Organization scope")
    team_id: UUID | None = Field(default=None, description="Team scope")
    user_id: UUID | None = Field(default=None, description="User scope")


class APIKeyCreate(BaseModel):
    """Request to create an API key."""
    
    key_alias: str | None = Field(default=None, max_length=255, description="Key alias/name")
    scope: APIKeyScope = Field(default_factory=APIKeyScope, description="Key scope")
    models: list[str] | None = Field(default=None, description="Allowed models (null = all)")
    blocked_models: list[str] = Field(default_factory=list, description="Blocked models")
    max_budget: Decimal | None = Field(default=None, ge=0, description="Maximum budget")
    tpm_limit: int | None = Field(default=None, ge=0, description="Tokens per minute limit")
    rpm_limit: int | None = Field(default=None, ge=0, description="Requests per minute limit")
    max_parallel_requests: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = Field(default=None, description="Expiration timestamp")
    permissions: list[str] = Field(
        default_factory=lambda: ["chat", "completions", "embeddings"],
        description="API key permissions",
    )


class APIKeyUpdate(BaseModel):
    """Request to update an API key."""
    
    key_alias: str | None = Field(default=None, max_length=255)
    models: list[str] | None = Field(default=None)
    blocked_models: list[str] | None = Field(default=None)
    max_budget: Decimal | None = Field(default=None, ge=0)
    tpm_limit: int | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=0)
    max_parallel_requests: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = Field(default=None)
    is_active: bool | None = Field(default=None)


class APIKeyResponse(BaseModel):
    """API key response (excludes actual key)."""
    
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    key_alias: str | None
    user_id: UUID | None
    team_id: UUID | None
    org_id: UUID | None
    models: list[str] | None
    blocked_models: list[str]
    max_budget: Decimal | None
    spend: Decimal
    tpm_limit: int | None
    rpm_limit: int | None
    max_parallel_requests: int | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    created_by: UUID | None
    permissions: list[str]


class APIKeyWithSecret(BaseModel):
    """API key response that includes the actual key (only returned on creation)."""
    
    api_key: str = Field(..., description="The actual API key (only shown once)")
    key_info: APIKeyResponse = Field(..., description="Key metadata")


class APIKeyListResponse(PaginatedResponse):
    """Paginated list of API keys."""
    
    items: list[APIKeyResponse]


# ========== Error Responses ==========

class ErrorDetail(BaseModel):
    """Error detail model."""
    
    loc: list[str] | None = Field(default=None, description="Error location")
    msg: str = Field(..., description="Error message")
    type: str = Field(..., description="Error type")


class ErrorResponse(BaseModel):
    """Standard error response."""
    
    detail: str | list[ErrorDetail] = Field(..., description="Error details")
    code: str | None = Field(default=None, description="Error code")


# ========== Health & Status ==========

class HealthResponse(BaseModel):
    """Health check response."""
    
    status: str = Field(..., description="Service status")
    version: str = Field(..., description="API version")
    timestamp: datetime = Field(..., description="Current timestamp")


class StatsResponse(BaseModel):
    """Service statistics response."""

    total_requests: int
    total_tokens: int
    total_spend: Decimal
    active_organizations: int
    active_teams: int
    active_users: int
    active_api_keys: int


# ========== Provider Config Schemas ==========

class ProviderConfigCreate(BaseModel):
    """Request to create a provider configuration."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique provider name (e.g., 'openai-prod', 'anthropic-main')",
    )
    provider_type: str = Field(
        ...,
        pattern=r"^(openai|anthropic|azure|bedrock|gemini|cohere|mistral|groq)$",
        description="Provider type",
    )
    api_key: str | None = Field(
        default=None,
        description="API key (will be encrypted at rest)",
    )
    api_base: str | None = Field(
        default=None,
        max_length=500,
        description="Custom API base URL",
    )
    org_id: UUID | None = Field(
        default=None,
        description="Organization ID (null = global provider)",
    )
    is_active: bool = Field(default=True, description="Whether provider is active")
    tpm_limit: int | None = Field(default=None, ge=0, description="Tokens per minute limit")
    rpm_limit: int | None = Field(default=None, ge=0, description="Requests per minute limit")
    settings: dict[str, Any] | None = Field(
        default=None,
        description="Provider-specific settings (region, project_id, etc.)",
    )


class ProviderConfigUpdate(BaseModel):
    """Request to update a provider configuration."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    api_key: str | None = Field(
        default=None,
        description="New API key (will be encrypted at rest)",
    )
    api_base: str | None = Field(default=None, max_length=500)
    is_active: bool | None = Field(default=None)
    tpm_limit: int | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=0)
    settings: dict[str, Any] | None = Field(default=None)


class ProviderConfigResponse(BaseResponse):
    """Provider configuration response."""

    name: str
    provider_type: str
    api_base: str | None
    org_id: UUID | None
    is_active: bool
    tpm_limit: int | None
    rpm_limit: int | None
    settings: dict[str, Any]
    # Note: api_key is never returned for security


class ProviderConfigListResponse(PaginatedResponse):
    """Paginated list of provider configurations."""

    items: list[ProviderConfigResponse]


class ProviderHealthResponse(BaseModel):
    """Provider health status response."""

    provider_id: UUID
    name: str
    provider_type: str
    is_active: bool
    is_healthy: bool
    latency_ms: float | None
    last_check: datetime | None
    error_message: str | None = None


class ProviderTestResponse(BaseModel):
    """Provider connectivity test response."""

    success: bool
    latency_ms: float | None
    error_message: str | None = None
    model_list: list[str] | None = None


# ========== Team-Provider Access Schemas ==========


class TeamProviderAccessResponse(BaseModel):
    """Team provider access response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    team_id: UUID
    provider_config_id: UUID
    granted_by: UUID | None
    granted_at: datetime
    team_name: str | None = None
    team_slug: str | None = None


class TeamProviderAccessListResponse(BaseModel):
    """List of teams with access to a provider."""

    items: list[TeamProviderAccessResponse]
    total: int


# ========== Model Deployment Schemas ==========

class ModelDeploymentCreate(BaseModel):
    """Request to create a model deployment.
    
    Supports two modes:
    1. Linked mode: provider_config_id required, uses provider's API key
    2. Standalone mode: provider_config_id=null, requires provider_type and api_key
    """

    model_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Public model name exposed to users (e.g., 'gpt-4o')",
    )
    provider_model: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Actual model identifier at provider (e.g., 'gpt-4o-2024-08-06')",
    )
    provider_config_id: UUID | None = Field(
        default=None,
        description="Reference to provider configuration (null for standalone deployments)",
    )
    # Standalone deployment fields
    provider_type: str | None = Field(
        default=None,
        max_length=50,
        description="Provider type for standalone deployments (openai, anthropic, etc.)",
    )
    model_type: str = Field(
        default=ModelType.CHAT.value,
        max_length=50,
        description="Model type: chat, embedding, image_generation, audio_transcription, audio_speech, rerank, moderation",
    )
    api_key: str | None = Field(
        default=None,
        description="API key for standalone deployments (will be encrypted at rest)",
    )
    api_base: str | None = Field(
        default=None,
        max_length=500,
        description="Custom API base URL for standalone deployments",
    )
    org_id: UUID | None = Field(
        default=None,
        description="Organization ID (null = global deployment)",
    )
    is_active: bool = Field(default=True, description="Whether deployment is active")
    priority: int = Field(
        default=1,
        ge=1,
        le=100,
        description="Routing priority (higher = preferred)",
    )
    tpm_limit: int | None = Field(
        default=None,
        ge=0,
        description="Tokens per minute limit (overrides provider)",
    )
    rpm_limit: int | None = Field(
        default=None,
        ge=0,
        description="Requests per minute limit (overrides provider)",
    )
    timeout: float | None = Field(
        default=None,
        ge=1,
        le=600,
        description="Request timeout in seconds",
    )
    settings: dict[str, Any] | None = Field(
        default=None,
        description="Deployment-specific settings",
    )

    @model_validator(mode='after')
    def validate_deployment_mode(self) -> 'ModelDeploymentCreate':
        """Validate deployment mode and model_type."""
        if self.provider_config_id is None:
            # Standalone mode requires provider_type
            if not self.provider_type:
                raise ValueError("provider_type is required for standalone deployments (when provider_config_id is null)")
            # Standalone mode requires api_key
            if not self.api_key:
                raise ValueError("api_key is required for standalone deployments (when provider_config_id is null)")
        # Validate model_type is valid
        valid_types = ModelType.values()
        if self.model_type not in valid_types:
            raise ValueError(f"model_type must be one of: {', '.join(valid_types)}")
        return self


class ModelDeploymentUpdate(BaseModel):
    """Request to update a model deployment."""

    model_name: str | None = Field(default=None, min_length=1, max_length=255)
    provider_model: str | None = Field(default=None, min_length=1, max_length=255)
    # Can switch between linked and standalone modes
    provider_config_id: UUID | None = Field(
        default=None,
        description="Reference to provider configuration (null for standalone)",
    )
    provider_type: str | None = Field(default=None, max_length=50)
    model_type: str | None = Field(
        default=None,
        max_length=50,
        description="Model type: chat, embedding, image_generation, audio_transcription, audio_speech, rerank, moderation",
    )
    api_key: str | None = Field(
        default=None,
        description="New API key for standalone deployments (will be encrypted)",
    )
    api_base: str | None = Field(default=None, max_length=500)
    is_active: bool | None = Field(default=None)
    priority: int | None = Field(default=None, ge=1, le=100)
    tpm_limit: int | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=0)
    timeout: float | None = Field(default=None, ge=1, le=600)
    settings: dict[str, Any] | None = Field(default=None)


class ModelDeploymentResponse(BaseResponse):
    """Model deployment response."""

    model_name: str
    provider_model: str
    provider_config_id: UUID | None
    provider_type: str | None  # Now can be set at deployment level
    model_type: str  # Model type classification
    api_base: str | None  # Now can be set at deployment level
    org_id: UUID | None
    is_active: bool
    priority: int
    tpm_limit: int | None
    rpm_limit: int | None
    timeout: float | None
    settings: dict[str, Any]
    # Include provider info for convenience (for linked deployments)
    provider_name: str | None = None
    # Note: api_key is never returned for security


class ModelDeploymentListResponse(PaginatedResponse):
    """Paginated list of model deployments."""

    items: list[ModelDeploymentResponse]


class ModelDeploymentWithProvider(ModelDeploymentResponse):
    """Model deployment response with full provider details."""

    provider: ProviderConfigResponse | None = None
