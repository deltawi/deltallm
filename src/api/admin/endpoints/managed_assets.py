from __future__ import annotations

from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, get_auth_scope
from src.audit.actions import AuditAction
from src.db.catalog.managed_assets import (
    ManagedAssetAccessRepository,
    ManagedAssetNotFoundError,
    ManagedAssetPolicyConflictError,
)
from src.middleware.admin import require_authenticated
from src.services.access.managed_asset_access import (
    AssetAccessRole,
    AssetAccessPolicy,
    AssetGrant,
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    AssetVisibility,
    GovernanceSource,
    revise_asset_access,
    resolve_asset_capabilities,
    serialize_asset_access,
    validate_grant_subject_for_principal,
    validate_model_credential_audience,
    binding_requires_audience_coverage,
)
from src.services.access.creator_model_access import refresh_creator_model_access_for_app
from src.services.access.creator_mcp_access import refresh_creator_mcp_access_for_app
from src.services.access.creator_prompt_access import refresh_creator_prompt_access_for_app
from src.services.access.creator_route_group_access import (
    refresh_creator_route_group_access_for_app,
)

router = APIRouter(tags=["Managed Assets"])
_MAX_AUDIENCE_GRANTS = 100


def _repository_or_503(request: Request) -> ManagedAssetAccessRepository:
    repository = getattr(request.app.state, "managed_asset_access_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Managed asset access repository unavailable",
        )
    return repository


def asset_principal_for_request(request: Request) -> AssetPrincipal:
    scope = get_auth_scope(
        request,
        authorization=request.headers.get("Authorization"),
        x_master_key=request.headers.get("X-Master-Key"),
    )
    return AssetPrincipal(
        account_id=scope.account_id,
        team_ids=frozenset(scope.team_ids),
        organization_ids=frozenset(scope.org_ids),
        is_platform_admin=scope.is_platform_admin,
    )


def parse_asset_access_input(
    payload: dict[str, Any],
    *,
    managed_asset_id: str,
) -> tuple[AssetGrant, ...]:
    raw_access = payload.get("access")
    access = raw_access if isinstance(raw_access, dict) else payload
    raw_grants = access.get("grants")
    if raw_grants is None:
        raw_items: list[dict[str, Any]] = [access]
    elif isinstance(raw_grants, list) and all(isinstance(item, dict) for item in raw_grants):
        raw_items = raw_grants
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="access.grants must be a list",
        )
    if len(raw_items) > _MAX_AUDIENCE_GRANTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"access.grants cannot contain more than {_MAX_AUDIENCE_GRANTS} audiences",
        )

    grants: list[AssetGrant] = []
    for item in raw_items:
        raw_subject_type = item.get("subject_type", item.get("visibility", "private"))
        normalized_subject_type = str(raw_subject_type or "private").strip().lower()
        if normalized_subject_type == AssetVisibility.PRIVATE.value:
            if len(raw_items) > 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="private access cannot be combined with audience grants",
                )
            continue
        try:
            subject_type = AssetSubjectType(normalized_subject_type)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subject_type must be team, organization, or public",
            ) from exc

        raw_role = item.get("access_role", item.get("role"))
        if raw_role is None and subject_type is AssetSubjectType.PUBLIC:
            raw_role = AssetAccessRole.READER.value
        try:
            access_role = AssetAccessRole(str(raw_role).strip().lower())
        except (AttributeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="access_role must be reader or editor",
            ) from exc
        if access_role is AssetAccessRole.OWNER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="owner is implicit and cannot be assigned as an audience role",
            )
        subject_id = str(item.get("subject_id") or "").strip() or None
        try:
            grants.append(
                AssetGrant(
                    managed_asset_id=managed_asset_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    access_role=access_role,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    subjects = {(grant.subject_type, grant.subject_id) for grant in grants}
    if len(subjects) != len(grants):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="an audience can only be granted access once",
        )
    return tuple(grants)


def _expected_policy_version(payload: dict[str, Any]) -> int:
    raw_access = payload.get("access")
    access = raw_access if isinstance(raw_access, dict) else payload
    raw_version = access.get("expected_policy_version")
    if isinstance(raw_version, bool):
        raw_version = None
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expected_policy_version is required and must be a positive integer",
        ) from exc
    if version < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expected_policy_version is required and must be a positive integer",
        )
    return version


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")


