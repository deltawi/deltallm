"""Database models for ProxyLLM.

This module defines all SQLAlchemy models for the multi-tenant RBAC system:
- Organizations: Top-level entities that contain teams and users
- Teams: Groups within organizations
- Users: Individual users who can belong to multiple orgs/teams
- RBAC: Roles and permissions for access control
- API Keys: Scoped to org/team/user with permissions
- Audit Logs: For compliance and tracking
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deltallm.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class Organization(Base, UUIDMixin, TimestampMixin):
    """Organization model - top-level entity for multi-tenancy.
    
    Organizations contain teams, users, and API keys. Budget and settings
    are managed at the organization level.
    """
    
    __tablename__ = "organizations"
    
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Organization display name",
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        comment="URL-friendly unique identifier",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Optional organization description",
    )
    max_budget: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 4),
        nullable=True,
        comment="Maximum budget limit for the organization",
    )
    spend: Mapped[Decimal] = mapped_column(
        Numeric(15, 4),
        default=Decimal("0"),
        nullable=False,
        comment="Current spend amount",
    )
    settings: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        comment="Organization-specific settings",
    )
    
    # Relationships
    teams: Mapped[List["Team"]] = relationship(
        "Team",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    members: Mapped[List["OrgMember"]] = relationship(
        "OrgMember",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    roles: Mapped[List["Role"]] = relationship(
        "Role",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    
    def __repr__(self) -> str:
        return f"<Organization(id={self.id}, slug={self.slug}, name={self.name})>"


class Team(Base, UUIDMixin, TimestampMixin):
    """Team model - groups within organizations.
    
    Teams allow for finer-grained access control and budget management
    within an organization.
    """
    
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_team_org_slug"),
    )
    
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Team display name",
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="URL-friendly identifier (unique within org)",
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        comment="Parent organization ID",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Optional team description",
    )
    max_budget: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 4),
        nullable=True,
        comment="Maximum budget limit for the team",
    )
    spend: Mapped[Decimal] = mapped_column(
        Numeric(15, 4),
        default=Decimal("0"),
        nullable=False,
        comment="Current spend amount",
    )
    settings: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        comment="Team-specific settings",
    )
    
    # Relationships
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="teams",
    )
    members: Mapped[List["TeamMember"]] = relationship(
        "TeamMember",
        back_populates="team",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey",
        back_populates="team",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    
    def __repr__(self) -> str:
        return f"<Team(id={self.id}, slug={self.slug}, org_id={self.org_id})>"


class User(Base, UUIDMixin, TimestampMixin):
    """User model - individual users of the system.
    
    Users can belong to multiple organizations and teams. Authentication
    is handled via email/password or external providers.
    """
    
    __tablename__ = "users"
    
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        comment="User email address (unique)",
    )
    password_hash: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Hashed password (nullable for OAuth users)",
    )
    first_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="User's first name",
    )
    last_name: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="User's last name",
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Superuser flag (system-wide admin)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Account active status",
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Last successful login timestamp",
    )
    
    # Relationships
    org_memberships: Mapped[List["OrgMember"]] = relationship(
        "OrgMember",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    team_memberships: Mapped[List["TeamMember"]] = relationship(
        "TeamMember",
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey",
        back_populates="user",
        foreign_keys="APIKey.user_id",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    created_api_keys: Mapped[List["APIKey"]] = relationship(
        "APIKey",
        back_populates="created_by_user",
        foreign_keys="APIKey.created_by",
        lazy="selectin",
    )
    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="user",
        lazy="selectin",
    )
    
    @property
    def full_name(self) -> str:
        """Return user's full name."""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        return self.first_name or self.last_name or self.email
    
    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email})>"


class OrgMember(Base, UUIDMixin):
    """Organization membership model.
    
    Links users to organizations with a specific role.
    """
    
    __tablename__ = "org_members"
    __table_args__ = (
        UniqueConstraint("user_id", "org_id", name="uq_org_member_user_org"),
    )
    
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="User ID",
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        comment="Organization ID",
    )
    role: Mapped[str] = mapped_column(
        String(50),
        default="member",
        nullable=False,
        comment="Organization role (owner, admin, member, viewer)",
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="When the user joined the organization",
    )
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="org_memberships")
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="members",
    )
    
    def __repr__(self) -> str:
        return f"<OrgMember(user_id={self.user_id}, org_id={self.org_id}, role={self.role})>"


