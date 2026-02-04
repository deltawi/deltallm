"""Database module for ProxyLLM.

This module provides database models, session management, and utilities
for the multi-tenant RBAC system with Organizations, Teams, and Users.
"""

from deltallm.db.base import Base, TimestampMixin, UUIDMixin
from deltallm.db.models import (
    Organization,
    Team,
    User,
    OrgMember,
    TeamMember,
    Role,
    Permission,
    RolePermission,
    APIKey,
    APIKeyPermission,
    SpendLog,
    AuditLog,
)
from deltallm.db.session import (
    AsyncSession,
    get_session,
    init_db,
    close_db,
)

__all__ = [
    # Base
    "Base",
    "TimestampMixin",
    "UUIDMixin",
    # Models
    "Organization",
    "Team",
    "User",
    "OrgMember",
    "TeamMember",
    "Role",
    "Permission",
    "RolePermission",
    "APIKey",
    "APIKeyPermission",
    "SpendLog",
    "AuditLog",
    # Session
    "AsyncSession",
    "get_session",
    "init_db",
    "close_db",
]
