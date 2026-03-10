from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import emit_admin_mutation_audit, model_entries, to_json_value
from src.db.prompt_registry import PromptRegistryRepository
from src.db.route_groups import RouteGroupRepository
from src.middleware.admin import require_admin_permission
from src.router.policy_validation import validate_route_policy
from src.router import Router, RouterConfig, RoutingStrategy, build_deployment_registry, build_route_group_policies
from src.services.prompt_registry import apply_route_preferences_to_metadata, parse_prompt_reference
from src.services.route_groups import RouteGroupRuntimeCache

router = APIRouter(tags=["Admin Route Groups"])

ALLOWED_MODES = {"chat", "embedding", "image_generation", "audio_speech", "audio_transcription", "rerank"}


def _repository_or_503(request: Request) -> RouteGroupRepository:
    repository = getattr(request.app.state, "route_group_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Route group repository unavailable")
    return repository


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
    mode = str(value or "chat").strip()
    if mode not in ALLOWED_MODES:
        allowed = ", ".join(sorted(ALLOWED_MODES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"mode must be one of: {allowed}")
    return mode


def _validate_strategy(value: Any | None, *, field_name: str = "strategy") -> str | None:
    if value is None:
        return None
    strategy = str(value).strip()
    if not strategy:
        return None
    if strategy not in RoutingStrategy._value2member_map_:
        allowed = ", ".join(item.value for item in RoutingStrategy)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be one of: {allowed}")
    return strategy


def _validate_int_or_none(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer") from exc


def _validate_iterations(value: Any) -> int:
    parsed = _validate_int_or_none(value, field_name="iterations")
    iterations = parsed if parsed is not None else 100
    if iterations < 1 or iterations > 5000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="iterations must be between 1 and 5000")
    return iterations


def _merge_simulation_members(
    base_members: list[dict[str, Any]],
    policy_members: Any,
) -> list[dict[str, Any]]:
    if not isinstance(policy_members, list):
        return list(base_members)

    by_id: dict[str, dict[str, Any]] = {
        str(member.get("deployment_id") or ""): dict(member)
        for member in base_members
        if isinstance(member, dict) and str(member.get("deployment_id") or "")
    }
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_member in policy_members:
        if not isinstance(raw_member, dict):
            continue
        deployment_id = str(raw_member.get("deployment_id") or "").strip()
        if not deployment_id:
            continue
        existing = by_id.get(deployment_id)
        if existing is None:
            continue
        merged = dict(existing)
        if "enabled" in raw_member:
            merged["enabled"] = bool(raw_member.get("enabled", True))
        if raw_member.get("weight") is not None:
            merged["weight"] = int(raw_member["weight"])
        if raw_member.get("priority") is not None:
            merged["priority"] = int(raw_member["priority"])
        ordered.append(merged)
        seen.add(deployment_id)

    for member in base_members:
        deployment_id = str(member.get("deployment_id") or "")
        if not deployment_id or deployment_id in seen:
            continue
        ordered.append(dict(member))
    return ordered


def _apply_policy_simulation_override(
    runtime_groups: list[dict[str, Any]],
    *,
    group_key: str,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    found = False
    for group in runtime_groups:
        if str(group.get("key") or "") != group_key:
            updated.append(group)
            continue
        found = True
        patched = dict(group)
        if "strategy" in policy:
            patched["strategy"] = policy.get("strategy")
        if "timeouts" in policy:
            patched["timeouts"] = policy.get("timeouts")
        if "retry" in policy:
            patched["retry"] = policy.get("retry")
        if "members" in policy:
            patched["members"] = _merge_simulation_members(list(group.get("members") or []), policy.get("members"))
        updated.append(patched)

    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    return updated


async def _resolve_member_ids(repository: RouteGroupRepository, group_key: str) -> set[str]:
    members = await repository.list_members(group_key)
    return {
        member.deployment_id.strip()
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
            health = await health_backend.get_health(member.deployment_id)
            healthy = str(health.get("healthy", "true")) != "false"

        item["model_name"] = runtime_entry.get("model_name")
        item["provider"] = runtime_entry.get("provider")
        item["mode"] = runtime_entry.get("model_info", {}).get("mode") or "chat"
        item["healthy"] = healthy
        payloads.append(item)
    return payloads


async def _reload_runtime(request: Request) -> None:
    reloader = getattr(request.app.state, "model_hot_reload_manager", None)
    if reloader is None:
        return
    await reloader.reload_runtime()


def _runtime_cache(request: Request) -> RouteGroupRuntimeCache | None:
    cache = getattr(request.app.state, "route_group_runtime_cache", None)
    if callable(getattr(cache, "invalidate", None)):
        return cache
    return None


async def _invalidate_runtime_cache(request: Request) -> None:
    cache = _runtime_cache(request)
    if cache is None:
        return
    await cache.invalidate()


async def _invalidate_prompt_group_cache(request: Request, group_key: str) -> None:
    service = getattr(request.app.state, "prompt_registry_service", None)
    if service is None or not callable(getattr(service, "invalidate_scope", None)):
        return
    await service.invalidate_scope(scope_type="group", scope_id=group_key)


def _validated_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")
    return dict(value)


async def _validate_default_prompt(request: Request, value: Any) -> tuple[bool, dict[str, str] | None]:
    if value is ...:
        return False, None
    if value is None:
        return True, None
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="default_prompt must be an object or null")
    template_key = str(value.get("template_key") or value.get("key") or "").strip()
    if not template_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="default_prompt.template_key is required")
    label = str(value.get("label") or "").strip()
    prompt_repository = _prompt_repository(request)
    if prompt_repository is not None:
        template = await prompt_repository.get_template(template_key)
        if template is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="default_prompt.template_key does not exist")
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
    return metadata or None


