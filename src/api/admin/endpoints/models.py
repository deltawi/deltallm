from __future__ import annotations

import inspect
from time import perf_counter
import secrets
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, field_validator, model_validator

from src.api.admin.endpoints.common import build_connection_summary, model_entries, to_json_value
from src.api.admin.endpoints.managed_assets import (
    asset_principal_for_request,
    parse_asset_access_input,
)
from src.api.admin.model_contracts import (
    ModelCredentialBindingRevokeResponse,
    ModelDeleteResponse,
    ModelMutationResponse,
)
from src.api.audit import emit_control_audit_event
from src.audit.actions import AuditAction
from src.config import ModelMode
from src.config_runtime.models import ModelHotReloadManager
from src.db.catalog.logical_models import LogicalModelRecord, LogicalModelRepository
from src.db.catalog.managed_assets import ManagedAssetAccessRepository
from src.db.catalog.named_credentials import NamedCredentialRecord, NamedCredentialRepository
from src.db.catalog.model_deployments import ModelDeploymentRecord, ModelDeploymentRepository
from src.api.admin.list_contracts import AdminListResponse, ModelListItem
from src.services.admin_list_health import list_health_refs, list_health_snapshot
from src.services.model_admin_listing import ModelSortKey, SortDirection, model_list_page
from src.db.routing.route_policy_lifecycle import RoutePolicyStateConflictError
from src.governance.access_groups import InvalidAccessGroupError, normalize_access_group_list
from src.middleware.admin import require_authenticated
from src.upstream_auth import (
    supports_custom_openai_compatible_auth,
    validate_auth_header_format,
    validate_auth_header_name,
)
from src.providers.healthcheck import probe_provider_health
from src.providers.model_discovery import discover_provider_models
from src.providers.resolution import (
    provider_presets,
    resolve_provider,
    validate_provider_mode_compatibility,
)
from src.router import HealthCheckInProgressError, build_deployment_registry
from src.router.health_state import HealthRefInput
from src.router.registry import DeploymentRegistryStore
from src.router.runtime_generation import (
    RoutingRuntimeGenerationStore,
    rebuild_routing_runtime_generation,
)
from src.services.asset_binding_mirror import reload_callable_target_grants_for_app
from src.services.creator_model_access import refresh_creator_model_access_for_app
from src.services.callable_targets import build_callable_target_catalog
from src.services.model_deployments import (
    resolve_runtime_deltallm_params,
)
from src.services.managed_asset_access import (
    AssetAccessPolicy,
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    GovernanceSource,
    ManagedAsset,
    ModelCredentialBindingMode,
    ModelCredentialBindingState,
    authorize_model_credential_binding,
    binding_requires_audience_coverage,
    resolve_asset_capabilities,
    revise_asset_access,
    serialize_asset_access,
    validate_grant_subject_for_principal,
    validate_model_credential_audience,
    namespace_creator_callable_key,
)
from src.services.model_identity import (
    creator_api_model_id,
    normalize_creator_namespace,
    normalize_model_slug,
    suggested_creator_namespace,
)
from src.services.model_visibility import get_tier_policy_mode_from_app
from src.services.named_credentials import (
    canonicalize_named_credential_provider,
    merge_named_credential_params,
    redact_connection_config,
    resolve_named_credential_record,
)
from src.services.organization_callable_target_sync import sync_auto_follow_organization_bindings

router = APIRouter(tags=["Models"])
_MISSING = object()
_CREDENTIAL_CONNECTION_FIELDS = {
    "api_key",
    "api_base",
    "api_version",
    "auth_header_name",
    "auth_header_format",
    "region",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
}
_INLINE_SECRET_FIELDS = {
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
}


