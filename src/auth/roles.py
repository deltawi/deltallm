from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformRole:
    ADMIN: str = "platform_admin"
    CO_ADMIN: str = "platform_co_admin"


@dataclass(frozen=True)
class OrganizationRole:
    OWNER: str = "org_owner"
    ADMIN: str = "org_admin"
    BILLING: str = "org_billing"
    AUDITOR: str = "org_auditor"


@dataclass(frozen=True)
class TeamRole:
    ADMIN: str = "team_admin"
    DEVELOPER: str = "team_developer"
    VIEWER: str = "team_viewer"


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
    SPEND_READ = "spend.read"
    CONFIG_READ = "config.read"
    CONFIG_UPDATE = "config.update"


PLATFORM_ROLE_PERMISSIONS: dict[str, set[str]] = {
    PlatformRole.ADMIN: {Permission.PLATFORM_ADMIN},
    PlatformRole.CO_ADMIN: {Permission.PLATFORM_ADMIN},
}


def has_platform_permission(role: str | None, permission: str) -> bool:
    if not role:
        return False
    allowed = PLATFORM_ROLE_PERMISSIONS.get(role, set())
    return permission in allowed
