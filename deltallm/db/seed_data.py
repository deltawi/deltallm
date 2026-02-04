"""Seed data for system roles and permissions.

This module provides the built-in roles and permissions for the RBAC system.
System roles cannot be deleted and are available across all organizations.
"""

from typing import Any

# System permissions organized by resource
SYSTEM_PERMISSIONS: list[dict[str, Any]] = [
    # API Key permissions
    {"resource": "api_key", "action": "create", "description": "Create new API keys"},
    {"resource": "api_key", "action": "read", "description": "View API key details"},
    {"resource": "api_key", "action": "update", "description": "Update API key settings"},
    {"resource": "api_key", "action": "delete", "description": "Delete API keys"},
    {"resource": "api_key", "action": "use", "description": "Use API keys for requests"},
    
    # Model permissions
    {"resource": "model", "action": "use", "description": "Use models for inference"},
    {"resource": "model", "action": "create", "description": "Add custom model configurations"},
    {"resource": "model", "action": "read", "description": "View model configurations"},
    {"resource": "model", "action": "update", "description": "Update model configurations"},
    {"resource": "model", "action": "delete", "description": "Delete model configurations"},
    
    # User permissions
    {"resource": "user", "action": "create", "description": "Invite/create new users"},
    {"resource": "user", "action": "read", "description": "View user profiles"},
    {"resource": "user", "action": "update", "description": "Update user settings"},
    {"resource": "user", "action": "delete", "description": "Deactivate/delete users"},
    
    # Team permissions
    {"resource": "team", "action": "create", "description": "Create new teams"},
    {"resource": "team", "action": "read", "description": "View team details"},
    {"resource": "team", "action": "update", "description": "Update team settings"},
    {"resource": "team", "action": "delete", "description": "Delete teams"},
    {"resource": "team", "action": "manage_members", "description": "Add/remove team members"},
    
    # Organization permissions
    {"resource": "org", "action": "create", "description": "Create organizations"},
    {"resource": "org", "action": "read", "description": "View organization details"},
    {"resource": "org", "action": "update", "description": "Update organization settings"},
    {"resource": "org", "action": "delete", "description": "Delete organizations"},
    {"resource": "org", "action": "manage_members", "description": "Manage org members and roles"},
    {"resource": "org", "action": "manage_billing", "description": "Manage billing settings"},
    {"resource": "org", "action": "manage_settings", "description": "Manage org-wide settings"},
    
    # Role permissions
    {"resource": "role", "action": "create", "description": "Create custom roles"},
    {"resource": "role", "action": "read", "description": "View roles and permissions"},
    {"resource": "role", "action": "update", "description": "Update custom roles"},
    {"resource": "role", "action": "delete", "description": "Delete custom roles"},
    {"resource": "role", "action": "assign", "description": "Assign roles to users"},
    
    # Audit permissions
    {"resource": "audit", "action": "read", "description": "View audit logs"},
    
    # Spend/Budget permissions
    {"resource": "spend", "action": "read", "description": "View spend reports"},
    {"resource": "budget", "action": "manage", "description": "Manage budgets"},
]