class ProviderModelDiscoveryRequest(BaseModel):
    provider: str
    mode: ModelMode | None = None
    named_credential_id: str | None = None
    api_key: str | None = None
    api_base: str | None = None
    api_version: str | None = None
    auth_header_name: str | None = None
    auth_header_format: str | None = None

    @field_validator("auth_header_name")
    @classmethod
    def validate_custom_auth_header_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_auth_header_name(value)

    @field_validator("auth_header_format")
    @classmethod
    def validate_custom_auth_header_format(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_auth_header_format(value)

    @model_validator(mode="after")
    def validate_custom_auth_header_provider(self) -> "ProviderModelDiscoveryRequest":
        if self.auth_header_name is None and self.auth_header_format is None:
            return self
        if not supports_custom_openai_compatible_auth(self.provider):
            raise ValueError(
                f"Custom auth headers are not supported for provider '{self.provider}'"
            )
        return self


def _find_runtime_deployment(app: Any, deployment_id: str) -> Any | None:
    registry = getattr(getattr(app.state, "router", None), "deployment_registry", {}) or {}
    for deployments in registry.values():
        for deployment in deployments:
            if deployment.deployment_id == deployment_id:
                return deployment
    return None


def _runtime_health_ref(app: Any, deployment_id: str) -> HealthRefInput:
    deployment = _find_runtime_deployment(app, deployment_id)
    return deployment.health_ref if deployment is not None else deployment_id


def _to_int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalized_provider_key(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or "unknown"


def _provider_health_status(*, models: int, healthy_models: int) -> str:
    if models <= 0:
        return "healthy"
    if healthy_models <= 0:
        return "down"
    if healthy_models < models:
        return "degraded"
    return "healthy"


def _named_credential_repository(app: Any) -> NamedCredentialRepository | None:
    repository = getattr(app.state, "named_credential_repository", None)
    if isinstance(repository, NamedCredentialRepository):
        return repository
    return repository


def _managed_asset_repository(app: Any) -> ManagedAssetAccessRepository | None:
    return getattr(app.state, "managed_asset_access_repository", None)


def _logical_model_repository(app: Any) -> LogicalModelRepository | None:
    return getattr(app.state, "logical_model_repository", None)


def _model_deployment_repository(app: Any) -> ModelDeploymentRepository | None:
    return getattr(app.state, "model_deployment_repository", None)


def _model_repositories_or_503(
    app: Any,
) -> tuple[ManagedAssetAccessRepository, LogicalModelRepository, ModelDeploymentRepository]:
    access_repository = _managed_asset_repository(app)
    logical_repository = _logical_model_repository(app)
    deployment_repository = _model_deployment_repository(app)
    if access_repository is None or logical_repository is None or deployment_repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Managed model repositories unavailable",
        )
    return access_repository, logical_repository, deployment_repository


async def _model_access_by_name(
    app: Any,
    principal: AssetPrincipal,
    model_names: set[str],
) -> tuple[dict[str, AssetAccessPolicy], set[str]]:
    access_repository = _managed_asset_repository(app)
    if access_repository is None:
        return {}, set()
    access_index = getattr(access_repository, "model_access_index", None)
    if not callable(access_index):
        return {}, set()
    return await access_index(model_names, principal)


def _tier_visible_platform_model_names(
    app: Any,
    principal: AssetPrincipal,
    model_names: set[str],
) -> set[str]:
    """Resolve the platform catalog visible to a control-plane account.

    Creator models are handled by managed access. Platform administrators need
    the complete catalog for administration. In enforce mode ordinary accounts
    receive the union of their organizations' explicit tier catalogs and fail
    closed while the tier snapshot is unavailable.
    """

    if principal.is_platform_admin or get_tier_policy_mode_from_app(app) != "enforce":
        return set(model_names)
    service = getattr(app.state, "tier_policy_service", None)
    resolver = getattr(service, "resolve_org_allowed_callable_keys", None)
    if not callable(resolver) or bool(getattr(service, "snapshot_stale", False)):
        return set()
    visible: set[str] = set()
    for organization_id in principal.organization_ids:
        try:
            allowed = resolver(organization_id)
        except Exception:
            return set()
        if allowed is not None:
            visible.update(model_names.intersection(str(item) for item in allowed))
    return visible


async def scoped_model_entries_for_principal(
    request: Request,
    principal: AssetPrincipal,
) -> list[dict[str, Any]]:
    entries = await _control_plane_model_entries(request.app)
    logical_repository = _logical_model_repository(request.app)
    logical_by_name: dict[str, LogicalModelRecord] = {}
    list_by_names = getattr(logical_repository, "list_by_names", None)
    if callable(list_by_names):
        logical_models = await list_by_names(
            [str(entry.get("model_name") or "") for entry in entries]
        )
        logical_by_name = {model.model_name: model for model in logical_models}
    for entry in entries:
        api_model_id = str(entry.get("model_name") or "")
        logical_model = logical_by_name.get(api_model_id)
        entry["api_model_id"] = api_model_id
        entry["display_name"] = (
            str(logical_model.display_name or api_model_id) if logical_model else api_model_id
        )
        entry["created_by_user_id"] = logical_model.created_by_user_id if logical_model else None
        if logical_model:
            entry["created_at"] = entry.get("created_at") or logical_model.created_at
            if logical_model.updated_at and (
                not entry.get("updated_at") or logical_model.updated_at > entry["updated_at"]
            ):
                entry["updated_at"] = logical_model.updated_at
    visible_policies, creator_names = await _model_access_by_name(
        request.app,
        principal,
        {str(entry.get("model_name") or "") for entry in entries},
    )
    platform_names = {
        str(entry.get("model_name") or "")
        for entry in entries
        if str(entry.get("model_name") or "") not in creator_names
    }
    visible_platform_names = _tier_visible_platform_model_names(
        request.app,
        principal,
        platform_names,
    )
    credential_ids = {
        str(entry.get("named_credential_id") or "").strip()
        for entry in entries
        if str(entry.get("named_credential_id") or "").strip()
    }
    accessible_credential_ids = await _accessible_named_credential_ids(
        request.app,
        principal,
        credential_ids,
    )
    owned_credential_ids = await _owned_named_credential_ids(
        request.app,
        principal,
        credential_ids,
    )
    scoped: list[dict[str, Any]] = []
    for entry in entries:
        model_name = str(entry.get("model_name") or "")
        policy = visible_policies.get(model_name)
        if model_name in creator_names and policy is None:
            continue
        if model_name not in creator_names and model_name not in visible_platform_names:
            continue
        if policy is not None and (
            policy.asset.governance_source is GovernanceSource.CREATOR
            or principal.is_platform_admin
        ):
            entry["access"] = serialize_asset_access(policy, principal)
        _apply_principal_credential_view(
            entry,
            credential_accessible=(
                bool(entry.get("named_credential_id"))
                and str(entry.get("named_credential_id")) in accessible_credential_ids
            ),
            credential_owned=(
                bool(entry.get("named_credential_id"))
                and str(entry.get("named_credential_id")) in owned_credential_ids
            ),
            principal=principal,
        )
        scoped.append(entry)
    return scoped


async def _control_plane_model_entries(app: Any) -> list[dict[str, Any]]:
    """Build control-plane entries from persisted, unresolved deployment records."""

    runtime_entries = model_entries(app)
    runtime_deployment_ids = {str(entry.get("deployment_id") or "") for entry in runtime_entries}
    repository = _model_deployment_repository(app)
    list_all = getattr(repository, "list_all", None)
    if not callable(list_all):
        return runtime_entries
    try:
        records = await list_all()
    except Exception:
        return runtime_entries
    if not records:
        return runtime_entries

    credential_repository = _named_credential_repository(app)
    credential_ids = [
        record.named_credential_id for record in records if record.named_credential_id
    ]
    credential_loader = getattr(credential_repository, "list_by_ids", None)
    credentials = (
        await credential_loader(credential_ids)
        if callable(credential_loader) and credential_ids
        else {}
    )
    entries: list[dict[str, Any]] = []
    for record in records:
        params = dict(record.deltallm_params)
        provider = resolve_provider(params)
        credential = credentials.get(record.named_credential_id or "")
        routable = record.deployment_id in runtime_deployment_ids
        summary_params = dict(params)
        if credential is not None:
            for field in _CREDENTIAL_CONNECTION_FIELDS - _INLINE_SECRET_FIELDS:
                value = credential.connection_config.get(field)
                if value not in (None, ""):
                    summary_params[field] = value
        entries.append(
            {
                "deployment_id": record.deployment_id,
                "model_id": record.model_id,
                "model_name": record.model_name,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "routable": routable,
                "runtime_status": "active"
                if routable
                else "credential_unavailable"
                if record.governance_source == GovernanceSource.CREATOR.value
                else "runtime_unavailable",
                "provider": provider,
                "mode": (record.model_info or {}).get("mode", "chat"),
                "credential_source": "named"
                if (
                    record.named_credential_id
                    or record.credential_binding_mode
                    or record.credential_binding_state
                )
                else "inline",
                "inline_credentials_present": False,
                "connection_summary": build_connection_summary(
                    summary_params,
                    provider=provider,
                ),
                "named_credential_id": record.named_credential_id,
                "named_credential_name": credential.name if credential is not None else None,
                "credential_binding_mode": record.credential_binding_mode,
                "credential_binding_state": record.credential_binding_state,
                "credential_bound_by_account_id": record.credential_bound_by_account_id,
                "deltallm_params": redact_connection_config(params),
                "model_info": dict(record.model_info or {}),
            }
        )
    return entries


async def _accessible_named_credential_ids(
    app: Any,
    principal: AssetPrincipal,
    credential_ids: set[str],
) -> set[str]:
    if not credential_ids:
        return set()
    if principal.is_platform_admin:
        return set(credential_ids)
    repository = _managed_asset_repository(app)
    if repository is None:
        return set()
    bulk_loader = getattr(repository, "accessible_resource_ids", None)
    if callable(bulk_loader):
        return await bulk_loader(AssetKind.NAMED_CREDENTIAL, credential_ids, principal)
    accessible: set[str] = set()
    for credential_id in credential_ids:
        policy = await repository.get_policy_for_resource(
            AssetKind.NAMED_CREDENTIAL,
            credential_id,
            principal=principal,
        )
        if policy is not None:
            accessible.add(credential_id)
    return accessible


async def _owned_named_credential_ids(
    app: Any,
    principal: AssetPrincipal,
    credential_ids: set[str],
) -> set[str]:
    if not credential_ids or not principal.account_id:
        return set()
    if principal.is_platform_admin:
        return set(credential_ids)
    repository = _managed_asset_repository(app)
    if repository is None:
        return set()
    bulk_loader = getattr(repository, "owned_resource_ids", None)
    if callable(bulk_loader):
        return await bulk_loader(
            AssetKind.NAMED_CREDENTIAL,
            credential_ids,
            principal.account_id,
        )
    owned: set[str] = set()
    for credential_id in credential_ids:
        policy = await repository.get_policy_for_resource(
            AssetKind.NAMED_CREDENTIAL,
            credential_id,
        )
        if policy is not None and policy.asset.owner_account_id == principal.account_id:
            owned.add(credential_id)
    return owned


def _apply_principal_credential_view(
    entry: dict[str, Any],
    *,
    credential_accessible: bool,
    credential_owned: bool = False,
    principal: AssetPrincipal | None = None,
) -> None:
    named_credential_id = str(entry.get("named_credential_id") or "").strip() or None
    binding_state = entry.pop("credential_binding_state", None)
    binding_mode = entry.pop("credential_binding_mode", None)
    entry.pop("credential_bound_by_account_id", None)
    has_named_binding = bool(named_credential_id or binding_state or binding_mode)
    can_revoke = bool(
        binding_state == ModelCredentialBindingState.ACTIVE.value
        and ((principal is not None and principal.is_platform_admin) or credential_owned)
    )
    entry["credential_binding"] = {
        "state": binding_state,
        "mode": binding_mode if credential_accessible else None,
        "credential_access": (
            "accessible"
            if has_named_binding and credential_accessible
            else "opaque"
            if has_named_binding
            else "not_applicable"
        ),
        "can_replace": has_named_binding,
        "can_revoke": can_revoke,
    }
    if not has_named_binding or credential_accessible:
        return
    entry["named_credential_id"] = None
    entry["named_credential_name"] = None
    safe_params = dict(entry.get("deltallm_params") or {})
    for field in _CREDENTIAL_CONNECTION_FIELDS:
        safe_params.pop(field, None)
    entry["deltallm_params"] = safe_params
    entry["connection_summary"] = {}


async def _model_policy_for_deployment(
    app: Any,
    deployment_id: str,
) -> tuple[LogicalModelRecord, AssetAccessPolicy] | None:
    logical_repository = _logical_model_repository(app)
    access_repository = _managed_asset_repository(app)
    if logical_repository is None or access_repository is None:
        return None
    model = await logical_repository.get_by_deployment_id(deployment_id)
    if model is None or not model.managed_asset_id:
        return None
    policy = await access_repository.get_policy(model.managed_asset_id)
    if policy is None:
        return None
    return model, policy


async def _load_named_credential_or_400(
    app: Any,
    credential_id: str | None,
    *,
    provider: str | None = None,
    principal: AssetPrincipal | None = None,
) -> NamedCredentialRecord | None:
    normalized_id = str(credential_id or "").strip()
    if not normalized_id:
        return None
    repository = _named_credential_repository(app)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Named credential repository unavailable",
        )
    if principal is not None and not principal.is_platform_admin:
        access_repository = _managed_asset_repository(app)
        policy = (
            await access_repository.get_policy_for_resource(
                AssetKind.NAMED_CREDENTIAL,
                normalized_id,
                principal=principal,
            )
            if access_repository is not None
            else None
        )
        if policy is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="named_credential_id is invalid or inaccessible",
            )
    named_credential = await repository.get_by_id(normalized_id)
    if named_credential is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="named_credential_id is invalid"
        )
    if provider is not None and canonicalize_named_credential_provider(
        named_credential.provider
    ) != canonicalize_named_credential_provider(provider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Named credential provider does not match deployment provider",
        )
    return named_credential


