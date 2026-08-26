from __future__ import annotations

from dataclasses import asdict
import logging
from time import perf_counter
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import ValidationError

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.services.asset_binding_mirror import (
    delete_callable_target_binding_mirror,
    mirror_route_group_binding_to_callable_target,
    reload_callable_target_grants,
)
from src.api.admin.endpoints.common import (
    db_or_503,
    emit_admin_mutation_audit,
    model_entries,
    to_json_value,
)
from src.api.admin.route_group_contracts import (
    RouteGroupDeleteResponse,
    RouteGroupMemberMutationResponse,
    RouteGroupMutationResponse,
    RoutePolicySimulationRequest,
    RoutePolicySimulationResponse,
    RoutePolicyMutationResponse,
    RoutePolicyRollbackResponse,
)
from src.db.prompt_registry import PromptRegistryRepository
from src.db.route_policy_lifecycle import RoutePolicyStateConflictError
from src.db.route_groups import RouteGroupRepository
from src.governance.access_groups import InvalidAccessGroupError, normalize_access_group_list
from src.middleware.admin import require_admin_permission
from src.router.policy_validation import (
    PolicyMemberInventoryItem,
    validate_route_policy,
)
from src.router.route_group_validation import (
    deployment_modes_by_id,
    normalize_route_group_mode,
    validate_route_group_member_modes,
)
from src.router import RoutingStrategy
from src.router.runtime_generation import require_routing_runtime_generation
from src.services.asset_ownership import (
    apply_owner_scope_to_metadata,
    normalize_owner_scope_type,
    owner_scope_from_metadata,
    public_metadata_without_owner_scope,
)
from src.services.asset_scopes import normalize_scope_type
from src.services.organization_callable_target_sync import (
    maybe_disable_organization_auto_follow_for_scope_mutation,
)
from src.services.route_policy_simulation import (
    RoutePolicySimulationInvalidError,
    RoutePolicySimulationNotFoundError,
    RoutePolicySimulationService,
    RoutePolicySimulationUnavailableError,
)
from src.services.route_policy_publication import (
    RoutePolicyPublicationNotFoundError,
    RoutePolicyPublicationService,
)
from src.services.route_group_refresh import refresh_route_group_runtime
from src.services.route_group_mutations import RouteGroupMutationService
from src.services.route_groups import RouteGroupRuntimeCache

router = APIRouter(tags=["Admin Route Groups"])
logger = logging.getLogger(__name__)

_ALLOWED_BINDING_SCOPE_TYPES = {"api_key", "key", "team", "organization", "org", "user"}


def _repository_or_503(request: Request) -> RouteGroupRepository:
    repository = getattr(request.app.state, "route_group_repository", None)
    if repository is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Route group repository unavailable",
        )
    return repository


def _mutation_service(request: Request) -> RouteGroupMutationService:
    service = getattr(request.app.state, "route_group_mutation_service", None)
    if isinstance(service, RouteGroupMutationService):
        return service
    return RouteGroupMutationService(
        route_groups=_repository_or_503(request),
        callable_bindings=getattr(
            request.app.state,
            "callable_target_binding_repository",
            None,
        ),
        model_deployments=getattr(
            request.app.state,
            "model_deployment_repository",
            None,
        ),
        model_registry_getter=lambda: getattr(request.app.state, "model_registry", None),
    )


def _raise_route_policy_conflict(exc: RoutePolicyStateConflictError) -> None:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _policy_response_payload(policy: Any) -> dict[str, Any]:
    return to_json_value(asdict(policy))


def _prompt_repository(request: Request) -> PromptRegistryRepository | None:
    repository = getattr(request.app.state, "prompt_registry_repository", None)
    if repository is not None and callable(getattr(repository, "get_template", None)):
        return repository
    return None


def _prompt_resolution_repository(request: Request) -> PromptRegistryRepository | None:
    repository = getattr(request.app.state, "prompt_registry_repository", None)
    if repository is not None and callable(getattr(repository, "resolve_prompt", None)):
        return repository
    return None


def _validate_mode(value: Any) -> str:
    try:
        return normalize_route_group_mode(value)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _validate_member_modes(
    request: Request,
    *,
    group_key: str,
    group_mode: str,
    member_ids: list[str],
) -> None:
    try:
        validate_route_group_member_modes(
            group_key=group_key,
            group_mode=group_mode,
            member_ids=member_ids,
            deployment_modes=deployment_modes_by_id(model_entries(request.app)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _validate_strategy(value: Any | None, *, field_name: str = "strategy") -> str | None:
    if value is None:
        return None
    strategy = str(value).strip()
    if not strategy:
        return None
    if strategy not in RoutingStrategy._value2member_map_:
        allowed = ", ".join(item.value for item in RoutingStrategy)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be one of: {allowed}",
        )
    return strategy


def _validate_int_or_none(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer"
        )
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer"
        ) from exc


def _validate_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must be a boolean",
        )
    return value


async def _resolve_policy_members(
    repository: RouteGroupRepository, group_key: str
) -> dict[str, PolicyMemberInventoryItem]:
    members = await repository.list_members(group_key)
    return {
        member.deployment_id.strip(): PolicyMemberInventoryItem(
            deployment_id=member.deployment_id.strip(),
            enabled=member.enabled,
        )
        for member in members
        if isinstance(member.deployment_id, str) and member.deployment_id.strip()
    }


async def _serialize_group_members(request: Request, members: list[Any]) -> list[dict[str, Any]]:
    entries_by_id = {
        str(entry.get("deployment_id") or ""): entry
        for entry in model_entries(request.app)
        if str(entry.get("deployment_id") or "")
    }
    health_backend = getattr(request.app.state, "router_state_backend", None)

    payloads: list[dict[str, Any]] = []
    for member in members:
        item = to_json_value(asdict(member))
        runtime_entry = entries_by_id.get(member.deployment_id)
        if runtime_entry is None:
            item["model_name"] = None
            item["provider"] = None
            item["mode"] = None
            item["healthy"] = None
            payloads.append(item)
            continue

        healthy = True
        if health_backend is not None:
            runtime_deployment = next(
                (
                    deployment
                    for deployments in getattr(
                        request.app.state.router, "deployment_registry", {}
                    ).values()
                    for deployment in deployments
                    if deployment.deployment_id == member.deployment_id
                ),
                None,
            )
            health_ref = (
                runtime_deployment.health_ref
                if runtime_deployment is not None
                else member.deployment_id
            )
            health = await health_backend.get_health(health_ref)
            healthy = str(health.get("healthy", "true")) != "false"

        item["model_name"] = runtime_entry.get("model_name")
        item["provider"] = runtime_entry.get("provider")
        item["mode"] = runtime_entry.get("model_info", {}).get("mode") or "chat"
        item["healthy"] = healthy
        payloads.append(item)
    return payloads


def _runtime_cache(request: Request) -> RouteGroupRuntimeCache | None:
    cache = getattr(request.app.state, "route_group_runtime_cache", None)
    if callable(getattr(cache, "invalidate", None)):
        return cache
    return None


async def _refresh_route_group_runtime(
    request: Request,
    *,
    prompt_group_key: str | None = None,
) -> tuple[str, ...]:
    warnings: list[str] = []
    service = getattr(request.app.state, "prompt_registry_service", None)
    if (
        prompt_group_key
        and service is not None
        and callable(getattr(service, "invalidate_scope", None))
    ):
        try:
            await service.invalidate_scope(scope_type="group", scope_id=prompt_group_key)
        except Exception:
            warnings.append("Mutation committed, but local prompt cache invalidation failed")
            logger.warning("local prompt cache invalidation failed after commit", exc_info=True)
    result = await refresh_route_group_runtime(
        cache=_runtime_cache(request),
        reloader=getattr(request.app.state, "model_hot_reload_manager", None),
        invalidation=getattr(request.app.state, "governance_invalidation_service", None),
    )
    warnings.extend(result.warnings)
    return tuple(warnings)


def _validated_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object"
        )
    metadata = dict(value)
    if "access_groups" in metadata:
        try:
            metadata["access_groups"] = normalize_access_group_list(
                metadata.get("access_groups"), strict=True
            )
        except InvalidAccessGroupError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
    return metadata


def _validate_binding_scope_type(value: Any) -> str:
    scope_type = str(value or "").strip().lower()
    if scope_type not in _ALLOWED_BINDING_SCOPE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_BINDING_SCOPE_TYPES))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"scope_type must be one of: {allowed}"
        )
    normalized = normalize_scope_type(scope_type)
    if normalized == "group":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="scope_type must be one of: api_key, team, organization, user",
        )
    return normalized


def _validate_scope_id(value: Any, *, field_name: str = "scope_id") -> str:
    scope_id = str(value or "").strip()
    if not scope_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} is required"
        )
    return scope_id


def _group_response_payload(group: Any) -> dict[str, Any]:
    payload = to_json_value(asdict(group))
    if isinstance(payload, dict):
        payload["metadata"] = public_metadata_without_owner_scope(payload.get("metadata"))
    return payload


def _binding_response_payload(binding: Any) -> dict[str, Any]:
    return to_json_value(asdict(binding))


async def _validate_default_prompt(
    request: Request, value: Any
) -> tuple[bool, dict[str, str] | None]:
    if value is ...:
        return False, None
    if value is None:
        return True, None
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="default_prompt must be an object or null",
        )
    template_key = str(value.get("template_key") or value.get("key") or "").strip()
    if not template_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="default_prompt.template_key is required",
        )
    label = str(value.get("label") or "").strip()
    prompt_repository = _prompt_repository(request)
    if prompt_repository is not None:
        template = await prompt_repository.get_template(template_key)
        if template is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="default_prompt.template_key does not exist",
            )
    payload: dict[str, str] = {"template_key": template_key}
    if label:
        payload["label"] = label
    return True, payload


async def _resolve_group_metadata(
    request: Request,
    *,
    existing_metadata: dict[str, Any] | None,
    raw_metadata: Any,
    raw_default_prompt: Any,
    raw_owner_scope_type: Any = ...,
    raw_owner_scope_id: Any = ...,
) -> dict[str, Any] | None:
    metadata = dict(existing_metadata or {})
    raw_metadata_value = _validated_metadata(raw_metadata)
    if raw_metadata_value is not None:
        metadata.update(raw_metadata_value)
    has_default_prompt, default_prompt = await _validate_default_prompt(request, raw_default_prompt)
    if has_default_prompt:
        if default_prompt is None:
            metadata.pop("default_prompt", None)
        else:
            metadata["default_prompt"] = default_prompt
    if raw_owner_scope_type is not ... or raw_owner_scope_id is not ...:
        current_scope = owner_scope_from_metadata(existing_metadata)
        current_scope_type = current_scope.scope_type
        current_scope_id = current_scope.scope_id
        scope_type = (
            current_scope_type
            if raw_owner_scope_type is ...
            else normalize_owner_scope_type(raw_owner_scope_type)
        )
        scope_id = (
            current_scope_id
            if raw_owner_scope_id is ...
            else (str(raw_owner_scope_id).strip() if raw_owner_scope_id is not None else None)
        )
        try:
            metadata = (
                apply_owner_scope_to_metadata(
                    metadata or None,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
                or {}
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return metadata or None


def _validate_policy_payload(
    payload: dict[str, Any], *, available_members: dict[str, PolicyMemberInventoryItem]
) -> tuple[dict[str, Any], list[str]]:
    try:
        normalized, warnings = validate_route_policy(payload, available_members=available_members)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if "strategy" in normalized:
        _validate_strategy(normalized.get("strategy"))
    return normalized, warnings


def _policy_publication_service(request: Request) -> RoutePolicyPublicationService:
    async def refresh_runtime() -> tuple[str, ...]:
        return await _refresh_route_group_runtime(request)

    return RoutePolicyPublicationService(
        route_groups=_repository_or_503(request),
        refresh_runtime=refresh_runtime,
    )


async def _publish_route_group_policy_response(
    request: Request,
    group_key: str,
    payload: dict[str, Any],
    *,
    latest_draft: bool,
) -> dict[str, Any]:
    request_start = perf_counter()
    service = _policy_publication_service(request)
    try:
        if latest_draft:
            result = await service.publish_latest_draft(group_key, published_by="admin_api")
        else:
            result = await service.publish_document(
                group_key,
                payload,
                published_by="admin_api",
            )
    except RoutePolicyPublicationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoutePolicyStateConflictError as exc:
        _raise_route_policy_conflict(exc)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    response = {
        "group_key": group_key,
        "policy": _policy_response_payload(result.policy),
        "warnings": list(result.warnings),
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_policy",
        resource_id=group_key,
        request_payload=None if latest_draft else payload,
        response_payload=response,
    )
    return response


@router.get(
    "/ui/api/route-groups", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))]
)
async def list_route_groups(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    groups, total = await repository.list_groups(search=search, limit=limit, offset=offset)
    data = [_group_response_payload(group) for group in groups]
    return {
        "data": data,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.get(
    "/ui/api/route-groups/{group_key}",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))],
)
async def get_route_group(request: Request, group_key: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    members = await repository.list_members(group_key)
    policy = await repository.get_published_policy(group_key)
    bindings, _ = await repository.list_bindings(group_key=group_key, limit=200, offset=0)
    return {
        "group": _group_response_payload(group),
        "members": await _serialize_group_members(request, members),
        "policy": _policy_response_payload(policy) if policy is not None else None,
        "bindings": [_binding_response_payload(binding) for binding in bindings],
    }


@router.post(
    "/ui/api/route-groups",
    response_model=RouteGroupMutationResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def create_route_group(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)

    group_key = str(payload.get("group_key") or payload.get("key") or "").strip()
    if not group_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="group_key is required")

    name = str(payload.get("name")).strip() if payload.get("name") is not None else None
    mode = _validate_mode(payload.get("mode"))
    strategy = _validate_strategy(payload.get("strategy"))
    enabled = _validate_bool(payload.get("enabled", True), field_name="enabled")
    metadata = await _resolve_group_metadata(
        request,
        existing_metadata=None,
        raw_metadata=payload.get("metadata"),
        raw_default_prompt=payload.get("default_prompt", ...),
        raw_owner_scope_type=payload.get("owner_scope_type", ...),
        raw_owner_scope_id=payload.get("owner_scope_id", ...),
    )

    try:
        created = await repository.create_group(
            group_key=group_key,
            name=name,
            mode=mode,
            routing_strategy=strategy,
            enabled=enabled,
            metadata=metadata,
        )
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Route group already exists"
            ) from exc
        raise

    refresh_warnings = await _refresh_route_group_runtime(
        request,
        prompt_group_key=created.group_key,
    )
    response = _group_response_payload(created)
    if refresh_warnings:
        response["warnings"] = list(refresh_warnings)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_group",
        resource_id=created.group_key,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.put(
    "/ui/api/route-groups/{group_key}",
    response_model=RouteGroupMutationResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def update_route_group(
    request: Request, group_key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)

    existing = await repository.get_group(group_key)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    name = (
        str(payload.get("name")).strip()
        if "name" in payload and payload.get("name") is not None
        else existing.name
    )
    mode = _validate_mode(payload.get("mode", existing.mode))
    strategy = _validate_strategy(payload.get("strategy", existing.routing_strategy))
    enabled = _validate_bool(payload.get("enabled", existing.enabled), field_name="enabled")
    metadata = await _resolve_group_metadata(
        request,
        existing_metadata=existing.metadata,
        raw_metadata=payload.get("metadata"),
        raw_default_prompt=payload.get("default_prompt", ...),
        raw_owner_scope_type=payload.get("owner_scope_type", ...),
        raw_owner_scope_id=payload.get("owner_scope_id", ...),
    )
    members = await repository.list_members(group_key)
    _validate_member_modes(
        request,
        group_key=group_key,
        group_mode=mode,
        member_ids=[member.deployment_id for member in members if member.enabled],
    )

    try:
        updated = await repository.update_group(
            group_key,
            name=name,
            mode=mode,
            routing_strategy=strategy,
            enabled=enabled,
            metadata=metadata,
        )
    except RoutePolicyStateConflictError as exc:
        _raise_route_policy_conflict(exc)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    before = _group_response_payload(existing)
    after = _group_response_payload(updated)
    refresh_warnings = await _refresh_route_group_runtime(
        request,
        prompt_group_key=group_key,
    )
    if refresh_warnings:
        after["warnings"] = list(refresh_warnings)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_group",
        resource_id=group_key,
        request_payload=payload,
        response_payload=after,
        before=before,
        after=after,
    )
    return after


