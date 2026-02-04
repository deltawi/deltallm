"""Permission definitions and checking utilities for RBAC.

This module defines all available permissions and provides utilities
for checking permissions in code and as decorators.
"""

from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class Permission:
    """A permission is defined by a resource and an action.
    
    Format: resource:action (e.g., "api_key:create", "model:use")
    """
    resource: str
    action: str
    
    def __str__(self) -> str:
        return f"{self.resource}:{self.action}"
    
    def __repr__(self) -> str:
        return f"Permission({self.resource}, {self.action})"
    
    def matches(self, pattern: str) -> bool:
        """Check if this permission matches a pattern.
        
        Patterns can use wildcards:
        - "*:*" matches all permissions
        - "api_key:*" matches all api_key actions
        - "*:create" matches create action on any resource
        
        Args:
            pattern: Permission pattern to match against.
            
        Returns:
            True if this permission matches the pattern.
        """
        if pattern == "*:*":
            return True
        
        parts = pattern.split(":")
        if len(parts) != 2:
            return False
        
        res_pattern, act_pattern = parts
        
        res_match = res_pattern == "*" or res_pattern == self.resource
        act_match = act_pattern == "*" or act_pattern == self.action
        
        return res_match and act_match


class PermissionChecker:
    """Permission constants and checking utilities.
    
    This class provides:
    1. Permission constants for all resources and actions
    2. Methods to check if a user has a permission
    3. Methods to filter permissions by resource
    """
    
    # ========== API Key Permissions ==========
    API_KEY_CREATE = Permission("api_key", "create")
    API_KEY_READ = Permission("api_key", "read")
    API_KEY_UPDATE = Permission("api_key", "update")
    API_KEY_DELETE = Permission("api_key", "delete")
    API_KEY_USE = Permission("api_key", "use")
    
    # ========== Model Permissions ==========
    MODEL_USE = Permission("model", "use")
    MODEL_CREATE = Permission("model", "create")
    MODEL_READ = Permission("model", "read")
    MODEL_UPDATE = Permission("model", "update")
    MODEL_DELETE = Permission("model", "delete")
    
    # ========== User Permissions ==========
    USER_CREATE = Permission("user", "create")
    USER_READ = Permission("user", "read")
    USER_UPDATE = Permission("user", "update")
    USER_DELETE = Permission("user", "delete")
    
    # ========== Team Permissions ==========
    TEAM_CREATE = Permission("team", "create")
    TEAM_READ = Permission("team", "read")
    TEAM_UPDATE = Permission("team", "update")
    TEAM_DELETE = Permission("team", "delete")
    TEAM_MANAGE_MEMBERS = Permission("team", "manage_members")
    
    # ========== Organization Permissions ==========
    ORG_CREATE = Permission("org", "create")
    ORG_READ = Permission("org", "read")
    ORG_UPDATE = Permission("org", "update")
    ORG_DELETE = Permission("org", "delete")
    ORG_MANAGE_MEMBERS = Permission("org", "manage_members")
    ORG_MANAGE_BILLING = Permission("org", "manage_billing")
    ORG_MANAGE_SETTINGS = Permission("org", "manage_settings")
    
    # ========== Role Permissions ==========
    ROLE_CREATE = Permission("role", "create")
    ROLE_READ = Permission("role", "read")
    ROLE_UPDATE = Permission("role", "update")
    ROLE_DELETE = Permission("role", "delete")
    ROLE_ASSIGN = Permission("role", "assign")
    
    # ========== Audit Permissions ==========
    AUDIT_READ = Permission("audit", "read")
    
    # ========== Spend/Budget Permissions ==========
    SPEND_READ = Permission("spend", "read")
    BUDGET_MANAGE = Permission("budget", "manage")
    
    @classmethod
    def all_permissions(cls) -> list[Permission]:
        """Get all defined permissions."""
        return [
            # API Key
            cls.API_KEY_CREATE,
            cls.API_KEY_READ,
            cls.API_KEY_UPDATE,
            cls.API_KEY_DELETE,
            cls.API_KEY_USE,
            # Model
            cls.MODEL_USE,
            cls.MODEL_CREATE,
            cls.MODEL_READ,
            cls.MODEL_UPDATE,
            cls.MODEL_DELETE,
            # User
            cls.USER_CREATE,
            cls.USER_READ,
            cls.USER_UPDATE,
            cls.USER_DELETE,
            # Team
            cls.TEAM_CREATE,
            cls.TEAM_READ,
            cls.TEAM_UPDATE,
            cls.TEAM_DELETE,
            cls.TEAM_MANAGE_MEMBERS,
            # Organization
            cls.ORG_CREATE,
            cls.ORG_READ,
            cls.ORG_UPDATE,
            cls.ORG_DELETE,
            cls.ORG_MANAGE_MEMBERS,
            cls.ORG_MANAGE_BILLING,
            cls.ORG_MANAGE_SETTINGS,
            # Role
            cls.ROLE_CREATE,
            cls.ROLE_READ,
            cls.ROLE_UPDATE,
            cls.ROLE_DELETE,
            cls.ROLE_ASSIGN,
            # Audit
            cls.AUDIT_READ,
            # Spend/Budget
            cls.SPEND_READ,
            cls.BUDGET_MANAGE,
        ]
    
    @classmethod
    def get_permissions_for_resource(cls, resource: str) -> list[Permission]:
        """Get all permissions for a specific resource."""
        return [p for p in cls.all_permissions() if p.resource == resource]
    
    @classmethod
    def from_string(cls, permission_str: str) -> Permission:
        """Create a Permission from a string like 'api_key:create'.
        
        Args:
            permission_str: Permission string in resource:action format.
            
        Returns:
            Permission object.
            
        Raises:
            ValueError: If the string is not in the correct format.
        """
        parts = permission_str.split(":")
        if len(parts) != 2:
            raise ValueError(f"Invalid permission format: {permission_str}")
        return Permission(parts[0], parts[1])
    
    @staticmethod
    def check_permission(
        user_permissions: list[str],
        required: Permission | str,
    ) -> bool:
        """Check if a list of permissions includes the required permission.
        
        Args:
            user_permissions: List of permission strings the user has.
            required: The permission required (can be Permission object or string).
            
        Returns:
            True if the user has the required permission.
        """
        if isinstance(required, str):
            required = PermissionChecker.from_string(required)
        
        for perm_str in user_permissions:
            # Check for wildcard (all permissions)
            if perm_str == "*:*":
                return True
            
            # Check if user's permission matches required
            if required.matches(perm_str):
                return True
        
        return False


