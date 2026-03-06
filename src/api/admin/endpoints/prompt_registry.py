from __future__ import annotations

from dataclasses import asdict
from time import perf_counter
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from src.api.admin.endpoints.common import emit_admin_mutation_audit, to_json_value
from src.audit.actions import AuditAction
from src.auth.roles import Permission
from src.db.prompt_registry import PromptRegistryRepository
from src.middleware.admin import require_admin_permission
from src.services.prompt_registry import PromptRegistryService, normalize_route_preferences
from src.services.prompt_rendering import detect_secret_like_content

router = APIRouter(tags=["Admin Prompt Registry"])

_TEMPLATE_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{1,127}$")
_ALLOWED_SCOPE_TYPES = {"key", "team", "org", "group"}


def _repository_or_503(request: Request) -> PromptRegistryRepository:
    repository = getattr(request.app.state, "prompt_registry_repository", None)
    if repository is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Prompt registry repository unavailable")
    return repository


def _service(request: Request) -> PromptRegistryService | None:
    candidate = getattr(request.app.state, "prompt_registry_service", None)
    if candidate is not None and callable(getattr(candidate, "invalidate_all", None)):
        return candidate
    return None


def _validate_template_key(value: Any) -> str:
    template_key = str(value or "").strip()
    if not template_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="template_key is required")
    if not _TEMPLATE_KEY_RE.match(template_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="template_key must match ^[a-zA-Z0-9][a-zA-Z0-9._:-]{1,127}$",
        )
    return template_key


def _validate_label(value: Any) -> str:
    label = str(value or "").strip()
    if not label:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="label is required")
    return label


def _validate_scope_type(value: Any) -> str:
    scope_type = str(value or "").strip().lower()
    if scope_type not in _ALLOWED_SCOPE_TYPES:
        allowed = ", ".join(sorted(_ALLOWED_SCOPE_TYPES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"scope_type must be one of: {allowed}")
    return scope_type


def _validate_version(value: Any, *, field_name: str = "version") -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer") from exc
    if parsed < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be >= 1")
    return parsed


def _validate_template_body(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="template_body must be an object")
    findings = detect_secret_like_content(value)
    if findings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"template_body contains secret-like content ({', '.join(sorted(findings))})",
        )
    return value


async def _invalidate_template_cache(request: Request, template_key: str) -> None:
    service = _service(request)
    if service is None:
        return
    await service.invalidate_template(template_key)


async def _invalidate_scope_cache(request: Request, *, scope_type: str, scope_id: str) -> None:
    service = _service(request)
    if service is None:
        return
    await service.invalidate_scope(scope_type=scope_type, scope_id=scope_id)


async def _invalidate_all_cache(request: Request) -> None:
    service = _service(request)
    if service is None:
        return
    await service.invalidate_all()


@router.get("/ui/api/prompt-registry/templates", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_prompt_templates(
    request: Request,
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    items, total = await repository.list_templates(search=search, limit=limit, offset=offset)
    data = [to_json_value(asdict(item)) for item in items]
    return {"data": data, "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total}}


@router.get("/ui/api/prompt-registry/templates/{template_key}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def get_prompt_template(request: Request, template_key: str) -> dict[str, Any]:
    repository = _repository_or_503(request)
    template = await repository.get_template(template_key)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")
    versions = await repository.list_versions(template_key)
    labels = await repository.list_labels(template_key)
    bindings, _ = await repository.list_bindings(template_key=template_key, limit=200, offset=0)
    return {
        "template": to_json_value(asdict(template)),
        "versions": [to_json_value(asdict(item)) for item in versions],
        "labels": [to_json_value(asdict(item)) for item in labels],
        "bindings": [to_json_value(asdict(item)) for item in bindings],
    }


@router.post("/ui/api/prompt-registry/templates", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def create_prompt_template(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    template_key = _validate_template_key(payload.get("template_key") or payload.get("key"))
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    description = str(payload.get("description")).strip() if payload.get("description") is not None else None
    owner_scope = str(payload.get("owner_scope")).strip() if payload.get("owner_scope") is not None else None
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")

    try:
        created = await repository.create_template(
            template_key=template_key,
            name=name,
            description=description,
            owner_scope=owner_scope,
            metadata=metadata,
        )
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Prompt template already exists") from exc
        raise

    response = to_json_value(asdict(created))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_template",
        resource_id=template_key,
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.put("/ui/api/prompt-registry/templates/{template_key}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def update_prompt_template(request: Request, template_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    existing = await repository.get_template(template_key)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")

    name = str(payload.get("name") or existing.name).strip()
    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="name is required")
    description = (
        str(payload.get("description")).strip()
        if "description" in payload and payload.get("description") is not None
        else existing.description
    )
    owner_scope = (
        str(payload.get("owner_scope")).strip()
        if "owner_scope" in payload and payload.get("owner_scope") is not None
        else existing.owner_scope
    )
    metadata = payload.get("metadata", existing.metadata)
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")

    updated = await repository.update_template(
        template_key,
        name=name,
        description=description,
        owner_scope=owner_scope,
        metadata=metadata,
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")

    response = to_json_value(asdict(updated))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_template",
        resource_id=template_key,
        request_payload=payload,
        response_payload=response,
        before=to_json_value(asdict(existing)),
        after=response,
    )
    return response


@router.delete("/ui/api/prompt-registry/templates/{template_key}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def delete_prompt_template(request: Request, template_key: str) -> dict[str, bool]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    deleted = await repository.delete_template(template_key)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")
    await _invalidate_all_cache(request)
    response = {"deleted": True}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_template",
        resource_id=template_key,
        response_payload=response,
    )
    return response


@router.post("/ui/api/prompt-registry/templates/{template_key}/versions", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def create_prompt_version(request: Request, template_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    if await repository.get_template(template_key) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")

    template_body = _validate_template_body(payload.get("template_body"))
    variables_schema = payload.get("variables_schema")
    if variables_schema is not None and not isinstance(variables_schema, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="variables_schema must be an object")
    model_hints = payload.get("model_hints")
    if model_hints is not None and not isinstance(model_hints, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model_hints must be an object")
    raw_route_preferences = payload.get("route_preferences")
    try:
        route_preferences = normalize_route_preferences(raw_route_preferences)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    created = await repository.create_version(
        template_key,
        template_body=template_body,
        variables_schema=variables_schema,
        model_hints=model_hints,
        route_preferences=route_preferences,
        status="draft",
    )
    if created is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")

    if bool(payload.get("publish")):
        published = await repository.publish_version(template_key, version=created.version, published_by="admin_api")
        if published is not None:
            created = published

    await _invalidate_template_cache(request, template_key)
    response = to_json_value(asdict(created))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_version",
        resource_id=f"{template_key}:v{created.version}",
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.post("/ui/api/prompt-registry/templates/{template_key}/versions/{version}/publish", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def publish_prompt_version(request: Request, template_key: str, version: int) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    parsed_version = _validate_version(version)
    published = await repository.publish_version(template_key, version=parsed_version, published_by="admin_api")
    if published is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt version not found")
    await _invalidate_template_cache(request, template_key)
    response = to_json_value(asdict(published))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_version",
        resource_id=f"{template_key}:v{parsed_version}",
        response_payload=response,
    )
    return response


@router.get("/ui/api/prompt-registry/templates/{template_key}/labels", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_prompt_labels(request: Request, template_key: str) -> list[dict[str, Any]]:
    repository = _repository_or_503(request)
    labels = await repository.list_labels(template_key)
    return [to_json_value(asdict(item)) for item in labels]


@router.post("/ui/api/prompt-registry/templates/{template_key}/labels", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def assign_prompt_label(request: Request, template_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    label = _validate_label(payload.get("label"))
    version = _validate_version(payload.get("version"))
    require_approval = bool(payload.get("require_approval", False))
    approved_by = str(payload.get("approved_by") or "").strip()
    if require_approval and label.lower() in {"production", "prod"} and not approved_by:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="approved_by is required when approval is enabled")

    assigned = await repository.assign_label(template_key, label=label, version=version)
    if assigned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template or version not found")

    await _invalidate_template_cache(request, template_key)
    response = to_json_value(asdict(assigned))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_label",
        resource_id=f"{template_key}:{label}",
        request_payload={**payload, "approved_by": approved_by or None, "approval_used": require_approval},
        response_payload=response,
    )
    return response


@router.delete("/ui/api/prompt-registry/templates/{template_key}/labels/{label}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def delete_prompt_label(request: Request, template_key: str, label: str) -> dict[str, bool]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    removed = await repository.delete_label(template_key, label)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt label not found")
    await _invalidate_template_cache(request, template_key)
    response = {"deleted": True}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_label",
        resource_id=f"{template_key}:{label}",
        response_payload=response,
    )
    return response


@router.get("/ui/api/prompt-registry/bindings", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def list_prompt_bindings(
    request: Request,
    scope_type: str | None = Query(default=None),
    scope_id: str | None = Query(default=None),
    template_key: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repository = _repository_or_503(request)
    parsed_scope_type = _validate_scope_type(scope_type) if scope_type else None
    items, total = await repository.list_bindings(
        scope_type=parsed_scope_type,
        scope_id=scope_id,
        template_key=template_key,
        limit=limit,
        offset=offset,
    )
    return {
        "data": [to_json_value(asdict(item)) for item in items],
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.post("/ui/api/prompt-registry/bindings", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def upsert_prompt_binding(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    scope_type = _validate_scope_type(payload.get("scope_type"))
    scope_id = str(payload.get("scope_id") or "").strip()
    if not scope_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope_id is required")
    template_key = _validate_template_key(payload.get("template_key"))
    label = _validate_label(payload.get("label"))
    try:
        priority = int(payload.get("priority", 100))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="priority must be an integer") from exc
    enabled = bool(payload.get("enabled", True))
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata must be an object")

    binding = await repository.upsert_binding(
        scope_type=scope_type,
        scope_id=scope_id,
        template_key=template_key,
        label=label,
        priority=priority,
        enabled=enabled,
        metadata=metadata,
    )
    if binding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")

    await _invalidate_scope_cache(request, scope_type=scope_type, scope_id=scope_id)
    response = to_json_value(asdict(binding))
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_binding",
        resource_id=f"{scope_type}:{scope_id}:{template_key}:{label}",
        request_payload=payload,
        response_payload=response,
    )
    return response


@router.delete("/ui/api/prompt-registry/bindings/{binding_id}", dependencies=[Depends(require_admin_permission(Permission.CONFIG_UPDATE))])
async def delete_prompt_binding(request: Request, binding_id: str) -> dict[str, bool]:
    request_start = perf_counter()
    repository = _repository_or_503(request)
    removed = await repository.delete_binding(binding_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt binding not found")
    await _invalidate_all_cache(request)
    response = {"deleted": True}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_PROMPT_REGISTRY_UPDATE,
        resource_type="prompt_binding",
        resource_id=binding_id,
        response_payload=response,
    )
    return response


@router.post("/ui/api/prompt-registry/render", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def dry_run_prompt_render(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    service = _service(request)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Prompt registry service unavailable")

    template_key = _validate_template_key(payload.get("template_key"))
    label = str(payload.get("label")).strip() if payload.get("label") is not None else None
    version = _validate_version(payload.get("version")) if payload.get("version") is not None else None
    variables = payload.get("variables")
    if variables is not None and not isinstance(variables, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="variables must be an object")
    variables = variables or {}
    try:
        return await service.dry_run_render(template_key=template_key, label=label, version=version, variables=variables)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/ui/api/prompt-registry/preview-resolution", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def preview_prompt_resolution(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    service = _service(request)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Prompt registry service unavailable")
    api_key = str(payload.get("api_key")).strip() if payload.get("api_key") is not None else None
    team_id = str(payload.get("team_id")).strip() if payload.get("team_id") is not None else None
    organization_id = str(payload.get("organization_id")).strip() if payload.get("organization_id") is not None else None
    route_group_key = str(payload.get("route_group_key")).strip() if payload.get("route_group_key") is not None else None
    return await service.resolve_binding_preview(
        api_key=api_key,
        team_id=team_id,
        organization_id=organization_id,
        route_group_key=route_group_key,
    )
