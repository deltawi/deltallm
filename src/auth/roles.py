from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformRole:
    ADMIN: str = "platform_admin"
    ORG_USER: str = "org_user"


@dataclass(frozen=True)
class OrganizationRole:
    MEMBER: str = "org_member"
    OWNER: str = "org_owner"
    ADMIN: str = "org_admin"
    BILLING: str = "org_billing"
    AUDITOR: str = "org_auditor"


@dataclass(frozen=True)
class TeamRole:
    ADMIN: str = "team_admin"
    DEVELOPER: str = "team_developer"
    VIEWER: str = "team_viewer"


PLATFORM_ROLES: set[str] = {
    PlatformRole.ADMIN,
    PlatformRole.ORG_USER,
}

ORGANIZATION_ROLES: set[str] = {
    OrganizationRole.MEMBER,
    OrganizationRole.OWNER,
    OrganizationRole.ADMIN,
    OrganizationRole.BILLING,
    OrganizationRole.AUDITOR,
}

TEAM_ROLES: set[str] = {
    TeamRole.ADMIN,
    TeamRole.DEVELOPER,
    TeamRole.VIEWER,
}


class Permission:
    PLATFORM_ADMIN = "platform.admin"
    ORG_READ = "org.read"
    ORG_UPDATE = "org.update"
    TEAM_READ = "team.read"
    TEAM_UPDATE = "team.update"
    USER_READ = "user.read"
    USER_UPDATE = "user.update"
    KEY_READ = "key.read"
    KEY_UPDATE = "key.update"
    KEY_REVOKE = "key.revoke"
    KEY_CREATE_SELF = "key.create_self"
    SPEND_READ = "spend.read"
    AUDIT_READ = "audit.read"
    CONFIG_READ = "config.read"
    CONFIG_UPDATE = "config.update"


PLATFORM_ROLE_PERMISSIONS: dict[str, set[str]] = {
    PlatformRole.ADMIN: {Permission.PLATFORM_ADMIN},
}

LEGACY_PLATFORM_ROLE_ALIASES: dict[str, str] = {
    "platform_co_admin": PlatformRole.ADMIN,
}

ORG_ROLE_PERMISSIONS: dict[str, set[str]] = {
    OrganizationRole.MEMBER: {Permission.ORG_READ, Permission.TEAM_READ},
    OrganizationRole.OWNER: {
        Permission.ORG_READ, Permission.ORG_UPDATE, Permission.SPEND_READ, Permission.AUDIT_READ,
        Permission.TEAM_READ, Permission.TEAM_UPDATE,
        Permission.KEY_READ, Permission.KEY_UPDATE, Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF,
        Permission.USER_READ, Permission.USER_UPDATE,
    },
    OrganizationRole.ADMIN: {
        Permission.ORG_READ, Permission.ORG_UPDATE,
        Permission.TEAM_READ, Permission.TEAM_UPDATE,
        Permission.KEY_READ, Permission.KEY_UPDATE, Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF,
        Permission.USER_READ, Permission.USER_UPDATE, Permission.AUDIT_READ,
    },
    OrganizationRole.BILLING: {Permission.ORG_READ, Permission.SPEND_READ, Permission.TEAM_READ, Permission.KEY_READ},
    OrganizationRole.AUDITOR: {
        Permission.ORG_READ,
        Permission.SPEND_READ,
        Permission.TEAM_READ,
        Permission.KEY_READ,
        Permission.USER_READ,
        Permission.AUDIT_READ,
    },
}

TEAM_ROLE_PERMISSIONS: dict[str, set[str]] = {
    TeamRole.ADMIN: {Permission.TEAM_READ, Permission.TEAM_UPDATE, Permission.USER_READ, Permission.USER_UPDATE, Permission.KEY_READ, Permission.KEY_UPDATE, Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF},
    TeamRole.DEVELOPER: {Permission.TEAM_READ, Permission.USER_READ, Permission.KEY_READ, Permission.KEY_CREATE_SELF},
    TeamRole.VIEWER: {Permission.TEAM_READ},
}


def has_platform_permission(role: str | None, permission: str) -> bool:
    if not role:
        return False
    normalized = LEGACY_PLATFORM_ROLE_ALIASES.get(role, role)
    allowed = PLATFORM_ROLE_PERMISSIONS.get(normalized, set())
    return permission in allowed


def validate_platform_role(role: str | None) -> str:
    normalized = str(role or "").strip()
    if normalized not in PLATFORM_ROLES:
        raise ValueError("invalid role")
    return normalized


def validate_organization_role(role: str | None) -> str:
    normalized = str(role or "").strip()
    if normalized not in ORGANIZATION_ROLES:
        raise ValueError("invalid organization role")
    return normalized


def validate_team_role(role: str | None) -> str:
    normalized = str(role or "").strip()
    if normalized not in TEAM_ROLES:
        raise ValueError("invalid team role")
    return normalized