async def _validate_model_named_credential_audience(
    app: Any,
    *,
    model_policy: AssetAccessPolicy | None,
    credential_id: str | None,
    binding_mode: ModelCredentialBindingMode | str | None,
) -> None:
    if (
        model_policy is None
        or model_policy.asset.governance_source is not GovernanceSource.CREATOR
        or not credential_id
        or not binding_requires_audience_coverage(binding_mode)
    ):
        return
    access_repository = _managed_asset_repository(app)
    if access_repository is None or not model_policy.asset.owner_account_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Managed asset authorization is unavailable",
        )
    credential_policy = await access_repository.get_policy_for_resource(
        AssetKind.NAMED_CREDENTIAL,
        credential_id,
    )
    if credential_policy is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="named_credential_id is invalid or inaccessible",
        )
    owner_principal = await access_repository.principal_for_account(
        model_policy.asset.owner_account_id
    )
    team_organization_ids = {
        grant.subject_id: await access_repository.organization_id_for_team(grant.subject_id)
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


async def _authorize_model_credential_binding_or_400(
    app: Any,
    *,
    credential_id: str | None,
    principal: AssetPrincipal,
) -> ModelCredentialBindingMode | None:
    if not credential_id:
        return None
    access_repository = _managed_asset_repository(app)
    if access_repository is None:
        if principal.is_platform_admin:
            return ModelCredentialBindingMode.PLATFORM_OVERRIDE
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Managed asset authorization is unavailable",
        )
    credential_policy = await access_repository.get_policy_for_resource(
        AssetKind.NAMED_CREDENTIAL,
        credential_id,
    )
    if credential_policy is None:
        if principal.is_platform_admin:
            return ModelCredentialBindingMode.PLATFORM_OVERRIDE
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="named_credential_id is invalid or inaccessible",
        )
    try:
        return authorize_model_credential_binding(credential_policy, principal)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _deployment_health_flags(app: Any, deployment_ids: list[str]) -> dict[str, bool]:
    if not deployment_ids:
        return {}

    health_backend = getattr(app.state, "router_state_backend", None)
    if health_backend is None:
        return {deployment_id: True for deployment_id in deployment_ids}

    health_refs = [_runtime_health_ref(app, deployment_id) for deployment_id in deployment_ids]
    health_by_deployment = await health_backend.get_health_batch(health_refs)
    cooldown_by_deployment = await health_backend.get_cooldown_batch(health_refs)
    return {
        deployment_id: str(health_by_deployment.get(deployment_id, {}).get("healthy", "true"))
        != "false"
        and not cooldown_by_deployment.get(deployment_id, False)
        for deployment_id in deployment_ids
    }


async def _serialize_deployment_health(app: Any, deployment_id: str) -> dict[str, Any]:
    health_backend = getattr(app.state, "router_state_backend", None)
    if health_backend is None:
        return {
            "healthy": True,
            "in_cooldown": False,
            "consecutive_failures": 0,
            "last_error": None,
            "last_error_at": None,
            "last_success_at": None,
        }

    health_ref = _runtime_health_ref(app, deployment_id)
    health = await health_backend.get_health(health_ref)
    in_cooldown = await health_backend.is_cooled_down(health_ref)
    healthy = str(health.get("healthy", "true")) != "false" and not in_cooldown
    return {
        "healthy": healthy,
        "in_cooldown": in_cooldown,
        "consecutive_failures": _to_int_or_none(health.get("consecutive_failures")) or 0,
        "last_error": health.get("last_error") or None,
        "last_error_at": _to_int_or_none(health.get("last_error_at")),
        "last_success_at": _to_int_or_none(health.get("last_success_at")),
    }


def _serialize_model_write_response(
    *,
    deployment_id: str,
    model_id: str | None,
    model_name: str,
    display_name: str,
    provider: str,
    named_credential_id: str | None,
    named_credential_name: str | None,
    deltallm_params: dict[str, Any],
    summary_params: dict[str, Any] | None,
    model_info: dict[str, Any],
    warnings: list[str],
    access: dict[str, object] | None = None,
    credential_accessible: bool = True,
    credential_binding_mode: str | None = None,
    credential_binding_state: str | None = None,
    credential_bound_by_account_id: str | None = None,
    credential_owned: bool = False,
    principal: AssetPrincipal | None = None,
) -> dict[str, Any]:
    credential_source = (
        "named"
        if named_credential_id or credential_binding_mode or credential_binding_state
        else "inline"
    )
    effective_summary_params = summary_params or deltallm_params
    response = {
        "deployment_id": deployment_id,
        "model_id": model_id,
        "model_name": model_name,
        "api_model_id": model_name,
        "display_name": display_name,
        "provider": provider,
        "mode": model_info.get("mode", "chat"),
        "credential_source": credential_source,
        "inline_credentials_present": credential_source == "inline"
        and any(str(deltallm_params.get(field) or "").strip() for field in _INLINE_SECRET_FIELDS),
        "connection_summary": build_connection_summary(
            effective_summary_params,
            provider=provider or resolve_provider(effective_summary_params),
        ),
        "named_credential_id": named_credential_id,
        "named_credential_name": named_credential_name,
        "deltallm_params": redact_connection_config(deltallm_params),
        "model_info": model_info,
        "warnings": warnings,
        "access": access,
        "credential_binding_mode": credential_binding_mode,
        "credential_binding_state": credential_binding_state,
        "credential_bound_by_account_id": credential_bound_by_account_id,
    }
    _apply_principal_credential_view(
        response,
        credential_accessible=credential_accessible,
        credential_owned=credential_owned,
        principal=principal,
    )
    return to_json_value(response)


def _rebuild_runtime_registry(app: Any) -> None:
    model_registry = getattr(app.state, "model_registry", {})
    route_groups = list(getattr(app.state, "route_groups", []))
    app.state.callable_target_catalog = build_callable_target_catalog(
        model_registry,
        route_groups,
    )
    rebuilt = build_deployment_registry(model_registry, route_groups=route_groups)

    runtime_registry = DeploymentRegistryStore(rebuilt)
    generation_store = getattr(app.state, "routing_runtime_generation_store", None)
    if isinstance(generation_store, RoutingRuntimeGenerationStore):
        replacement = rebuild_routing_runtime_generation(
            generation_store.require_snapshot(),
            model_registry=model_registry,
            route_groups=route_groups,
            callable_target_catalog=app.state.callable_target_catalog,
            deployment_registry=runtime_registry,
        )
        generation_store.replace(replacement)
        app.state.router = replacement.router
        app.state.failover_manager = replacement.failover_manager
        app.state.cooldown_manager = replacement.cooldown_manager

    for attr in ("router_health_handler", "background_health_checker"):
        holder = getattr(app.state, attr, None)
        if holder is not None and runtime_registry is not None:
            holder.registry = runtime_registry


async def _invalidate_route_group_runtime_cache(app: Any) -> None:
    cache = getattr(app.state, "route_group_runtime_cache", None)
    invalidate = getattr(cache, "invalidate", None)
    if callable(invalidate):
        await invalidate()


async def _sync_auto_follow_org_bindings(app: Any) -> None:
    await sync_auto_follow_organization_bindings(
        db=getattr(getattr(app.state, "prisma_manager", None), "client", None),
        callable_target_binding_repository=getattr(
            app.state, "callable_target_binding_repository", None
        ),
        route_group_repository=getattr(app.state, "route_group_repository", None),
        callable_target_catalog=getattr(app.state, "callable_target_catalog", None),
    )
    await reload_callable_target_grants_for_app(app)


def _validate_model_config_or_400(model_config: dict[str, Any]) -> None:
    try:
        validate_provider_mode_compatibility(model_config)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _normalize_model_info_or_400(
    model_info: dict[str, Any],
    *,
    existing_model_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = dict(model_info)
    if normalized.get("chat_capabilities") is not None:
        from src.chat_capabilities import ChatRoutingCapabilities

        try:
            normalized["chat_capabilities"] = ChatRoutingCapabilities.model_validate(
                normalized["chat_capabilities"]
            ).model_dump()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model_info.chat_capabilities must declare supported chat features with boolean values",
            ) from exc
    if "access_groups" in normalized:
        try:
            normalized["access_groups"] = normalize_access_group_list(
                normalized.get("access_groups"), strict=True
            )
        except InvalidAccessGroupError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    for field_name in ("max_tokens", "max_input_tokens", "max_output_tokens"):
        value = normalized.get(field_name)
        if value is None:
            continue
        existing_value = (existing_model_info or {}).get(field_name, _MISSING)
        preserves_legacy_value = bool(
            isinstance(value, int)
            and not isinstance(value, bool)
            and value < 1
            and isinstance(existing_value, int)
            and not isinstance(existing_value, bool)
            and value == existing_value
        )
        if isinstance(value, str):
            candidate = value.strip()
            if candidate and candidate.isascii() and candidate.isdigit():
                parsed = int(candidate)
                if parsed > 0:
                    normalized[field_name] = parsed
                    continue
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            continue
        if not preserves_legacy_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"model_info.{field_name} must be a positive integer",
            )
    return normalized