class TeamMember(Base, UUIDMixin):
    """Team membership model.
    
    Links users to teams with a specific role.
    """
    
    __tablename__ = "team_members"
    __table_args__ = (
        UniqueConstraint("user_id", "team_id", name="uq_team_member_user_team"),
    )
    
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="User ID",
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        comment="Team ID",
    )
    role: Mapped[str] = mapped_column(
        String(50),
        default="member",
        nullable=False,
        comment="Team role (admin, member)",
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="When the user joined the team",
    )
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="team_memberships")
    team: Mapped["Team"] = relationship("Team", back_populates="members")
    
    def __repr__(self) -> str:
        return f"<TeamMember(user_id={self.user_id}, team_id={self.team_id}, role={self.role})>"


class Role(Base, UUIDMixin, TimestampMixin):
    """Role model for RBAC.
    
    Roles can be system-wide (is_system=True, org_id=None) or
    organization-specific (org_id set).
    """
    
    __tablename__ = "roles"
    
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Role name",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Role description",
    )
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        comment="Organization ID (NULL for system roles)",
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="Whether this is a built-in system role",
    )
    
    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="roles",
    )
    permissions: Mapped[List["Permission"]] = relationship(
        "Permission",
        secondary="role_permissions",
        back_populates="roles",
        lazy="selectin",
    )
    
    def __repr__(self) -> str:
        return f"<Role(id={self.id}, name={self.name}, is_system={self.is_system})>"


class Permission(Base, UUIDMixin):
    """Permission model for RBAC.
    
    Permissions define what actions can be performed on what resources.
    Format: resource:action (e.g., "api_key:create", "model:use")
    """
    
    __tablename__ = "permissions"
    __table_args__ = (
        UniqueConstraint("resource", "action", name="uq_permission_resource_action"),
    )
    
    resource: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Resource type (e.g., api_key, model, user, team)",
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Action (e.g., create, read, update, delete, use)",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Permission description",
    )
    
    # Relationships
    roles: Mapped[List["Role"]] = relationship(
        "Role",
        secondary="role_permissions",
        back_populates="permissions",
        lazy="selectin",
    )
    
    @property
    def full_name(self) -> str:
        """Return full permission name in resource:action format."""
        return f"{self.resource}:{self.action}"
    
    def __repr__(self) -> str:
        return f"<Permission(id={self.id}, name={self.full_name})>"


# Association table for Role <-> Permission many-to-many relationship
class RolePermission(Base):
    """Association table linking roles to permissions."""
    
    __tablename__ = "role_permissions"
    
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )


