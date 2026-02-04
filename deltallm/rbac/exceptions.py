"""RBAC-related exceptions."""


class RBACError(Exception):
    """Base exception for RBAC errors."""
    
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code


class PermissionDeniedError(RBACError):
    """Raised when a user doesn't have the required permission.
    
    HTTP Status: 403 Forbidden
    """
    
    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, code="PERMISSION_DENIED")


class RoleNotFoundError(RBACError):
    """Raised when a role is not found.
    
    HTTP Status: 404 Not Found
    """
    
    def __init__(self, role_id: str | None = None, role_name: str | None = None):
        if role_id:
            message = f"Role not found: {role_id}"
        elif role_name:
            message = f"Role not found: {role_name}"
        else:
            message = "Role not found"
        super().__init__(message, code="ROLE_NOT_FOUND")


class UserNotInOrganizationError(RBACError):
    """Raised when a user is not a member of an organization.
    
    HTTP Status: 403 Forbidden
    """
    
    def __init__(self, user_id: str, org_id: str):
        super().__init__(
            f"User {user_id} is not a member of organization {org_id}",
            code="USER_NOT_IN_ORG"
        )


class UserNotInTeamError(RBACError):
    """Raised when a user is not a member of a team.
    
    HTTP Status: 403 Forbidden
    """
    
    def __init__(self, user_id: str, team_id: str):
        super().__init__(
            f"User {user_id} is not a member of team {team_id}",
            code="USER_NOT_IN_TEAM"
        )


class OrganizationNotFoundError(RBACError):
    """Raised when an organization is not found.
    
    HTTP Status: 404 Not Found
    """
    
    def __init__(self, org_id: str | None = None, slug: str | None = None):
        if org_id:
            message = f"Organization not found: {org_id}"
        elif slug:
            message = f"Organization not found: {slug}"
        else:
            message = "Organization not found"
        super().__init__(message, code="ORG_NOT_FOUND")


class TeamNotFoundError(RBACError):
    """Raised when a team is not found.
    
    HTTP Status: 404 Not Found
    """
    
    def __init__(self, team_id: str | None = None):
        if team_id:
            message = f"Team not found: {team_id}"
        else:
            message = "Team not found"
        super().__init__(message, code="TEAM_NOT_FOUND")


class DuplicateRoleError(RBACError):
    """Raised when trying to create a role that already exists.
    
    HTTP Status: 409 Conflict
    """
    
    def __init__(self, role_name: str):
        super().__init__(f"Role already exists: {role_name}", code="DUPLICATE_ROLE")


class SystemRoleModificationError(RBACError):
    """Raised when trying to modify a system role.
    
    HTTP Status: 403 Forbidden
    """
    
    def __init__(self, role_name: str):
        super().__init__(
            f"Cannot modify system role: {role_name}",
            code="SYSTEM_ROLE_IMMUTABLE"
        )