def _normalized_model_payload_or_400(
    payload: dict[str, Any],
    *,
    existing_model_name: str | None = None,
    existing_named_credential_id: str | None = None,
    existing_params: dict[str, Any] | None = None,
    existing_model_info: dict[str, Any] | None = None,
) -> tuple[str, str | None, dict[str, Any], dict[str, Any]]:
    model_name = str(payload.get("model_name") or existing_model_name or "").strip()
    if not model_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="model_name is required"
        )

    raw_named_credential_id = payload.get("named_credential_id", _MISSING)
    if raw_named_credential_id is _MISSING:
        named_credential_id = existing_named_credential_id
    else:
        named_credential_id = str(raw_named_credential_id or "").strip() or None

    raw_params = payload.get("deltallm_params")
    if raw_params is None:
        params = dict(existing_params or {})
    elif isinstance(raw_params, dict):
        params = _merged_model_params(
            existing_params=existing_params,
            incoming_params=raw_params,
            named_credential_id=named_credential_id,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="deltallm_params must be an object"
        )

    provider = str(params.get("provider") or "").strip().lower()
    model = str(params.get("model") or "").strip()
    api_base = str(params.get("api_base") or "").strip()

    if not provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider is required")
    if not model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="deltallm_params.model is required"
        )

    params["provider"] = provider
    params["model"] = model
    if api_base:
        params["api_base"] = api_base

    api_version = params.get("api_version")
    if api_version is not None:
        normalized_api_version = str(api_version).strip()
        params["api_version"] = normalized_api_version or None

    raw_model_info = payload.get("model_info")
    if raw_model_info is None:
        model_info = dict(existing_model_info or {})
    elif isinstance(raw_model_info, dict):
        model_info = dict(raw_model_info)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="model_info must be an object"
        )

    return (
        model_name,
        named_credential_id,
        params,
        _normalize_model_info_or_400(
            model_info,
            existing_model_info=existing_model_info,
        ),
    )


def _display_name_or_400(payload: dict[str, Any], *, fallback: str) -> str:
    display_name = str(payload.get("display_name") or fallback).strip()
    if not display_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="display_name is required"
        )
    if len(display_name) > 128:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="display_name must be 128 characters or fewer",
        )
    return display_name


async def _creator_model_identity_or_400(
    *,
    principal: AssetPrincipal,
    payload: dict[str, Any],
    legacy_model_name: str,
    logical_repository: LogicalModelRepository | None,
) -> tuple[str, str | None]:
    """Resolve an explicit creator id, retaining the legacy request contract as fallback."""
    has_explicit_identity = any(
        payload.get(field) is not None
        for field in ("api_model_id", "api_model_slug", "api_namespace")
    )
    if not has_explicit_identity:
        try:
            return namespace_creator_callable_key(legacy_model_name, principal), None
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    if principal.account_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated account is required",
        )
    if logical_repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Logical model repository unavailable",
        )

    identity = await logical_repository.get_creator_namespace(principal.account_id)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    _, stored_namespace = identity
    requested_namespace = str(payload.get("api_namespace") or stored_namespace or "").strip()
    requested_slug = str(payload.get("api_model_slug") or "").strip()
    explicit_api_model_id = str(payload.get("api_model_id") or "").strip()
    if explicit_api_model_id:
        namespace_part, separator, slug_part = explicit_api_model_id.partition("/")
        if not separator or "/" in slug_part:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="api_model_id must use <creator-namespace>/<model-slug>",
            )
        if requested_namespace and requested_namespace.lower() != namespace_part.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="api_model_id does not match api_namespace",
            )
        if requested_slug and requested_slug.lower() != slug_part.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="api_model_id does not match api_model_slug",
            )
        requested_namespace = namespace_part
        requested_slug = slug_part
    try:
        namespace = normalize_creator_namespace(requested_namespace)
        slug = normalize_model_slug(requested_slug)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if stored_namespace is not None and stored_namespace.lower() != namespace:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'Creator namespace is already locked as "{stored_namespace}"',
        )
    return creator_api_model_id(namespace, slug), namespace if stored_namespace is None else None


async def _claim_creator_namespace_or_409(
    repository: LogicalModelRepository,
    *,
    account_id: str,
    namespace: str,
) -> None:
    if await repository.claim_creator_namespace(account_id, namespace):
        return
    identity = await repository.get_creator_namespace(account_id)
    if identity is not None and identity[1] is not None:
        detail = f'Creator namespace is already locked as "{identity[1]}"'
    else:
        detail = f'Creator namespace "{namespace}" is already in use'
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _merged_model_params(
    *,
    existing_params: dict[str, Any] | None,
    incoming_params: dict[str, Any],
    named_credential_id: str | None,
) -> dict[str, Any]:
    merged = dict(existing_params or {})
    existing_provider = str(merged.get("provider") or "").strip().lower() or resolve_provider(
        merged
    )
    incoming_provider = (
        str(incoming_params.get("provider") or existing_provider or "").strip().lower()
    )
    provider_changed = bool(
        existing_provider and incoming_provider and existing_provider != incoming_provider
    )

    if named_credential_id is not None or provider_changed:
        for field in _CREDENTIAL_CONNECTION_FIELDS:
            merged.pop(field, None)

    for key, value in incoming_params.items():
        if value is None:
            merged.pop(str(key), None)
        else:
            merged[str(key)] = value

    if named_credential_id is not None:
        for field in _CREDENTIAL_CONNECTION_FIELDS:
            merged.pop(field, None)

    return merged


def _validate_creator_model_credentials(
    payload: dict[str, Any],
    *,
    named_credential_id: str | None,
    principal: AssetPrincipal,
) -> None:
    if principal.is_platform_admin:
        return
    if named_credential_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Creator models require a named_credential_id",
        )
    raw_params = payload.get("deltallm_params")
    if not isinstance(raw_params, dict):
        return
    submitted_connection_fields = sorted(
        field for field in _CREDENTIAL_CONNECTION_FIELDS if raw_params.get(field) not in (None, "")
    )
    if submitted_connection_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Creator models cannot submit inline connection settings; "
                "store them in the named credential"
            ),
        )


def _require_model_capability(
    policy: AssetAccessPolicy,
    principal: AssetPrincipal,
    *,
    delete: bool = False,
) -> None:
    if policy.asset.governance_source is GovernanceSource.PLATFORM:
        if not principal.is_platform_admin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Deployment not found",
            )
        return
    capabilities = resolve_asset_capabilities(policy, principal)
    allowed = capabilities.can_delete if delete else capabilities.can_write
    if not allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")


async def _reload_model_runtime_after_commit(
    app: Any,
    *,
    fail_closed_asset_id: str | None = None,
    fail_closed_model_names: set[str] | None = None,
) -> list[str]:
    warnings = list(
        await refresh_creator_model_access_for_app(
            app,
            fail_closed_asset_id=fail_closed_asset_id,
            fail_closed_model_names=fail_closed_model_names,
        )
    )
    hot_reload: ModelHotReloadManager | None = getattr(app.state, "model_hot_reload_manager", None)
    if hot_reload is None:
        return warnings
    try:
        await hot_reload.reload_runtime()
    except Exception:
        warnings.append("Mutation committed, but local routing runtime refresh failed")
    return warnings


@router.get(
    "/ui/api/models",
    dependencies=[Depends(require_authenticated)],
    response_model=AdminListResponse[ModelListItem],
)
async def list_models(
    request: Request,
    search: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort_by: ModelSortKey | None = Query(default=None),
    sort_direction: SortDirection = Query(default="desc"),
) -> dict[str, Any]:
    principal = asset_principal_for_request(request)
    entries = await scoped_model_entries_for_principal(request, principal)
    health = await list_health_snapshot(
        getattr(request.app.state, "router_state_backend", None),
        list_health_refs(
            getattr(getattr(request.app.state, "router", None), "deployment_registry", None),
            [
                str(entry["deployment_id"])
                for entry in entries
                if entry.get("routable") is not False
            ],
        ),
    )
    return model_list_page(
        entries,
        health=health,
        search=search,
        provider=provider,
        mode=mode,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )


@router.get("/ui/api/models/provider-health-summary", dependencies=[Depends(require_authenticated)])
async def provider_health_summary(request: Request) -> dict[str, Any]:
    principal = asset_principal_for_request(request)
    entries = await scoped_model_entries_for_principal(request, principal)
    deployment_ids = [str(entry["deployment_id"]) for entry in entries]
    healthy_by_deployment = await _deployment_health_flags(request.app, deployment_ids)

    provider_aggregates: dict[str, dict[str, int | str]] = {}
    for entry in entries:
        deployment_id = str(entry["deployment_id"])
        provider = _normalized_provider_key(entry.get("provider"))
        aggregate = provider_aggregates.setdefault(
            provider,
            {
                "provider": provider,
                "models": 0,
                "healthy_models": 0,
            },
        )
        aggregate["models"] = int(aggregate["models"]) + 1
        if entry.get("routable", True) and healthy_by_deployment.get(deployment_id, True):
            aggregate["healthy_models"] = int(aggregate["healthy_models"]) + 1

    providers: list[dict[str, Any]] = []
    active_providers = 0
    down_providers = 0
    for aggregate in provider_aggregates.values():
        models = int(aggregate["models"])
        healthy_models = int(aggregate["healthy_models"])
        unhealthy_models = models - healthy_models
        status_name = _provider_health_status(models=models, healthy_models=healthy_models)
        if status_name == "down":
            down_providers += 1
        else:
            active_providers += 1
        providers.append(
            {
                "provider": aggregate["provider"],
                "models": models,
                "healthy_models": healthy_models,
                "unhealthy_models": unhealthy_models,
                "status": status_name,
            }
        )

    providers.sort(key=lambda item: (-int(item["models"]), str(item["provider"])))

    return {
        "total_models": len(entries),
        "providers": providers,
        "summary": {
            "total_providers": len(providers),
            "active_providers": active_providers,
            "down_providers": down_providers,
        },
    }


@router.get("/ui/api/provider-presets", dependencies=[Depends(require_authenticated)])
async def list_provider_presets() -> dict[str, Any]:
    return {"data": provider_presets()}


@router.post(
    "/ui/api/provider-models/discover",
    dependencies=[Depends(require_authenticated)],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator permission and completed MFA required"},
    },
)
async def discover_models_for_provider(
    request: Request, payload: ProviderModelDiscoveryRequest
) -> dict[str, Any]:
    principal = asset_principal_for_request(request)
    if not principal.is_platform_admin:
        if payload.named_credential_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider discovery requires an accessible named credential",
            )
        if any(
            value not in (None, "")
            for value in (
                payload.api_key,
                payload.api_base,
                payload.api_version,
                payload.auth_header_name,
                payload.auth_header_format,
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provider discovery cannot use inline connection settings",
            )
    raw_named_credential = await _load_named_credential_or_400(
        request.app,
        payload.named_credential_id,
        provider=str(payload.provider or "").strip().lower() or None,
        principal=principal,
    )
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    named_credential = resolve_named_credential_record(
        raw_named_credential,
        secret_resolver=getattr(dynamic_config, "secret_resolver", None),
    )
    merged_params = merge_named_credential_params(
        {
            "api_key": payload.api_key,
            "api_base": payload.api_base,
            "api_version": payload.api_version,
            "auth_header_name": payload.auth_header_name,
            "auth_header_format": payload.auth_header_format,
        },
        named_credential,
    )
    return await discover_provider_models(
        request.app.state.control_http_client,
        discovery_runtime=request.app.state.provider_discovery_runtime,
        provider=payload.provider,
        mode=payload.mode,
        api_key=merged_params.get("api_key"),
        api_base=merged_params.get("api_base"),
        api_version=merged_params.get("api_version"),
        auth_header_name=merged_params.get("auth_header_name"),
        auth_header_format=merged_params.get("auth_header_format"),
        default_openai_base_url=getattr(
            request.app.state.settings, "openai_base_url", "https://api.openai.com/v1"
        ),
        general_settings=getattr(
            request.app.state,
            "upstream_http_settings",
            getattr(getattr(request.app.state, "app_config", None), "general_settings", None),
        ),
    )


@router.get("/ui/api/models/identity", dependencies=[Depends(require_authenticated)])
async def get_model_identity(request: Request) -> dict[str, Any]:
    principal = asset_principal_for_request(request)
    if principal.is_platform_admin:
        return {
            "namespace_required": False,
            "api_namespace": None,
            "suggested_namespace": None,
            "namespace_locked": False,
        }
    if principal.account_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated account is required",
        )
    repository = _logical_model_repository(request.app)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Logical model repository unavailable",
        )
    identity = await repository.get_creator_namespace(principal.account_id)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    email, namespace = identity
    return {
        "namespace_required": True,
        "api_namespace": namespace,
        "suggested_namespace": namespace
        or suggested_creator_namespace(email, principal.account_id),
        "namespace_locked": namespace is not None,
    }


@router.get("/ui/api/models/{deployment_id:path}", dependencies=[Depends(require_authenticated)])
async def get_model(request: Request, deployment_id: str) -> dict[str, Any]:
    principal = asset_principal_for_request(request)
    entries = await scoped_model_entries_for_principal(request, principal)
    for entry in entries:
        if entry["deployment_id"] == deployment_id:
            health = (
                await _serialize_deployment_health(request.app, deployment_id)
                if entry.get("routable", True)
                else {
                    "healthy": False,
                    "in_cooldown": False,
                    "consecutive_failures": 0,
                    "last_error": "Credential binding is unavailable",
                    "last_error_at": None,
                    "last_success_at": None,
                }
            )
            entry["healthy"] = health["healthy"]
            entry["health"] = health
            return entry
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")


@router.post(
    "/ui/api/models/{deployment_id:path}/health-check",
    dependencies=[Depends(require_authenticated)],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Platform administrator permission and completed MFA required"},
    },
)
async def check_model_health(request: Request, deployment_id: str) -> dict[str, Any]:
    principal = asset_principal_for_request(request)
    model_policy = await _model_policy_for_deployment(request.app, deployment_id)
    if model_policy is not None:
        _, policy = model_policy
        _require_model_capability(policy, principal)
    elif not principal.is_platform_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    deployment = _find_runtime_deployment(request.app, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    health_checker = getattr(request.app.state, "background_health_checker", None)
    if health_checker is not None:
        try:
            result = await health_checker.check_deployment_once(deployment)
        except HealthCheckInProgressError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
    else:
        result = await probe_provider_health(
            request.app.state.control_http_client,
            deployment.deltallm_params,
            default_openai_base_url=request.app.state.settings.openai_base_url,
            general_settings=getattr(
                request.app.state,
                "upstream_http_settings",
                getattr(getattr(request.app.state, "app_config", None), "general_settings", None),
            ),
            health_check_timeout_seconds=30,
            discovery_runtime=request.app.state.provider_discovery_runtime,
        )

    health = await _serialize_deployment_health(request.app, deployment_id)
    if result.healthy and health["healthy"]:
        message = "Health check passed"
    elif result.healthy:
        message = "Provider health check passed; deployment remains in cooldown"
    else:
        message = result.error or "Health check failed"
    return {
        "deployment_id": deployment_id,
        "healthy": health["healthy"],
        "health": health,
        "message": message,
        "status_code": result.status_code,
        "checked_at": result.checked_at,
    }


@router.post(
    "/ui/api/models/{deployment_id:path}/credential-binding/revoke",
    dependencies=[Depends(require_authenticated)],
    response_model=ModelCredentialBindingRevokeResponse,
)
async def revoke_model_credential_binding(
    request: Request,
    deployment_id: str,
) -> dict[str, Any]:
    """Let the credential owner withdraw delegated runtime use immediately."""

    request_start = perf_counter()
    principal = asset_principal_for_request(request)
    deployment_repository = _model_deployment_repository(request.app)
    get_deployment = getattr(deployment_repository, "get_by_deployment_id", None)
    revoke_binding = getattr(deployment_repository, "revoke_credential_binding", None)
    if not callable(get_deployment) or not callable(revoke_binding):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model deployment repository unavailable",
        )

    deployment = await get_deployment(deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    credential_id = str(deployment.named_credential_id or "").strip() or None
    if (
        credential_id is None
        or deployment.credential_binding_state == ModelCredentialBindingState.REVOKED.value
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The model credential binding is already revoked",
        )

    access_repository = _managed_asset_repository(request.app)
    if access_repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Managed asset repository unavailable",
        )
    credential_policy = await access_repository.get_policy_for_resource(
        AssetKind.NAMED_CREDENTIAL,
        credential_id,
    )
    is_credential_owner = bool(
        credential_policy is not None
        and credential_policy.asset.owner_account_id
        and credential_policy.asset.owner_account_id == principal.account_id
    )
    if not principal.is_platform_admin and not is_credential_owner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    revoked = await revoke_binding(
        deployment_id,
        expected_credential_id=credential_id,
    )
    if revoked is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The model credential binding changed; refresh and try again",
        )

    model_access = await _model_policy_for_deployment(request.app, deployment_id)
    logical_model, model_policy = model_access if model_access is not None else (None, None)
    warnings = await _reload_model_runtime_after_commit(
        request.app,
        fail_closed_asset_id=(model_policy.asset.asset_id if model_policy is not None else None),
        fail_closed_model_names={
            logical_model.model_name if logical_model is not None else deployment.model_name
        },
    )
    response = {
        "deployment_id": deployment_id,
        "revoked": True,
        "warnings": warnings,
    }
    await emit_control_audit_event(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MODEL_CREDENTIAL_REVOKE,
        status="success",
        resource_type="model_credential_binding",
        resource_id=deployment_id,
        response_payload=response,
        critical=True,
    )
    return response