class APIKey(Base, UUIDMixin, TimestampMixin):
    """API Key model with org/team/user scoping.
    
    API keys can be scoped to:
    - Organization: counts against org budget
    - Team: counts against team budget
    - User: counts against user budget
    """
    
    __tablename__ = "api_keys"
    
    key_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        comment="SHA-256 hash of the API key",
    )
    key_alias: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Human-readable alias for the key",
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        comment="User ID (for user-scoped keys)",
    )
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=True,
        comment="Team ID (for team-scoped keys)",
    )
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=True,
        comment="Organization ID (for org-scoped keys)",
    )
    models: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String),
        nullable=True,
        comment="Allowed models (NULL = all)",
    )
    blocked_models: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String),
        nullable=True,
        default=list,
        comment="Explicitly blocked models",
    )
    max_budget: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 4),
        nullable=True,
        comment="Maximum budget for this key",
    )
    spend: Mapped[Decimal] = mapped_column(
        Numeric(15, 4),
        default=Decimal("0"),
        nullable=False,
        comment="Current spend for this key",
    )
    tpm_limit: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Tokens per minute limit",
    )
    rpm_limit: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Requests per minute limit",
    )
    max_parallel_requests: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Maximum concurrent requests",
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Key expiration timestamp",
    )
    key_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
        comment="Additional key metadata",
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        comment="User who created this key",
    )
    
    # Relationships
    user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="api_keys",
        foreign_keys=[user_id],
    )
    team: Mapped[Optional["Team"]] = relationship(
        "Team",
        back_populates="api_keys",
    )
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="api_keys",
    )
    created_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="created_api_keys",
        foreign_keys=[created_by],
    )
    permissions: Mapped[List["APIKeyPermission"]] = relationship(
        "APIKeyPermission",
        back_populates="api_key",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    
    def __repr__(self) -> str:
        return f"<APIKey(id={self.id}, alias={self.key_alias}, org_id={self.org_id})>"


class APIKeyPermission(Base):
    """Granular permissions for API keys.
    
    Defines what endpoints/features an API key can access.
    """
    
    __tablename__ = "api_key_permissions"
    
    key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission: Mapped[str] = mapped_column(
        String(100),
        primary_key=True,
        comment="Permission (e.g., chat, embeddings, images)",
    )
    
    # Relationships
    api_key: Mapped["APIKey"] = relationship(
        "APIKey",
        back_populates="permissions",
    )


class SpendLog(Base, UUIDMixin):
    """Spend log for tracking API usage and costs.
    
    Records every request with usage, cost, and context for analytics.
    """
    
    __tablename__ = "spend_logs"
    
    request_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Unique request identifier",
    )
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id"),
        nullable=True,
        index=True,
        comment="API key used for the request",
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment="User who made the request",
    )
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id"),
        nullable=True,
        index=True,
        comment="Team context for the request",
    )
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
        comment="Organization context for the request",
    )
    model: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Model used for the request",
    )
    endpoint_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="chat",
        index=True,
        comment="Endpoint type (chat, embedding, audio_speech, audio_transcription, image, rerank, moderation, batch)",
    )
    provider: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Provider that served the request",
    )
    prompt_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of prompt tokens",
    )
    completion_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of completion tokens",
    )
    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Total tokens used",
    )
    # Fields for non-token based endpoints
    audio_seconds: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 3),
        nullable=True,
        comment="Audio duration in seconds (for STT)",
    )
    audio_characters: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Character count (for TTS)",
    )
    image_count: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of images generated",
    )
    image_size: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        comment="Image size (e.g., 1024x1024)",
    )
    rerank_searches: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Number of rerank searches",
    )
    spend: Mapped[Decimal] = mapped_column(
        Numeric(20, 12),
        nullable=False,
        comment="Cost of the request in USD (12 decimal places for per-token precision)",
    )
    latency_ms: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 3),
        nullable=True,
        comment="Request latency in milliseconds",
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Request status (success, failure)",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if request failed",
    )
    request_tags: Mapped[Optional[List[str]]] = mapped_column(
        ARRAY(String),
        nullable=True,
        comment="Tags associated with the request",
    )
    request_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
        comment="Additional request metadata",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
        comment="When the request was made",
    )
    
    def __repr__(self) -> str:
        return f"<SpendLog(id={self.id}, request_id={self.request_id}, endpoint={self.endpoint_type}, spend={self.spend})>"


class AuditLog(Base, UUIDMixin):
    """Audit log for compliance and security.

    Records important actions for audit trails.
    """

    __tablename__ = "audit_logs"

    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=True,
        index=True,
        comment="Organization context",
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        index=True,
        comment="User who performed the action",
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="Action performed (e.g., create_key, update_team)",
    )
    resource_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="Type of resource affected",
    )
    resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="ID of resource affected",
    )
    old_values: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="Previous values (for updates)",
    )
    new_values: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
        comment="New values (for creates/updates)",
    )
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),  # IPv6 max length
        nullable=True,
        comment="Client IP address",
    )
    user_agent: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Client user agent",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        index=True,
        comment="When the action occurred",
    )

    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="audit_logs",
    )
    user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="audit_logs",
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action}, org_id={self.org_id})>"


class ProviderConfig(Base, UUIDMixin, TimestampMixin):
    """Provider configuration for LLM endpoints.

    Stores credentials and settings for external LLM providers.
    Providers are global by default (org_id = NULL) but can be
    scoped to specific organizations.
    """

    __tablename__ = "provider_configs"
    __table_args__ = (
        UniqueConstraint("name", "org_id", name="uq_provider_name_org"),
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Unique provider name (e.g., 'openai-prod', 'anthropic-main')",
    )
    provider_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        comment="Provider type (openai, anthropic, azure, bedrock, gemini, cohere, mistral, groq)",
    )
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Encrypted API key (Fernet encryption)",
    )
    api_base: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Custom API base URL (for custom endpoints or proxies)",
    )
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Organization ID (NULL = global provider)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether this provider is active",
    )
    tpm_limit: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Tokens per minute limit for this provider",
    )
    rpm_limit: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Requests per minute limit for this provider",
    )
    settings: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        comment="Provider-specific settings (e.g., region, project_id)",
    )

    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        backref="provider_configs",
    )
    deployments: Mapped[List["ModelDeployment"]] = relationship(
        "ModelDeployment",
        back_populates="provider_config",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<ProviderConfig(id={self.id}, name={self.name}, type={self.provider_type})>"


class ModelDeployment(Base, UUIDMixin, TimestampMixin):
    """Model deployment mapping models to providers.

    Maps public model names to specific provider configurations
    and actual model identifiers. Enables routing, load balancing,
    and failover across providers.
    
    Note: Now supports standalone deployments with direct API key storage,
    making provider_config_id optional for LiteLLM-style model management.
    """

    __tablename__ = "model_deployments"
    __table_args__ = (
        UniqueConstraint("model_name", "provider_config_id", name="uq_deployment_model_provider"),
    )

    model_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Public model name exposed to users (e.g., 'gpt-4o')",
    )
    provider_model: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Actual model identifier at provider (e.g., 'gpt-4o-2024-08-06')",
    )
    provider_config_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_configs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Reference to provider configuration (NULL = standalone deployment)",
    )
    # Standalone deployment fields (for LiteLLM-style model management)
    provider_type: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        index=True,
        comment="Provider type for standalone deployments (openai, anthropic, azure, etc.)",
    )
    model_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="chat",
        index=True,
        comment="Model type (chat, embedding, image_generation, audio_transcription, audio_speech, rerank, moderation)",
    )
    api_key_encrypted: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Encrypted API key for standalone deployments (Fernet encryption)",
    )
    api_base: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Custom API base URL for standalone deployments",
    )
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Organization ID (NULL = global deployment)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        comment="Whether this deployment is active",
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
        comment="Routing priority (higher = preferred)",
    )
    tpm_limit: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Tokens per minute limit (overrides provider)",
    )
    rpm_limit: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Requests per minute limit (overrides provider)",
    )
    timeout: Mapped[Optional[float]] = mapped_column(
        Numeric(10, 2),
        nullable=True,
        comment="Request timeout in seconds",
    )
    settings: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        comment="Deployment-specific settings",
    )
    pricing_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_pricing.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Reference to pricing configuration",
    )

    # Relationships
    provider_config: Mapped[Optional["ProviderConfig"]] = relationship(
        "ProviderConfig",
        back_populates="deployments",
    )
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        backref="model_deployments",
    )
    pricing: Mapped[Optional["ModelPricing"]] = relationship(
        "ModelPricing",
        backref="deployments",
        lazy="joined",
    )

    def __repr__(self) -> str:
        return f"<ModelDeployment(id={self.id}, model={self.model_name}, provider_model={self.provider_model})>"


class TeamProviderAccess(Base, UUIDMixin):
    """Team-provider access control.

    Links teams to providers they're allowed to use. This enables
    fine-grained access control where org admins can grant specific
    teams access to specific provider configurations.
    """

    __tablename__ = "team_provider_access"
    __table_args__ = (
        UniqueConstraint("team_id", "provider_config_id", name="uq_team_provider_access"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Team ID",
    )
    provider_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("provider_configs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Provider configuration ID",
    )
    granted_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="User who granted this access",
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
        comment="When access was granted",
    )

    # Relationships
    team: Mapped["Team"] = relationship(
        "Team",
        backref="provider_access",
    )
    provider_config: Mapped["ProviderConfig"] = relationship(
        "ProviderConfig",
        backref="team_access",
    )
    granted_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        backref="granted_provider_access",
    )

    def __repr__(self) -> str:
        return f"<TeamProviderAccess(team_id={self.team_id}, provider_config_id={self.provider_config_id})>"


class FileObject(Base, UUIDMixin, TimestampMixin):
    """File object for batch processing.
    
    Stores files uploaded for batch jobs. Files are stored as bytes
    and can be retrieved or deleted when no longer needed.
    """
    
    __tablename__ = "file_objects"
    
    bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Size of the file in bytes",
    )
    purpose: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Purpose of the file (batch, fine-tune, etc.)",
    )
    filename: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Original filename",
    )
    content_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
        comment="MIME type of the file",
    )
    content: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
        comment="File content as bytes",
    )
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Organization ID (NULL = global)",
    )
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id"),
        nullable=True,
        index=True,
        comment="API key that created this file",
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        comment="User who created this file",
    )
    
    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        backref="files",
    )
    api_key: Mapped[Optional["APIKey"]] = relationship(
        "APIKey",
        backref="files",
    )
    
    def __repr__(self) -> str:
        return f"<FileObject(id={self.id}, filename={self.filename}, bytes={self.bytes})>"


