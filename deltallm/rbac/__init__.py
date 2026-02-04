"""RBAC (Role-Based Access Control) module for ProxyLLM.

This module provides permission management, role assignment, and access control
for the multi-tenant organization/team/user hierarchy.
"""

from deltallm.rbac.audit import AuditLogger
from deltallm.rbac.manager import RBACManager
from deltallm.rbac.permissions import Permission, PermissionChecker, require_permission
from deltallm.rbac.exceptions import (
    RBACError,
    PermissionDeniedError,
    RoleNotFoundError,
    UserNotInOrganizationError,
)

__all__ = [
    # Manager
    "RBACManager",
    # Audit
    "AuditLogger",
    # Permissions
    "Permission",
    "PermissionChecker",
    "require_permission",
    # Exceptions
    "RBACError",
    "PermissionDeniedError",
    "RoleNotFoundError",
    "UserNotInOrganizationError",
]