@router.post(
    "/ui/api/models",
    dependencies=[Depends(require_authenticated)],
    response_model=ModelMutationResponse,
)
async def create_model(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    principal = asset_principal_for_request(request)
    requested_model_name, named_credential_id, deltallm_params, model_info = (
        _normalized_model_payload_or_400(payload)
    )
    display_name = _display_name_or_400(payload, fallback=requested_model_name)
    access_repository = _managed_asset_repository(request.app)
    logical_repository = _logical_model_repository(request.app)
    deployment_repository = _model_deployment_repository(request.app)
    namespace_to_claim: str | None = None
    if principal.is_platform_admin:
        model_name = str(payload.get("api_model_id") or requested_model_name).strip()
        if payload.get("api_model_id") is not None:
            try:
                model_name = normalize_model_slug(model_name)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
                ) from exc
        elif not model_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model_name is required",
            )
    else:
        model_name, namespace_to_claim = await _creator_model_identity_or_400(
            principal=principal,
            payload=payload,
            legacy_model_name=requested_model_name,
            logical_repository=logical_repository,
        )
    logical_model = None
    if principal.is_platform_admin and logical_repository is not None:
        logical_model = await logical_repository.get_by_name(model_name)
        if logical_model is not None and access_repository is not None:
            existing_policy = await access_repository.get_policy(logical_model.managed_asset_id)
            if (
                existing_policy is not None
                and existing_policy.asset.governance_source is GovernanceSource.CREATOR
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "API Model ID is owned by a creator; choose a different platform "
                        "API Model ID"
                    ),
                )
    _validate_creator_model_credentials(
        payload,
        named_credential_id=named_credential_id,
        principal=principal,
    )
    named_credential = await _load_named_credential_or_400(
        request.app,
        named_credential_id,
        provider=str(deltallm_params.get("provider") or "").strip().lower() or None,
        principal=principal,
    )
    credential_binding_mode = await _authorize_model_credential_binding_or_400(
        request.app,
        credential_id=named_credential_id,
        principal=principal,
    )
    effective_params = merge_named_credential_params(deltallm_params, named_credential)
    if not str(effective_params.get("api_base") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="deltallm_params.api_base is required"
        )
    default_deployment_id = (
        f"deployment-{secrets.token_hex(8)}"
        if payload.get("api_model_id") is not None
        else f"{model_name}-{secrets.token_hex(4)}"
    )
    deployment_id = str(payload.get("deployment_id") or default_deployment_id)

    if logical_model is None and logical_repository is not None:
        logical_model = await logical_repository.get_by_name(model_name)
    if logical_model is not None:
        stored_display_name = str(logical_model.display_name or logical_model.model_name)
        if "display_name" in payload and display_name != stored_display_name:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This API Model ID already exists with a different model name; "
                    "choose another API Model ID"
                ),
            )
        display_name = stored_display_name
    policy: AssetAccessPolicy | None = None
    new_logical_model = False
    if logical_model is not None and access_repository is not None:
        policy = await access_repository.get_policy(logical_model.managed_asset_id)
        if policy is None:
            if not principal.is_platform_admin:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Model not found",
                )
        elif (
            principal.is_platform_admin
            and policy.asset.governance_source is GovernanceSource.CREATOR
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "API Model ID is owned by a creator; choose a different platform API Model ID"
                ),
            )
        elif not principal.is_platform_admin:
            if policy.asset.governance_source is GovernanceSource.PLATFORM:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Platform models are managed by platform administrators",
                )
            _require_model_capability(policy, principal)
    elif logical_model is not None and not principal.is_platform_admin:
        _model_repositories_or_503(request.app)
    elif logical_model is None and (
        access_repository is None or logical_repository is None or deployment_repository is None
    ):
        if not principal.is_platform_admin:
            _model_repositories_or_503(request.app)
    elif logical_model is None:
        if not principal.is_platform_admin and principal.account_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authenticated account is required",
            )
        managed_asset_id = str(uuid4())
        logical_model = LogicalModelRecord(
            model_id=str(uuid4()),
            model_name=model_name,
            managed_asset_id=managed_asset_id,
            display_name=display_name,
        )
        base_policy = AssetAccessPolicy(
            asset=ManagedAsset(
                asset_id=managed_asset_id,
                asset_kind=AssetKind.MODEL,
                governance_source=(
                    GovernanceSource.PLATFORM
                    if principal.is_platform_admin
                    else GovernanceSource.CREATOR
                ),
                owner_account_id=None if principal.is_platform_admin else principal.account_id,
            )
        )
        grants = parse_asset_access_input(payload, managed_asset_id=managed_asset_id)
        if principal.is_platform_admin and grants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Platform model availability is controlled by tiers",
            )
        try:
            policy = revise_asset_access(
                base_policy,
                principal,
                grants=grants,
            )
            validate_grant_subject_for_principal(policy, principal)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        new_logical_model = True

    if (
        principal.is_platform_admin
        and logical_model is not None
        and (policy is None or policy.asset.governance_source is GovernanceSource.PLATFORM)
    ):
        grants = parse_asset_access_input(
            payload,
            managed_asset_id=logical_model.managed_asset_id,
        )
        if grants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Platform model availability is controlled by tiers",
            )

    await _validate_model_named_credential_audience(
        request.app,
        model_policy=policy,
        credential_id=named_credential_id,
        binding_mode=credential_binding_mode,
    )

    model_config = {
        "deployment_id": deployment_id,
        "model_id": logical_model.model_id if logical_model is not None else None,
        "model_name": model_name,
        "deltallm_params": deltallm_params,
        "model_info": model_info,
        "named_credential_id": named_credential_id,
        "credential_binding_mode": credential_binding_mode.value
        if credential_binding_mode is not None
        else None,
        "credential_binding_state": ModelCredentialBindingState.ACTIVE.value
        if named_credential_id
        else None,
        "credential_bound_by_account_id": principal.account_id
        if credential_binding_mode is not None
        else None,
        "display_name": display_name,
    }
    _validate_model_config_or_400(model_config)

    hot_reload: ModelHotReloadManager | None = getattr(
        request.app.state, "model_hot_reload_manager", None
    )
    warnings: list[str] = []
    database = getattr(access_repository, "prisma", None)
    can_create_atomically = bool(
        new_logical_model
        and policy is not None
        and logical_model is not None
        and database is not None
        and database is getattr(logical_repository, "prisma", None)
        and database is getattr(deployment_repository, "prisma", None)
        and hasattr(database, "tx")
    )
    if can_create_atomically:
        async with database.tx() as tx:
            if namespace_to_claim is not None:
                assert principal.account_id is not None
                await _claim_creator_namespace_or_409(
                    logical_repository.with_db(tx),
                    account_id=principal.account_id,
                    namespace=namespace_to_claim,
                )
            await access_repository.with_db(tx).create_policy(
                policy,
                created_by_account_id=principal.account_id,
            )
            await logical_repository.with_db(tx).create(logical_model)
            await deployment_repository.with_db(tx).create(
                ModelDeploymentRecord(
                    deployment_id=deployment_id,
                    model_name=model_name,
                    model_id=logical_model.model_id,
                    named_credential_id=named_credential_id,
                    credential_binding_mode=credential_binding_mode.value
                    if credential_binding_mode is not None
                    else None,
                    credential_binding_state=ModelCredentialBindingState.ACTIVE.value
                    if named_credential_id
                    else None,
                    credential_bound_by_account_id=principal.account_id
                    if credential_binding_mode is not None
                    else None,
                    deltallm_params=deltallm_params,
                    model_info=model_info,
                )
            )
        warnings = await _reload_model_runtime_after_commit(
            request.app,
            fail_closed_asset_id=policy.asset.asset_id,
            fail_closed_model_names={model_name},
        )
    elif new_logical_model and policy is not None and logical_model is not None:
        assert access_repository is not None
        assert logical_repository is not None
        if namespace_to_claim is not None:
            assert principal.account_id is not None
            await _claim_creator_namespace_or_409(
                logical_repository,
                account_id=principal.account_id,
                namespace=namespace_to_claim,
            )
        await access_repository.create_policy(
            policy,
            created_by_account_id=principal.account_id,
        )
        try:
            await logical_repository.create(logical_model)
            if hot_reload is not None:
                mutation = await hot_reload.add_model(model_config, updated_by="admin_api")
                deployment_id = mutation.value
                warnings = list(mutation.warnings)
            else:
                raise RuntimeError("Model runtime mutation service unavailable")
        except Exception:
            await logical_repository.delete(logical_model.model_id)
            await access_repository.delete_policy(policy.asset.asset_id)
            raise
    elif hot_reload is not None:
        mutation = await hot_reload.add_model(model_config, updated_by="admin_api")
        deployment_id = mutation.value
        warnings = list(mutation.warnings)
    else:
        request.app.state.model_registry.setdefault(model_name, []).append(
            {
                "deployment_id": deployment_id,
                "model_id": logical_model.model_id if logical_model is not None else None,
                "named_credential_id": named_credential_id,
                "named_credential_name": named_credential.name
                if named_credential is not None
                else None,
                "credential_binding_mode": credential_binding_mode.value
                if credential_binding_mode is not None
                else None,
                "credential_binding_state": ModelCredentialBindingState.ACTIVE.value
                if named_credential_id
                else None,
                "credential_bound_by_account_id": principal.account_id
                if credential_binding_mode is not None
                else None,
                "deltallm_params": resolve_runtime_deltallm_params(
                    deltallm_params,
                    request.app.state.settings,
                    named_credential=named_credential,
                    allow_platform_defaults=(
                        policy is None
                        or policy.asset.governance_source is not GovernanceSource.CREATOR
                    ),
                ),
                "model_info": model_info,
                "display_name": display_name,
            }
        )
        await _invalidate_route_group_runtime_cache(request.app)
        _rebuild_runtime_registry(request.app)
        await _sync_auto_follow_org_bindings(request.app)

    if new_logical_model and not can_create_atomically and policy is not None:
        warnings.extend(
            await refresh_creator_model_access_for_app(
                request.app,
                fail_closed_asset_id=policy.asset.asset_id,
                fail_closed_model_names={model_name},
            )
        )

    response = _serialize_model_write_response(
        deployment_id=deployment_id,
        model_id=logical_model.model_id if logical_model is not None else None,
        model_name=model_name,
        display_name=display_name,
        provider=str(deltallm_params.get("provider") or ""),
        named_credential_id=named_credential_id,
        named_credential_name=named_credential.name if named_credential is not None else None,
        deltallm_params=deltallm_params,
        summary_params=effective_params,
        model_info=model_info,
        warnings=warnings,
        access=serialize_asset_access(policy, principal) if policy is not None else None,
        credential_binding_mode=credential_binding_mode.value
        if credential_binding_mode is not None
        else None,
        credential_binding_state=ModelCredentialBindingState.ACTIVE.value
        if named_credential_id
        else None,
        credential_bound_by_account_id=(
            principal.account_id if credential_binding_mode is not None else None
        ),
        credential_owned=(
            bool(named_credential_id)
            and named_credential_id
            in await _owned_named_credential_ids(
                request.app,
                principal,
                {named_credential_id} if named_credential_id else set(),
            )
        ),
        principal=principal,
    )
    await emit_control_audit_event(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MODEL_CREATE,
        status="success",
        resource_type="model_deployment",
        resource_id=deployment_id,
        request_payload=payload,
        response_payload=response,
        critical=True,
    )
    return response