class ModelPricing(Base, UUIDMixin, TimestampMixin):
    """Custom pricing overrides for models.

    Stores custom pricing configurations that override YAML defaults.
    Supports global, org-level, and team-level pricing scopes.
    """

    __tablename__ = "model_pricing"
    __table_args__ = (
        UniqueConstraint("model_name", "org_id", "team_id", name="uq_model_pricing_scope"),
    )

    model_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        comment="Model name (e.g., 'gpt-4o', 'claude-3-opus')",
    )
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Organization ID (NULL = global pricing)",
    )
    team_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Team ID (NULL = org-level or global pricing)",
    )

    # Pricing mode
    mode: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="chat",
        comment="Pricing mode (chat, embedding, image_generation, audio_speech, audio_transcription, rerank, moderation, batch)",
    )

    # Token-based pricing
    input_cost_per_token: Mapped[Decimal] = mapped_column(
        Numeric(20, 12),
        nullable=False,
        default=Decimal("0"),
        comment="Cost per input token",
    )
    output_cost_per_token: Mapped[Decimal] = mapped_column(
        Numeric(20, 12),
        nullable=False,
        default=Decimal("0"),
        comment="Cost per output token",
    )

    # Prompt caching
    cache_creation_input_token_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 12),
        nullable=True,
        comment="Cost to create cached tokens",
    )
    cache_read_input_token_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 12),
        nullable=True,
        comment="Cost to read cached tokens",
    )

    # Image generation
    image_cost_per_image: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 12),
        nullable=True,
        comment="Cost per image (if not using size-based pricing)",
    )
    image_sizes: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Image size pricing map (e.g., {'1024x1024': '0.04'})",
    )
    quality_pricing: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="Quality multipliers (e.g., {'hd': 2.0})",
    )

    # Audio pricing
    audio_cost_per_character: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 12),
        nullable=True,
        comment="TTS cost per character",
    )
    audio_cost_per_minute: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 12),
        nullable=True,
        comment="STT cost per minute",
    )

    # Rerank pricing
    rerank_cost_per_search: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(20, 12),
        nullable=True,
        comment="Cost per rerank search",
    )

    # Batch settings
    batch_discount_percent: Mapped[float] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=50.0,
        comment="Batch discount percentage",
    )
    base_model: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="Base model for pricing inheritance",
    )

    # Limits
    max_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Maximum context window",
    )
    max_input_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Maximum input tokens",
    )
    max_output_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Maximum output tokens",
    )

    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        backref="model_pricing",
    )
    team: Mapped[Optional["Team"]] = relationship(
        "Team",
        backref="model_pricing",
    )

    def __repr__(self) -> str:
        scope = "global"
        if self.team_id:
            scope = f"team:{self.team_id}"
        elif self.org_id:
            scope = f"org:{self.org_id}"
        return f"<ModelPricing(model={self.model_name}, scope={scope})>"


class BatchJob(Base, UUIDMixin, TimestampMixin):
    """Batch job for processing multiple requests.
    
    Represents a batch job that processes multiple API requests
    with 50% discount on pricing. Tracks status, files, and cost.
    """
    
    __tablename__ = "batch_jobs"
    
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="validating",
        index=True,
        comment="Job status: validating, in_progress, finalizing, completed, failed, cancelled, expired",
    )
    endpoint: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="Endpoint to use for batch requests (e.g., /v1/chat/completions)",
    )
    completion_window: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="24h",
        comment="Time window for completion (e.g., 24h)",
    )
    
    # File references
    input_file_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="ID of the input file",
    )
    output_file_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="ID of the output file (generated after completion)",
    )
    error_file_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        comment="ID of the error file (if any requests failed)",
    )
    
    # Cost tracking
    original_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 6),
        nullable=True,
        comment="Estimated cost before discount",
    )
    discounted_cost: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 6),
        nullable=True,
        comment="Actual cost after 50% discount",
    )
    total_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Total tokens used across all requests",
    )
    prompt_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Total prompt tokens across all requests",
    )
    completion_tokens: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Total completion tokens across all requests",
    )
    
    # Request counts
    total_requests: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        comment="Total number of requests in the batch",
    )
    completed_requests: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=0,
        comment="Number of successfully completed requests",
    )
    failed_requests: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
        default=0,
        comment="Number of failed requests",
    )
    
    # Relationships
    org_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="Organization ID",
    )
    api_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("api_keys.id"),
        nullable=True,
        index=True,
        comment="API key that created this batch",
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
        comment="User who created this batch",
    )
    
    # Metadata
    batch_metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=False,
        comment="Additional batch metadata",
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        comment="Error message if batch failed",
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the batch job expires",
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the batch job completed",
    )
    
    # Relationships
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        backref="batch_jobs",
    )
    api_key: Mapped[Optional["APIKey"]] = relationship(
        "APIKey",
        backref="batch_jobs",
    )
    
    def __repr__(self) -> str:
        return f"<BatchJob(id={self.id}, status={self.status}, endpoint={self.endpoint})>"