@router.delete(
    "/ui/api/route-groups/{group_key}",
    response_model=RouteGroupDeleteResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def delete_route_group(request: Request, group_key: str) -> dict[str, Any]:
    request_start = perf_counter()
    deletion = await _mutation_service(request).delete_group(group_key)
    if not deletion.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    refresh_warnings = await _refresh_route_group_runtime(
        request,
        prompt_group_key=group_key,
    )
    response: dict[str, Any] = {"deleted": True}
    warnings = (*deletion.warnings, *refresh_warnings)
    if warnings:
        response["warnings"] = list(warnings)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_group",
        resource_id=group_key,
        response_payload=response,
    )
    return response


@router.get(
    "/ui/api/route-group-bindings",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))],
)
async def list_route_group_bindings(
    request: Request,
    group_key: str | None = Query(default=None),
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    normalized_scope_type = (
        _validate_binding_scope_type(scope_type) if scope_type is not None else None
    )
    bindings, total = await repository.list_bindings(
        group_key=group_key,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
        limit=limit,
        offset=offset,
    )
    return {
        "data": [_binding_response_payload(binding) for binding in bindings],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@router.post(
    "/ui/api/route-group-bindings",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def upsert_route_group_binding(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    group_key = str(payload.get("group_key") or "").strip()
    if not group_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="group_key is required")
    if await repository.get_group(group_key) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    normalized_scope_type = _validate_binding_scope_type(payload.get("scope_type"))
    scope_id = _validate_scope_id(payload.get("scope_id"))
    enabled = _validate_bool(payload.get("enabled", True), field_name="enabled")
    metadata = _validated_metadata(payload.get("metadata"))

    binding = await repository.upsert_binding(
        group_key,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
        enabled=enabled,
        metadata=metadata,
    )
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    await mirror_route_group_binding_to_callable_target(
        request,
        group_key=group_key,
        scope_type=normalized_scope_type,
        scope_id=scope_id,
        enabled=enabled,
        metadata=metadata,
    )
    if normalized_scope_type == "organization":
        await maybe_disable_organization_auto_follow_for_scope_mutation(
            db_or_503(request),
            scope_type=normalized_scope_type,
            scope_id=scope_id,
        )
    await reload_callable_target_grants(request)
    response = _binding_response_payload(binding)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTE_GROUP_BINDING_UPSERT,
        resource_type="route_group_binding",
        resource_id=binding.route_group_binding_id,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete(
    "/ui/api/route-group-bindings/{binding_id}",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def delete_route_group_binding(request: Request, binding_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    binding = await repository.get_binding(binding_id)
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Route group binding not found"
        )

    deleted = await repository.delete_binding(binding_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Route group binding not found"
        )

    await delete_callable_target_binding_mirror(
        request,
        callable_key=binding.group_key,
        scope_type=binding.scope_type,
        scope_id=binding.scope_id,
    )
    if binding.scope_type == "organization":
        await maybe_disable_organization_auto_follow_for_scope_mutation(
            db_or_503(request),
            scope_type=binding.scope_type,
            scope_id=binding.scope_id,
        )
    await reload_callable_target_grants(request)
    response = {"deleted": True, "route_group_binding_id": binding_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTE_GROUP_BINDING_DELETE,
        resource_type="route_group_binding",
        resource_id=binding_id,
        response_payload=response,
    )
    return response


@router.get(
    "/ui/api/route-groups/{group_key}/members",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))],
)
async def list_route_group_members(request: Request, group_key: str) -> list[dict[str, Any]]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    members = await repository.list_members(group_key)
    return [to_json_value(asdict(member)) for member in members]


@router.post(
    "/ui/api/route-groups/{group_key}/members",
    response_model=RouteGroupMemberMutationResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def upsert_route_group_member(
    request: Request, group_key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    deployment_id = str(payload.get("deployment_id") or "").strip()
    if not deployment_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="deployment_id is required"
        )

    enabled = _validate_bool(payload.get("enabled", True), field_name="enabled")
    weight = _validate_int_or_none(payload.get("weight"), field_name="weight")
    priority = _validate_int_or_none(payload.get("priority"), field_name="priority")

    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    if enabled:
        _validate_member_modes(
            request,
            group_key=group_key,
            group_mode=group.mode,
            member_ids=[deployment_id],
        )

    try:
        member = await repository.upsert_member(
            group_key,
            deployment_id=deployment_id,
            enabled=enabled,
            weight=weight,
            priority=priority,
        )
    except RoutePolicyStateConflictError as exc:
        _raise_route_policy_conflict(exc)
    except Exception as exc:
        if "foreign key" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="deployment_id does not exist"
            ) from exc
        raise
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    refresh_warnings = await _refresh_route_group_runtime(request)
    response = to_json_value(asdict(member))
    if refresh_warnings:
        response["warnings"] = list(refresh_warnings)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_group_member",
        resource_id=f"{group_key}:{deployment_id}",
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete(
    "/ui/api/route-groups/{group_key}/members/{deployment_id:path}",
    response_model=RouteGroupDeleteResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def delete_route_group_member(
    request: Request, group_key: str, deployment_id: str
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    try:
        removed = await repository.remove_member(group_key, deployment_id)
    except RoutePolicyStateConflictError as exc:
        _raise_route_policy_conflict(exc)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Route group member not found"
        )

    refresh_warnings = await _refresh_route_group_runtime(request)
    response: dict[str, Any] = {"deleted": True}
    if refresh_warnings:
        response["warnings"] = list(refresh_warnings)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_group_member",
        resource_id=f"{group_key}:{deployment_id}",
        response_payload=response,
    )
    return response


@router.get(
    "/ui/api/route-groups/{group_key}/policy",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))],
)
async def get_route_group_policy(request: Request, group_key: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    policy = await repository.get_published_policy(group_key)
    if policy is None:
        return {"group_key": group_key, "policy": None}
    return {"group_key": group_key, "policy": _policy_response_payload(policy)}


@router.get(
    "/ui/api/route-groups/{group_key}/policies",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))],
)
async def list_route_group_policies(request: Request, group_key: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    policies = await repository.list_policies(group_key)
    return {
        "group_key": group_key,
        "policies": [_policy_response_payload(policy) for policy in policies],
    }


@router.post(
    "/ui/api/route-groups/{group_key}/policy/validate",
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def validate_route_group_policy(
    request: Request, group_key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    normalized, warnings = _validate_policy_payload(
        payload,
        available_members=await _resolve_policy_members(repository, group_key),
    )
    return {"group_key": group_key, "valid": True, "policy": normalized, "warnings": warnings}


@router.post(
    "/ui/api/route-groups/{group_key}/policy/draft",
    response_model=RoutePolicyMutationResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def save_route_group_policy_draft(
    request: Request, group_key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    normalized, warnings = _validate_policy_payload(
        payload,
        available_members=await _resolve_policy_members(repository, group_key),
    )
    try:
        policy = await repository.save_draft_policy(group_key, normalized)
    except RoutePolicyStateConflictError as exc:
        _raise_route_policy_conflict(exc)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    response = {
        "group_key": group_key,
        "policy": _policy_response_payload(policy),
        "warnings": warnings,
    }
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_policy_draft",
        resource_id=group_key,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post(
    "/ui/api/route-groups/{group_key}/policy/publish",
    response_model=RoutePolicyMutationResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def publish_route_group_policy_v2(
    request: Request, group_key: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = payload or {}
    return await _publish_route_group_policy_response(
        request,
        group_key,
        body,
        latest_draft=not body,
    )


@router.post(
    "/ui/api/route-groups/{group_key}/policy/rollback",
    response_model=RoutePolicyRollbackResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def rollback_route_group_policy(
    request: Request, group_key: str, payload: dict[str, Any]
) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    if "version" not in payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="version is required")

    version = _validate_int_or_none(payload.get("version"), field_name="version")
    if version is None or version < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="version must be >= 1")

    try:
        policy = await repository.rollback_policy(
            group_key, target_version=version, published_by="admin_api"
        )
    except RoutePolicyStateConflictError as exc:
        _raise_route_policy_conflict(exc)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Route group or policy version not found"
        )

    refresh_warnings = await _refresh_route_group_runtime(request)
    response = {
        "group_key": group_key,
        "policy": _policy_response_payload(policy),
        "rolled_back_from_version": version,
    }
    if refresh_warnings:
        response["warnings"] = list(refresh_warnings)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_policy",
        resource_id=group_key,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post(
    "/ui/api/route-groups/{group_key}/policy/simulate",
    response_model=RoutePolicySimulationResponse,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))],
)
async def simulate_route_group_policy(
    request: Request, group_key: str, payload: dict[str, Any] | None = None
) -> RoutePolicySimulationResponse:
    try:
        simulation_request = RoutePolicySimulationRequest.model_validate(payload or {})
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.errors(include_url=False),
        ) from exc

    try:
        runtime = require_routing_runtime_generation(request.app.state)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Router runtime unavailable",
        ) from exc

    service = RoutePolicySimulationService(
        route_groups=_repository_or_503(request),
        runtime=runtime,
        prompts=_prompt_resolution_repository(request),
    )
    try:
        return await service.simulate(group_key, simulation_request)
    except RoutePolicySimulationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RoutePolicySimulationInvalidError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RoutePolicySimulationUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


@router.put(
    "/ui/api/route-groups/{group_key}/policy",
    response_model=RoutePolicyMutationResponse,
    deprecated=True,
    dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))],
)
async def publish_route_group_policy(
    request: Request,
    response: Response,
    group_key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    encoded_group_key = quote(group_key, safe="")
    response.headers["Link"] = (
        f'</ui/api/route-groups/{encoded_group_key}/policy/publish>; rel="successor-version"'
    )
    return await _publish_route_group_policy_response(
        request,
        group_key,
        payload,
        latest_draft=False,
    )