@router.put(
    "/ui/api/models/{deployment_id:path}",
    dependencies=[Depends(require_authenticated)],
    response_model=ModelMutationResponse,
)
async def update_model(
    request: Request, deployment_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    request_start = perf_counter()
    principal = asset_principal_for_request(request)
    model_access = await _model_policy_for_deployment(request.app, deployment_id)
    logical_model: LogicalModelRecord | None = None
    policy: AssetAccessPolicy | None = None
    if model_access is not None:
        logical_model, policy = model_access
        _require_model_capability(policy, principal)
    elif not principal.is_platform_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    hot_reload: ModelHotReloadManager | None = getattr(
        request.app.state, "model_hot_reload_manager", None
    )
    registry: dict[str, list[dict[str, Any]]] = request.app.state.model_registry
    model_repository = getattr(request.app.state, "model_deployment_repository", None)
    get_by_deployment_id = getattr(model_repository, "get_by_deployment_id", None)

    stored_deployment = None
    if callable(get_by_deployment_id):
        stored_deployment = await get_by_deployment_id(deployment_id)

    if stored_deployment is not None:
        found_model_name = stored_deployment.model_name
        found_deployment = {
            "deployment_id": stored_deployment.deployment_id,
            "model_id": stored_deployment.model_id,
            "named_credential_id": stored_deployment.named_credential_id,
            "credential_binding_mode": stored_deployment.credential_binding_mode,
            "credential_binding_state": stored_deployment.credential_binding_state,
            "credential_bound_by_account_id": (stored_deployment.credential_bound_by_account_id),
            "deltallm_params": dict(stored_deployment.deltallm_params),
            "model_info": dict(stored_deployment.model_info or {}),
        }
    else:
        found_model_name = None
        found_deployment = None

    if found_deployment is None or found_model_name is None:
        for model_name, deployments in list(registry.items()):
            for idx, deployment in enumerate(deployments):
                candidate_id = str(deployment.get("deployment_id") or f"{model_name}-{idx}")
                if candidate_id == deployment_id:
                    found_model_name = model_name
                    found_deployment = deployment
                    break
            if found_deployment:
                break

    if found_deployment is None or found_model_name is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    new_model_name, named_credential_id, deltallm_params, model_info = (
        _normalized_model_payload_or_400(
            payload,
            existing_model_name=found_model_name,
            existing_named_credential_id=found_deployment.get("named_credential_id"),
            existing_params=found_deployment.get("deltallm_params", {}),
            existing_model_info=found_deployment.get("model_info", {}),
        )
    )
    if logical_model is not None and new_model_name != found_model_name:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="API Model ID cannot be changed after creation",
        )
    existing_display_name = str(
        (logical_model.display_name if logical_model is not None else found_model_name)
        or found_model_name
    )
    display_name = _display_name_or_400(payload, fallback=existing_display_name)
    display_name_changed = logical_model is not None and display_name != existing_display_name
    existing_named_credential_id = (
        str(found_deployment.get("named_credential_id") or "").strip() or None
    )
    credential_replaced = named_credential_id != existing_named_credential_id
    existing_binding_state = str(found_deployment.get("credential_binding_state") or "").strip()
    if (
        not credential_replaced
        and policy is not None
        and policy.asset.governance_source is GovernanceSource.CREATOR
        and existing_binding_state == ModelCredentialBindingState.REVOKED.value
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The model credential binding was revoked; choose a replacement credential",
        )
    _validate_creator_model_credentials(
        payload,
        named_credential_id=named_credential_id,
        principal=principal,
    )
    named_credential = await _load_named_credential_or_400(
        request.app,
        named_credential_id,
        provider=str(deltallm_params.get("provider") or "").strip().lower() or None,
        principal=principal if credential_replaced else None,
    )
    if credential_replaced:
        credential_binding_mode = await _authorize_model_credential_binding_or_400(
            request.app,
            credential_id=named_credential_id,
            principal=principal,
        )
        credential_bound_by_account_id = (
            principal.account_id if credential_binding_mode is not None else None
        )
    else:
        raw_binding_mode = str(found_deployment.get("credential_binding_mode") or "").strip()
        try:
            credential_binding_mode = (
                ModelCredentialBindingMode(raw_binding_mode) if raw_binding_mode else None
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The model credential binding is invalid; choose a replacement credential",
            ) from exc
        credential_bound_by_account_id = (
            str(found_deployment.get("credential_bound_by_account_id") or "").strip() or None
        )
        if credential_binding_mode is None and named_credential_id:
            access_repository = _managed_asset_repository(request.app)
            credential_policy = (
                await access_repository.get_policy_for_resource(
                    AssetKind.NAMED_CREDENTIAL,
                    named_credential_id,
                )
                if access_repository is not None
                else None
            )
            if (
                credential_policy is not None
                and policy is not None
                and credential_policy.asset.owner_account_id == policy.asset.owner_account_id
            ):
                credential_binding_mode = ModelCredentialBindingMode.OWNER_DELEGATED
                credential_bound_by_account_id = credential_policy.asset.owner_account_id
            elif principal.is_platform_admin:
                credential_binding_mode = ModelCredentialBindingMode.PLATFORM_OVERRIDE
            else:
                credential_binding_mode = ModelCredentialBindingMode.AUDIENCE_SCOPED
    await _validate_model_named_credential_audience(
        request.app,
        model_policy=policy,
        credential_id=named_credential_id,
        binding_mode=credential_binding_mode,
    )
    effective_params = merge_named_credential_params(deltallm_params, named_credential)
    if not str(effective_params.get("api_base") or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="deltallm_params.api_base is required"
        )
    model_config = {
        "deployment_id": deployment_id,
        "model_id": logical_model.model_id if logical_model is not None else None,
        "model_name": new_model_name,
        "deltallm_params": deltallm_params,
        "model_info": model_info,
        "named_credential_id": named_credential_id,
        "credential_binding_mode": credential_binding_mode.value
        if credential_binding_mode is not None
        else None,
        "credential_binding_state": ModelCredentialBindingState.ACTIVE.value
        if named_credential_id
        else None,
        "credential_bound_by_account_id": credential_bound_by_account_id,
        "clear_credential_binding": named_credential_id is None,
        "display_name": display_name,
    }
    _validate_model_config_or_400(model_config)

    if hot_reload is not None:
        try:
            mutation = await hot_reload.update_model(
                deployment_id,
                model_config,
                updated_by="admin_api",
            )
        except RoutePolicyStateConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if not mutation.value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found"
            )
        warnings = list(mutation.warnings)
    else:
        warnings = []
        deployments = registry.get(found_model_name, [])
        for idx, deployment in enumerate(deployments):
            if str(deployment.get("deployment_id") or f"{found_model_name}-{idx}") == deployment_id:
                del deployments[idx]
                if not deployments:
                    registry.pop(found_model_name, None)
                break
        registry.setdefault(new_model_name, []).append(
            {
                "deployment_id": deployment_id,
                "model_id": logical_model.model_id if logical_model is not None else None,
                "named_credential_id": named_credential_id,
                "named_credential_name": named_credential.name
                if named_credential is not None
                else None,
                "credential_binding_mode": credential_binding_mode.value
                if credential_binding_mode is not None
                else None,
                "credential_binding_state": ModelCredentialBindingState.ACTIVE.value
                if named_credential_id
                else None,
                "deltallm_params": resolve_runtime_deltallm_params(
                    deltallm_params,
                    request.app.state.settings,
                    named_credential=named_credential,
                    allow_platform_defaults=(
                        policy is None
                        or policy.asset.governance_source is not GovernanceSource.CREATOR
                    ),
                ),
                "model_info": model_info,
                "display_name": display_name,
            }
        )
        await _invalidate_route_group_runtime_cache(request.app)
        _rebuild_runtime_registry(request.app)
        await _sync_auto_follow_org_bindings(request.app)

    repository_update = getattr(model_repository, "update", None)
    update_supports_display_name = bool(
        hot_reload is not None
        and callable(repository_update)
        and "display_name" in inspect.signature(repository_update).parameters
    )
    if display_name_changed and not update_supports_display_name:
        logical_repository = _logical_model_repository(request.app)
        if logical_repository is None or logical_model is None:
            warnings.append("Model settings were saved, but the display name was not updated")
            display_name = existing_display_name
        else:
            updated_logical_model = await logical_repository.update_display_name(
                logical_model.model_id,
                display_name,
            )
            if updated_logical_model is None:
                warnings.append("Model settings were saved, but the display name was not updated")
                display_name = existing_display_name

    response = _serialize_model_write_response(
        deployment_id=deployment_id,
        model_id=logical_model.model_id if logical_model is not None else None,
        model_name=new_model_name,
        display_name=display_name,
        provider=str(deltallm_params.get("provider") or ""),
        named_credential_id=named_credential_id,
        named_credential_name=named_credential.name if named_credential is not None else None,
        deltallm_params=deltallm_params,
        summary_params=effective_params,
        model_info=model_info,
        warnings=warnings,
        access=serialize_asset_access(policy, principal) if policy is not None else None,
        credential_accessible=(
            not named_credential_id
            or named_credential_id
            in await _accessible_named_credential_ids(
                request.app,
                principal,
                {named_credential_id},
            )
        ),
        credential_binding_mode=credential_binding_mode.value
        if credential_binding_mode is not None
        else None,
        credential_binding_state=ModelCredentialBindingState.ACTIVE.value
        if named_credential_id
        else None,
        credential_bound_by_account_id=credential_bound_by_account_id,
        credential_owned=(
            bool(named_credential_id)
            and named_credential_id
            in await _owned_named_credential_ids(
                request.app,
                principal,
                {named_credential_id} if named_credential_id else set(),
            )
        ),
        principal=principal,
    )
    await emit_control_audit_event(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_MODEL_UPDATE,
        status="success",
        resource_type="model_deployment",
        resource_id=deployment_id,
        request_payload=payload,
        response_payload=response,
        critical=True,
    )
    return response