class PermissionScope(Enum):
    """Scope levels for permission checking."""
    SYSTEM = "system"          # System-wide (superuser)
    ORGANIZATION = "org"       # Organization level
    TEAM = "team"              # Team level
    OWN = "own"                # Own resources only


def require_permission(
    permission: Permission | str,
    scope: PermissionScope = PermissionScope.ORGANIZATION,
    get_org_id: Optional[Callable[..., Any]] = None,
    get_team_id: Optional[Callable[..., Any]] = None,
) -> Callable:
    """Decorator to require a permission for an endpoint/function.
    
    This decorator checks if the current user has the required permission
    before allowing access to the decorated function.
    
    Args:
        permission: The required permission.
        scope: The scope level to check at.
        get_org_id: Optional function to extract org_id from args/kwargs.
        get_team_id: Optional function to extract team_id from args/kwargs.
        
    Returns:
        Decorator function.
        
    Example:
        @require_permission(PermissionChecker.API_KEY_CREATE)
        async def create_api_key(request: Request):
            ...
            
        @require_permission(
            PermissionChecker.TEAM_UPDATE,
            get_org_id=lambda *a, **k: k.get('org_id'),
        )
        async def update_team(org_id: UUID, team_id: UUID, ...):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            from deltallm.rbac.exceptions import PermissionDeniedError
            
            # TODO: Get current user from request context
            # For now, this is a placeholder - in real usage,
            # this would extract user info from FastAPI request
            current_user = kwargs.get('current_user')
            
            if current_user is None:
                # Try to find user in args (assuming first arg might be request)
                for arg in args:
                    if hasattr(arg, 'user'):
                        current_user = arg.user
                        break
            
            if current_user is None:
                raise PermissionDeniedError("Authentication required")
            
            # Check if user has permission
            # TODO: Integrate with RBACManager to check actual permissions
            # For now, just check superuser
            if getattr(current_user, 'is_superuser', False):
                return await func(*args, **kwargs)
            
            # TODO: Full permission check against database
            # This would involve:
            # 1. Get user's roles in the organization/team
            # 2. Get permissions for those roles
            # 3. Check if required permission is in the list
            
            raise PermissionDeniedError(f"Permission denied: {permission}")
        
        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            from deltallm.rbac.exceptions import PermissionDeniedError
            
            # Similar logic for sync functions
            current_user = kwargs.get('current_user')
            
            if current_user is None:
                for arg in args:
                    if hasattr(arg, 'user'):
                        current_user = arg.user
                        break
            
            if current_user is None:
                raise PermissionDeniedError("Authentication required")
            
            if getattr(current_user, 'is_superuser', False):
                return func(*args, **kwargs)
            
            raise PermissionDeniedError(f"Permission denied: {permission}")
        
        # Return appropriate wrapper based on whether function is async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator
