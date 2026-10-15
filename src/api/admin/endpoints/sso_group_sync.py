"""Platform-admin API for SSO group sync settings and the sign-in preview."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from src.api.admin.endpoints.common import db_or_503, emit_admin_mutation_audit
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.auth.sso_group_claims import MAX_CLAIM_VALUES, extract_claim_values
from src.auth.sso_group_settings import SSOGroupSyncSettings
from src.config_runtime.dynamic import (
    DynamicConfigRestartRequiredError,
    DynamicConfigValidationError,
)
from src.db.identity.sso_group_sync import (
    OrganizationTarget,
    TeamTarget,
    find_account_id_by_email,
    load_organization_targets,
    load_team_targets,
)
from src.middleware.admin import require_admin_permission
from src.services.identity.sso_group_access import plan_group_access
from src.services.identity.sso_group_sync import SSOGroupSyncService

router = APIRouter(tags=["Admin SSO"])
_PLATFORM_ADMIN = [Depends(require_admin_permission(Permission.PLATFORM_ADMIN))]

TargetStatus = Literal["ok", "not_found", "external_managed", "inactive_organization"]


class SSOProviderSummary(BaseModel):
    enabled: bool
    provider: str
    authorize_url: str | None


class SSOGroupTarget(BaseModel):
    id: str
    kind: Literal["team", "organization"]
    name: str | None
    organization_id: str | None = None
    organization_name: str | None = None
    status: TargetStatus


class SSOGroupSyncResponse(BaseModel):
    settings: SSOGroupSyncSettings
    provider: SSOProviderSummary
    targets: list[SSOGroupTarget]


class SSOGroupPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    settings: SSOGroupSyncSettings
    email: str | None = Field(default=None, max_length=320)
    claims: dict[str, Any] | None = None
    values: list[str] | None = Field(default=None, max_length=MAX_CLAIM_VALUES)


class SSOGroupPreviewChange(BaseModel):
    kind: Literal["platform_role", "organization", "team"]
    action: Literal["add", "update", "remove", "kept"]
    target_id: str | None
    target_name: str | None
    role_before: str | None
    role_after: str | None
    reason: str | None
    revokes_keys: bool


class SSOGroupPreviewResponse(BaseModel):
    claim_found: bool
    values: list[str]
    matched_values: list[str]
    sign_in_denied: bool
    account_found: bool
    account_role: str | None
    changes: list[SSOGroupPreviewChange]
    skipped: list[SSOGroupTarget]


def _general_settings(request: Request) -> Any:
    app_config = getattr(request.app.state, "app_config", None)
    return getattr(app_config, "general_settings", None)


def _current_settings(request: Request) -> SSOGroupSyncSettings:
    settings = getattr(_general_settings(request), "sso_group_sync", None)
    return settings if isinstance(settings, SSOGroupSyncSettings) else SSOGroupSyncSettings()


def _team_status(target: TeamTarget | None) -> TargetStatus:
    if target is None:
        return "not_found"
    if target.external_managed:
        return "external_managed"
    return "ok" if target.organization_active else "inactive_organization"


def _org_status(target: OrganizationTarget | None) -> TargetStatus:
    if target is None:
        return "not_found"
    return "ok" if target.active else "inactive_organization"


async def _targets(
    db: Any, team_ids: set[str], org_ids: set[str]
) -> tuple[dict[str, TeamTarget], dict[str, OrganizationTarget], list[SSOGroupTarget]]:
    teams = await load_team_targets(db, team_ids, lock=False)
    team_org_ids = {item.organization_id for item in teams.values() if item.organization_id}
    orgs = await load_organization_targets(db, org_ids | team_org_ids, lock=False)
    result: list[SSOGroupTarget] = []
    for team_id in sorted(team_ids):
        team = teams.get(team_id)
        org = orgs.get(team.organization_id) if team and team.organization_id else None
        result.append(
            SSOGroupTarget(
                id=team_id,
                kind="team",
                name=team.name if team else None,
                organization_id=team.organization_id if team else None,
                organization_name=org.name if org else None,
                status=_team_status(team),
            )
        )
    for org_id in sorted(org_ids):
        org = orgs.get(org_id)
        result.append(
            SSOGroupTarget(
                id=org_id,
                kind="organization",
                name=org.name if org else None,
                status=_org_status(org),
            )
        )
    return teams, orgs, result


async def _response(request: Request, settings: SSOGroupSyncSettings) -> SSOGroupSyncResponse:
    general = _general_settings(request)
    _, _, targets = await _targets(
        db_or_503(request), set(settings.mapped_team_ids), set(settings.mapped_organization_ids)
    )
    return SSOGroupSyncResponse(
        settings=settings,
        provider=SSOProviderSummary(
            enabled=getattr(request.app.state, "sso_auth_handler", None) is not None,
            provider=str(getattr(general, "sso_provider", "oidc") or "oidc"),
            authorize_url=getattr(general, "sso_authorize_url", None),
        ),
        targets=targets,
    )


@router.get(
    "/ui/api/settings/sso-group-sync",
    response_model=SSOGroupSyncResponse,
    dependencies=_PLATFORM_ADMIN,
)
async def get_sso_group_sync(request: Request) -> SSOGroupSyncResponse:
    return await _response(request, _current_settings(request))


@router.put(
    "/ui/api/settings/sso-group-sync",
    response_model=SSOGroupSyncResponse,
    dependencies=_PLATFORM_ADMIN,
)
async def update_sso_group_sync(
    request: Request, payload: SSOGroupSyncSettings
) -> SSOGroupSyncResponse:
    request_start = perf_counter()
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable"
        )
    _, _, targets = await _targets(
        db_or_503(request), set(payload.mapped_team_ids), set(payload.mapped_organization_ids)
    )
    invalid = [item for item in targets if item.status in ("not_found", "external_managed")]
    if invalid:
        names = ", ".join(f"{item.kind} {item.id} ({item.status})" for item in invalid)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Mappings point to unavailable targets: {names}",
        )
    before = _current_settings(request).model_dump(mode="json")
    after = payload.model_dump(mode="json")
    try:
        await dynamic_config.update_config(
            {"general_settings": {"sso_group_sync": after}}, updated_by="admin_api"
        )
    except DynamicConfigRestartRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "restart_required", "message": str(exc)},
        ) from exc
    except DynamicConfigValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_SETTINGS_UPDATE,
        resource_type="sso_group_sync",
        request_payload=after,
        before=before,
        after=after,
    )
    return await _response(request, payload)


@router.post(
    "/ui/api/settings/sso-group-sync/preview",
    response_model=SSOGroupPreviewResponse,
    dependencies=_PLATFORM_ADMIN,
)
async def preview_sso_group_sync(
    request: Request, payload: SSOGroupPreviewRequest
) -> SSOGroupPreviewResponse:
    """Show what the next SSO sign-in would change for one account. Nothing is written."""

    settings = payload.settings
    if payload.claims is not None:
        values = extract_claim_values([payload.claims], settings.claim)
    elif payload.values is not None:
        values = extract_claim_values([{"values": payload.values}], "values")
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Send claims or values to preview"
        )
    db = db_or_503(request)
    account_id = await find_account_id_by_email(db, payload.email) if payload.email else None
    plan = plan_group_access(settings, values or [])
    denied = settings.require_mapped_value and not plan.matched_values
    view = await SSOGroupSyncService(db).preview(plan, account_id)
    # A missing claim skips sync at sign-in, so the preview shows no changes then.
    diff_changes = view.diff.changes if values is not None and not denied else ()
    names = {item.team_id: item.name for item in view.teams.values()}
    names.update({item.organization_id: item.name for item in view.organizations.values()})
    _, _, skipped = await _targets(
        db,
        {item.target_id for item in view.diff.skipped if item.kind == "team"},
        {item.target_id for item in view.diff.skipped if item.kind == "organization"},
    )
    return SSOGroupPreviewResponse(
        claim_found=values is not None,
        values=list(values or []),
        matched_values=list(plan.matched_values),
        sign_in_denied=denied,
        account_found=account_id is not None,
        account_role=view.current.role if account_id else None,
        changes=[
            SSOGroupPreviewChange(
                kind=item.kind,
                action=item.action,
                target_id=item.target_id,
                target_name=names.get(item.target_id) if item.target_id else None,
                role_before=item.role_before,
                role_after=item.role_after,
                reason=item.reason,
                revokes_keys=item.action == "remove",
            )
            for item in diff_changes
        ],
        skipped=skipped,
    )