@router.delete(
    "/ui/api/models/{deployment_id:path}",
    dependencies=[Depends(require_authenticated)],
    response_model=ModelDeleteResponse,
)
async def delete_model(request: Request, deployment_id: str) -> dict[str, object]:
    request_start = perf_counter()
    principal = asset_principal_for_request(request)
    model_access = await _model_policy_for_deployment(request.app, deployment_id)
    logical_model: LogicalModelRecord | None = None
    policy: AssetAccessPolicy | None = None
    if model_access is not None:
        logical_model, policy = model_access
        _require_model_capability(policy, principal, delete=True)
    elif not principal.is_platform_admin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    hot_reload: ModelHotReloadManager | None = getattr(
        request.app.state, "model_hot_reload_manager", None
    )
    access_repository = _managed_asset_repository(request.app)
    logical_repository = _logical_model_repository(request.app)
    deployment_repository = _model_deployment_repository(request.app)
    database = getattr(access_repository, "prisma", None)
    can_delete_atomically = bool(
        logical_model is not None
        and policy is not None
        and database is not None
        and database is getattr(logical_repository, "prisma", None)
        and database is getattr(deployment_repository, "prisma", None)
        and hasattr(database, "tx")
    )
    if can_delete_atomically:
        async with database.tx() as tx:
            transactional_deployments = deployment_repository.with_db(tx)
            transactional_models = logical_repository.with_db(tx)
            removed = await transactional_deployments.delete(deployment_id)
            if not removed:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Deployment not found",
                )
            if await transactional_models.count_deployments(logical_model.model_id) == 0:
                await transactional_models.delete(logical_model.model_id)
                await access_repository.with_db(tx).delete_policy(policy.asset.asset_id)
        response = {
            "deleted": True,
            "warnings": await _reload_model_runtime_after_commit(
                request.app,
                fail_closed_asset_id=policy.asset.asset_id,
                fail_closed_model_names={logical_model.model_name},
            ),
        }
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_MODEL_DELETE,
            status="success",
            resource_type="model_deployment",
            resource_id=deployment_id,
            response_payload=response,
            critical=True,
        )
        return response

    if hot_reload is not None:
        try:
            mutation = await hot_reload.remove_model(deployment_id, updated_by="admin_api")
        except RoutePolicyStateConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        if not mutation.value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found"
            )
        if logical_model is not None and logical_repository is not None:
            if await logical_repository.count_deployments(logical_model.model_id) == 0:
                await logical_repository.delete(logical_model.model_id)
                if access_repository is not None and policy is not None:
                    await access_repository.delete_policy(policy.asset.asset_id)
                    creator_access_warnings = await refresh_creator_model_access_for_app(
                        request.app,
                        fail_closed_asset_id=policy.asset.asset_id,
                        fail_closed_model_names={logical_model.model_name},
                    )
                else:
                    creator_access_warnings = ()
            else:
                creator_access_warnings = ()
        else:
            creator_access_warnings = ()
        response = {
            "deleted": True,
            "warnings": [*mutation.warnings, *creator_access_warnings],
        }
        await emit_control_audit_event(
            request=request,
            request_start=request_start,
            action=AuditAction.ADMIN_MODEL_DELETE,
            status="success",
            resource_type="model_deployment",
            resource_id=deployment_id,
            response_payload=response,
            critical=True,
        )
        return response

    registry: dict[str, list[dict[str, Any]]] = request.app.state.model_registry
    for model_name, deployments in list(registry.items()):
        kept: list[dict[str, Any]] = []
        removed_flag = False
        for idx, deployment in enumerate(deployments):
            candidate_id = str(deployment.get("deployment_id") or f"{model_name}-{idx}")
            if candidate_id == deployment_id:
                removed_flag = True
                continue
            kept.append(deployment)

        if removed_flag:
            if kept:
                registry[model_name] = kept
            else:
                registry.pop(model_name, None)
            await _invalidate_route_group_runtime_cache(request.app)
            _rebuild_runtime_registry(request.app)
            await _sync_auto_follow_org_bindings(request.app)
            response = {"deleted": True, "warnings": []}
            await emit_control_audit_event(
                request=request,
                request_start=request_start,
                action=AuditAction.ADMIN_MODEL_DELETE,
                status="success",
                resource_type="model_deployment",
                resource_id=deployment_id,
                response_payload=response,
                critical=True,
            )
            return response

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