def _validate_policy_payload(payload: dict[str, Any], *, available_member_ids: set[str]) -> tuple[dict[str, Any], list[str]]:
    try:
        normalized, warnings = validate_route_policy(payload, available_member_ids=available_member_ids)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if "strategy" in normalized:
        _validate_strategy(normalized.get("strategy"))
    return normalized, warnings


@router.get("/ui/api/route-groups", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_route_groups(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    groups, total = await repository.list_groups(search=search, limit=limit, offset=offset)
    data = [to_json_value(asdict(group)) for group in groups]
    return {
        "data": data,
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/route-groups/{group_key}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def get_route_group(request: Request, group_key: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    members = await repository.list_members(group_key)
    policy = await repository.get_published_policy(group_key)
    return {
        "group": to_json_value(asdict(group)),
        "members": await _serialize_group_members(request, members),
        "policy": to_json_value(asdict(policy)) if policy is not None else None,
    }


@router.post("/ui/api/route-groups", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def create_route_group(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)

    group_key = str(payload.get("group_key") or payload.get("key") or "").strip()
    if not group_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="group_key is required")

    name = str(payload.get("name")).strip() if payload.get("name") is not None else None
    mode = _validate_mode(payload.get("mode"))
    strategy = _validate_strategy(payload.get("strategy"))
    enabled = bool(payload.get("enabled", True))
    metadata = await _resolve_group_metadata(
        request,
        existing_metadata=None,
        raw_metadata=payload.get("metadata"),
        raw_default_prompt=payload.get("default_prompt", ...),
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
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Route group already exists") from exc
        raise

    await _invalidate_runtime_cache(request)
    await _invalidate_prompt_group_cache(request, created.group_key)
    await _reload_runtime(request)
    response = to_json_value(asdict(created))
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


@router.put("/ui/api/route-groups/{group_key}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def update_route_group(request: Request, group_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)

    existing = await repository.get_group(group_key)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    name = str(payload.get("name")).strip() if "name" in payload and payload.get("name") is not None else existing.name
    mode = _validate_mode(payload.get("mode", existing.mode))
    strategy = _validate_strategy(payload.get("strategy", existing.routing_strategy))
    enabled = bool(payload.get("enabled", existing.enabled))
    metadata = await _resolve_group_metadata(
        request,
        existing_metadata=existing.metadata,
        raw_metadata=payload.get("metadata"),
        raw_default_prompt=payload.get("default_prompt", ...),
    )

    updated = await repository.update_group(
        group_key,
        name=name,
        mode=mode,
        routing_strategy=strategy,
        enabled=enabled,
        metadata=metadata,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    await _invalidate_runtime_cache(request)
    await _invalidate_prompt_group_cache(request, group_key)
    await _reload_runtime(request)
    before = to_json_value(asdict(existing))
    after = to_json_value(asdict(updated))
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


@router.delete("/ui/api/route-groups/{group_key}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def delete_route_group(request: Request, group_key: str) -> dict[str, bool]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    deleted = await repository.delete_group(group_key)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    await _invalidate_runtime_cache(request)
    await _invalidate_prompt_group_cache(request, group_key)
    await _reload_runtime(request)
    response = {"deleted": True}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_group",
        resource_id=group_key,
        response_payload=response,
    )
    return response


@router.get("/ui/api/route-groups/{group_key}/members", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_route_group_members(request: Request, group_key: str) -> list[dict[str, Any]]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    members = await repository.list_members(group_key)
    return [to_json_value(asdict(member)) for member in members]


@router.post("/ui/api/route-groups/{group_key}/members", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def upsert_route_group_member(request: Request, group_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    deployment_id = str(payload.get("deployment_id") or "").strip()
    if not deployment_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="deployment_id is required")

    enabled = bool(payload.get("enabled", True))
    weight = _validate_int_or_none(payload.get("weight"), field_name="weight")
    priority = _validate_int_or_none(payload.get("priority"), field_name="priority")

    try:
        member = await repository.upsert_member(
            group_key,
            deployment_id=deployment_id,
            enabled=enabled,
            weight=weight,
            priority=priority,
        )
    except Exception as exc:
        if "foreign key" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="deployment_id does not exist") from exc
        raise
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    await _invalidate_runtime_cache(request)
    await _reload_runtime(request)
    response = to_json_value(asdict(member))
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


@router.delete("/ui/api/route-groups/{group_key}/members/{deployment_id}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def delete_route_group_member(request: Request, group_key: str, deployment_id: str) -> dict[str, bool]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    removed = await repository.remove_member(group_key, deployment_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group member not found")

    await _invalidate_runtime_cache(request)
    await _reload_runtime(request)
    response = {"deleted": True}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_group_member",
        resource_id=f"{group_key}:{deployment_id}",
        response_payload=response,
    )
    return response


@router.get("/ui/api/route-groups/{group_key}/policy", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def get_route_group_policy(request: Request, group_key: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    policy = await repository.get_published_policy(group_key)
    if policy is None:
        return {"group_key": group_key, "policy": None}
    return {"group_key": group_key, "policy": to_json_value(asdict(policy))}


@router.get("/ui/api/route-groups/{group_key}/policies", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_route_group_policies(request: Request, group_key: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    policies = await repository.list_policies(group_key)
    return {"group_key": group_key, "policies": [to_json_value(asdict(policy)) for policy in policies]}


@router.post("/ui/api/route-groups/{group_key}/policy/validate", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def validate_route_group_policy(request: Request, group_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    normalized, warnings = _validate_policy_payload(
        payload,
        available_member_ids=await _resolve_member_ids(repository, group_key),
    )
    return {"group_key": group_key, "valid": True, "policy": normalized, "warnings": warnings}


@router.post("/ui/api/route-groups/{group_key}/policy/draft", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def save_route_group_policy_draft(request: Request, group_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    normalized, warnings = _validate_policy_payload(
        payload,
        available_member_ids=await _resolve_member_ids(repository, group_key),
    )
    policy = await repository.save_draft_policy(group_key, normalized)
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    response = {"group_key": group_key, "policy": to_json_value(asdict(policy)), "warnings": warnings}
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


@router.post("/ui/api/route-groups/{group_key}/policy/publish", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def publish_route_group_policy_v2(request: Request, group_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    body = payload or {}

    if body:
        normalized, warnings = _validate_policy_payload(
            body,
            available_member_ids=await _resolve_member_ids(repository, group_key),
        )
        policy = await repository.publish_policy(group_key, normalized, published_by="admin_api")
    else:
        warnings = []
        policy = await repository.publish_latest_draft(group_key, published_by="admin_api")

    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group or draft policy not found")

    await _invalidate_runtime_cache(request)
    await _reload_runtime(request)
    response = {"group_key": group_key, "policy": to_json_value(asdict(policy)), "warnings": warnings}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_ROUTING_UPDATE,
        resource_type="route_policy",
        resource_id=group_key,
        request_payload=body or None,
        response_payload=response,
    )
    return response


@router.post("/ui/api/route-groups/{group_key}/policy/rollback", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def rollback_route_group_policy(request: Request, group_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    if "version" not in payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="version is required")

    version = _validate_int_or_none(payload.get("version"), field_name="version")
    if version is None or version < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="version must be >= 1")

    policy = await repository.rollback_policy(group_key, target_version=version, published_by="admin_api")
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group or policy version not found")

    await _invalidate_runtime_cache(request)
    await _reload_runtime(request)
    response = {"group_key": group_key, "policy": to_json_value(asdict(policy)), "rolled_back_from_version": version}
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


@router.post("/ui/api/route-groups/{group_key}/policy/simulate", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def simulate_route_group_policy(request: Request, group_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    body = payload or {}
    iterations = _validate_iterations(body.get("iterations"))
    metadata = body.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")
    metadata = metadata or {}
    user_id = str(body.get("user_id") or "policy-simulation")

    runtime_groups = await repository.list_runtime_groups()
    available_member_ids = await _resolve_member_ids(repository, group_key)

    warnings: list[str] = []
    prompt_summary: dict[str, Any] | None = None
    if "prompt_ref" in body:
        prompt_ref = parse_prompt_reference(body.get("prompt_ref"))
        if prompt_ref is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="prompt_ref could not be parsed")
        prompt_repository = _prompt_resolution_repository(request)
        if prompt_repository is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Prompt registry repository unavailable",
            )
        resolved_prompt = await prompt_repository.resolve_prompt(
            template_key=prompt_ref.template_key,
            label=prompt_ref.label,
            version=prompt_ref.version,
        )
        if resolved_prompt is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="prompt_ref could not be resolved")
        try:
            metadata, route_preferences = apply_route_preferences_to_metadata(metadata, resolved_prompt.route_preferences)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        prompt_summary = {
            "template_key": resolved_prompt.template_key,
            "version": resolved_prompt.version,
            "label": resolved_prompt.label or prompt_ref.label,
            "route_preferences": route_preferences,
        }
        preferred_group = route_preferences.get("route_group") if isinstance(route_preferences, dict) else None
        if isinstance(preferred_group, str) and preferred_group and preferred_group != group_key:
            warnings.append(
                f"prompt route_preferences.route_group={preferred_group!r} is advisory and does not override simulation group {group_key!r}"
            )

    policy_override = body.get("policy")
    if policy_override is not None:
        if not isinstance(policy_override, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="policy must be an object")
        normalized, policy_warnings = _validate_policy_payload(policy_override, available_member_ids=available_member_ids)
        warnings.extend(policy_warnings)
        runtime_groups = _apply_policy_simulation_override(runtime_groups, group_key=group_key, policy=normalized)

    base_router = getattr(request.app.state, "router", None)
    if base_router is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Router runtime unavailable")

    model_registry = getattr(request.app.state, "model_registry", {})
    state_backend = getattr(request.app.state, "router_state_backend", base_router.state)
    deployment_registry = build_deployment_registry(model_registry, route_groups=runtime_groups)
    simulation_router = Router(
        strategy=base_router.strategy,
        state_backend=state_backend,
        config=RouterConfig(
            num_retries=base_router.config.num_retries,
            retry_after=base_router.config.retry_after,
            timeout=base_router.config.timeout,
            cooldown_time=base_router.config.cooldown_time,
            allowed_fails=base_router.config.allowed_fails,
            enable_pre_call_checks=base_router.config.enable_pre_call_checks,
            model_group_alias=base_router.config.model_group_alias,
            route_group_policies=build_route_group_policies(runtime_groups),
        ),
        deployment_registry=deployment_registry,
    )

    selection_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    no_selection_count = 0
    sample_decision: dict[str, Any] | None = None

    for _ in range(iterations):
        request_context: dict[str, Any] = {"metadata": dict(metadata), "user_id": user_id}
        selected = await simulation_router.select_deployment(group_key, request_context)
        decision = request_context.get("route_decision")
        if isinstance(decision, dict):
            reason = str(decision.get("reason") or "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if sample_decision is None:
                sample_decision = to_json_value(decision)
        if selected is None:
            no_selection_count += 1
            continue
        selection_counts[selected.deployment_id] = selection_counts.get(selected.deployment_id, 0) + 1

    selections = [
        {
            "deployment_id": deployment_id,
            "count": count,
            "ratio": count / iterations if iterations > 0 else 0.0,
        }
        for deployment_id, count in sorted(selection_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    return {
        "group_key": group_key,
        "iterations": iterations,
        "warnings": warnings,
        "prompt": prompt_summary,
        "effective_metadata": metadata,
        "summary": {
            "selected_requests": iterations - no_selection_count,
            "no_selection_requests": no_selection_count,
        },
        "reason_counts": reason_counts,
        "selections": selections,
        "sample_decision": sample_decision,
    }


@router.put("/ui/api/route-groups/{group_key}/policy", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def publish_route_group_policy(request: Request, group_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    group = await repository.get_group(group_key)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")
    normalized, warnings = _validate_policy_payload(
        payload,
        available_member_ids=await _resolve_member_ids(repository, group_key),
    )
    policy = await repository.publish_policy(group_key, normalized, published_by="admin_api")
    if policy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route group not found")

    await _invalidate_runtime_cache(request)
    await _reload_runtime(request)
    response = {"group_key": group_key, "policy": to_json_value(asdict(policy)), "warnings": warnings}
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
