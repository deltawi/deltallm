from __future__ import annotations

from src.models.responses import UserAPIKeyAuth


def can_access_owned_resource(
    *,
    owner_api_key: str | None,
    owner_team_id: str | None,
    owner_organization_id: str | None = None,
    auth: UserAPIKeyAuth,
) -> bool:
    if owner_api_key and owner_api_key == auth.api_key:
        return True
    if owner_team_id and auth.team_id and owner_team_id == auth.team_id:
        return True
    if owner_team_id is None and owner_organization_id and auth.organization_id and owner_organization_id == auth.organization_id:
        return True
    return False