# System roles with their permissions
SYSTEM_ROLES: dict[str, dict[str, Any]] = {
    "superuser": {
        "name": "Superuser",
        "description": "Full system access with all permissions",
        "is_system": True,
        "permissions": ["*:*"],  # Wildcard for all permissions
    },
    "org_owner": {
        "name": "Organization Owner",
        "description": "Full access to organization and all its resources",
        "is_system": True,
        "permissions": [
            # API keys
            "api_key:*",
            # Models
            "model:*",
            # Users
            "user:*",
            # Teams
            "team:*",
            # Organization
            "org:*",
            # Roles
            "role:*",
            # Audit
            "audit:read",
            # Spend/Budget
            "spend:read",
            "budget:manage",
        ],
    },
    "org_admin": {
        "name": "Organization Admin",
        "description": "Can manage most organization resources except billing",
        "is_system": True,
        "permissions": [
            # API keys
            "api_key:create",
            "api_key:read",
            "api_key:update",
            "api_key:delete",
            # Models
            "model:use",
            "model:read",
            "model:update",
            # Users
            "user:create",
            "user:read",
            "user:update",
            # Teams
            "team:create",
            "team:read",
            "team:update",
            "team:delete",
            "team:manage_members",
            # Organization
            "org:read",
            "org:update",
            "org:manage_members",
            # Roles
            "role:create",
            "role:read",
            "role:update",
            "role:delete",
            "role:assign",
            # Audit
            "audit:read",
            # Spend
            "spend:read",
        ],
    },
    "org_member": {
        "name": "Organization Member",
        "description": "Standard organization member with limited management access",
        "is_system": True,
        "permissions": [
            # API keys (own only)
            "api_key:create",
            "api_key:read",
            "api_key:update",
            "api_key:delete",
            # Models
            "model:use",
            "model:read",
            # Users (view only)
            "user:read",
            # Teams (view only)
            "team:read",
            # Organization (view only)
            "org:read",
            # Roles (view only)
            "role:read",
            # Spend (view own only)
            "spend:read",
        ],
    },
    "org_viewer": {
        "name": "Organization Viewer",
        "description": "Read-only access to organization resources",
        "is_system": True,
        "permissions": [
            # API keys (view only)
            "api_key:read",
            # Models
            "model:use",
            "model:read",
            # Users (view only)
            "user:read",
            # Teams (view only)
            "team:read",
            # Organization (view only)
            "org:read",
            # Roles (view only)
            "role:read",
            # Spend (view only)
            "spend:read",
        ],
    },
    "team_admin": {
        "name": "Team Admin",
        "description": "Can manage team resources and members",
        "is_system": True,
        "permissions": [
            # API keys (team-scoped)
            "api_key:create",
            "api_key:read",
            "api_key:update",
            "api_key:delete",
            "api_key:use",
            # Models
            "model:use",
            "model:read",
            # Teams
            "team:read",
            "team:manage_members",
        ],
    },
    "team_member": {
        "name": "Team Member",
        "description": "Can use team resources",
        "is_system": True,
        "permissions": [
            # API keys (use only)
            "api_key:use",
            "api_key:read",
            # Models
            "model:use",
            # Teams (view only)
            "team:read",
        ],
    },
}

# API key permissions (granular endpoint access)
API_KEY_PERMISSIONS: list[str] = [
    "chat",           # Chat completions
    "completions",    # Legacy completions
    "embeddings",     # Text embeddings
    "images",         # Image generation
    "audio",          # Audio (TTS/STT)
    "moderations",    # Content moderation
    "rerank",         # Reranking
    "batch",          # Batch API
    "files",          # File operations
    "fine_tuning",    # Fine-tuning
]


async def seed_permissions(session: Any) -> None:
    """Seed system permissions in the database.
    
    Args:
        session: SQLAlchemy async session.
    """
    from deltallm.db.models import Permission
    
    for perm_data in SYSTEM_PERMISSIONS:
        # Check if permission exists
        from sqlalchemy import select
        result = await session.execute(
            select(Permission).where(
                Permission.resource == perm_data["resource"],
                Permission.action == perm_data["action"],
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing is None:
            permission = Permission(**perm_data)
            session.add(permission)
    
    await session.commit()


async def seed_system_roles(session: Any) -> None:
    """Seed system roles in the database.
    
    Args:
        session: SQLAlchemy async session.
    """
    from sqlalchemy import select
    from deltallm.db.models import Permission, Role
    
    # Get all permissions for lookup
    result = await session.execute(select(Permission))
    permissions = {p.full_name: p for p in result.scalars().all()}
    
    for role_key, role_data in SYSTEM_ROLES.items():
        # Check if role exists
        result = await session.execute(
            select(Role).where(
                Role.name == role_data["name"],
                Role.is_system == True,  # noqa: E712
                Role.org_id.is_(None),
            )
        )
        existing = result.scalar_one_or_none()
        
        if existing is None:
            # Create role
            role = Role(
                name=role_data["name"],
                description=role_data["description"],
                is_system=role_data["is_system"],
                org_id=None,
            )
            
            # Add permissions
            for perm_pattern in role_data["permissions"]:
                if perm_pattern == "*:*":
                    # All permissions
                    role.permissions.extend(permissions.values())
                elif "*" in perm_pattern:
                    # Wildcard pattern (e.g., "api_key:*")
                    resource, action = perm_pattern.split(":")
                    for full_name, perm in permissions.items():
                        if full_name.startswith(f"{resource}:"):
                            role.permissions.append(perm)
                else:
                    # Specific permission
                    if perm_pattern in permissions:
                        role.permissions.append(permissions[perm_pattern])
            
            session.add(role)
    
    await session.commit()


async def seed_all(session: Any) -> None:
    """Run all seed operations.
    
    Args:
        session: SQLAlchemy async session.
    """
    await seed_permissions(session)
    await seed_system_roles(session)