async def _validate_model_credential_dependencies(
    repository: ManagedAssetAccessRepository,
    revised: AssetAccessPolicy,
) -> None:
    dependency_pairs: list[tuple[AssetAccessPolicy, AssetAccessPolicy]] = []
    dependency_pairs_with_mode: list[tuple[AssetAccessPolicy, AssetAccessPolicy, str | None]] = []
    if revised.asset.asset_kind is AssetKind.MODEL:
        if revised.asset.governance_source is not GovernanceSource.CREATOR:
            return
        loader = getattr(repository, "list_credential_binding_policies_for_model", None)
        if callable(loader):
            dependency_pairs_with_mode.extend(
                (revised, credential_policy, mode)
                for credential_policy, mode in await loader(revised.asset.asset_id)
            )
        else:
            dependency_pairs.extend(
                (revised, credential_policy)
                for credential_policy in await repository.list_credential_policies_for_model(
                    revised.asset.asset_id
                )
            )
    elif revised.asset.asset_kind is AssetKind.NAMED_CREDENTIAL:
        loader = getattr(repository, "list_model_binding_policies_for_credential", None)
        if callable(loader):
            dependency_pairs_with_mode.extend(
                (model_policy, revised, mode)
                for model_policy, mode in await loader(revised.asset.asset_id)
            )
        else:
            dependency_pairs.extend(
                (model_policy, revised)
                for model_policy in await repository.list_model_policies_for_credential(
                    revised.asset.asset_id
                )
            )
    else:
        return

    dependency_pairs_with_mode.extend(
        (model_policy, credential_policy, "audience_scoped")
        for model_policy, credential_policy in dependency_pairs
    )
    for model_policy, credential_policy, binding_mode in dependency_pairs_with_mode:
        if not binding_requires_audience_coverage(binding_mode):
            continue
        owner_id = model_policy.asset.owner_account_id
        if not owner_id:
            continue
        owner_principal = await repository.principal_for_account(owner_id)
        if not resolve_asset_capabilities(credential_policy, owner_principal).can_read:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Access change would prevent a linked model owner from reading this credential",
            )
        team_organization_ids = {
            grant.subject_id: await repository.organization_id_for_team(grant.subject_id)
            for grant in model_policy.grants
            if grant.subject_type is AssetSubjectType.TEAM and grant.subject_id
        }
        try:
            validate_model_credential_audience(
                model_policy,
                credential_policy,
                model_owner_principal=owner_principal,
                model_team_organization_ids=team_organization_ids,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/ui/api/assets/audience-options",
    dependencies=[Depends(require_authenticated)],
)
async def search_managed_asset_audience_options(
    request: Request,
    subject_type: AssetSubjectType,
    search: str = Query(default="", max_length=200),
    selected_id: list[str] = Query(default=[]),
    limit: int = Query(default=3, ge=1, le=20),
) -> dict[str, Any]:
    if subject_type is AssetSubjectType.PUBLIC:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="subject_type must be team or organization",
        )
    if len(selected_id) > _MAX_AUDIENCE_GRANTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"selected_id cannot contain more than {_MAX_AUDIENCE_GRANTS} values",
        )
    repository = _repository_or_503(request)
    principal = asset_principal_for_request(request)
    return {
        "data": await repository.search_audience_options(
            subject_type,
            principal,
            search=search,
            selected_ids=tuple(selected_id),
            limit=limit,
        )
    }


@router.get(
    "/ui/api/assets/{asset_id}/access",
    dependencies=[Depends(require_authenticated)],
)
async def get_managed_asset_access(request: Request, asset_id: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    principal = asset_principal_for_request(request)
    policy = await repository.get_accessible_policy(asset_id, principal)
    if policy is None:
        raise _not_found()
    return {"access": serialize_asset_access(policy, principal)}


@router.put(
    "/ui/api/assets/{asset_id}/access",
    dependencies=[Depends(require_authenticated)],
)
async def update_managed_asset_access(
    request: Request,
    asset_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    principal = asset_principal_for_request(request)
    current = await repository.get_accessible_policy(asset_id, principal)
    if current is None:
        raise _not_found()

    grants = parse_asset_access_input(payload, managed_asset_id=current.asset.asset_id)
    expected_policy_version = _expected_policy_version(payload)
    if (
        current.asset.asset_kind is AssetKind.MODEL
        and current.asset.governance_source is GovernanceSource.PLATFORM
        and grants
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Platform model availability is controlled by tiers",
        )
    try:
        revised = revise_asset_access(
            current,
            principal,
            grants=grants,
        )
        validate_grant_subject_for_principal(revised, principal)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await _validate_model_credential_dependencies(repository, revised)

    try:
        replace_grants = getattr(repository, "replace_grants", repository.replace_grant)
        updated = await replace_grants(
            revised,
            expected_policy_version=expected_policy_version,
            changed_by_account_id=principal.account_id,
        )
    except ManagedAssetNotFoundError as exc:
        raise _not_found() from exc
    except ManagedAssetPolicyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    warnings: tuple[str, ...] = ()
    if updated.asset.asset_kind is AssetKind.MODEL:
        warnings = await refresh_creator_model_access_for_app(
            request.app,
            fail_closed_asset_id=asset_id,
        )
    elif updated.asset.asset_kind is AssetKind.PROMPT_TEMPLATE:
        warnings = await refresh_creator_prompt_access_for_app(
            request.app,
            fail_closed_asset_id=asset_id,
        )
    elif updated.asset.asset_kind is AssetKind.ROUTE_GROUP:
        warnings = await refresh_creator_route_group_access_for_app(
            request.app,
            fail_closed_asset_id=asset_id,
        )
    elif updated.asset.asset_kind is AssetKind.MCP_SERVER:
        warnings = await refresh_creator_mcp_access_for_app(
            request.app,
            fail_closed_asset_id=asset_id,
        )
    response: dict[str, Any] = {"access": serialize_asset_access(updated, principal)}
    if warnings:
        response["warnings"] = list(warnings)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.MANAGED_ASSET_ACCESS_UPDATE,
        resource_type="managed_asset",
        resource_id=asset_id,
        request_payload=payload,
        response_payload=response,
        before={"access": serialize_asset_access(current, principal)},
        after=response,
    )
    return response
