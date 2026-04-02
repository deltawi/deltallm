from __future__ import annotations

from time import perf_counter
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel

from src.api.admin.endpoints.common import model_entries
from src.api.audit import emit_control_audit_event
from src.audit.actions import AuditAction
from src.config import ModelMode
from src.config_runtime.models import ModelHotReloadManager
from src.middleware.admin import require_authenticated, require_master_key
from src.providers.healthcheck import probe_provider_health
from src.providers.model_discovery import discover_provider_models
from src.providers.resolution import provider_presets, validate_provider_mode_compatibility
from src.router import build_deployment_registry
from src.services.asset_binding_mirror import reload_callable_target_grants_for_app
from src.services.callable_targets import build_callable_target_catalog
from src.services.model_deployments import DuplicateModelNameError, ensure_model_name_available
from src.services.organization_callable_target_sync import sync_auto_follow_organization_bindings

router = APIRouter(tags=["Models"])


class ProviderModelDiscoveryRequest(BaseModel):
    provider: str
    mode: ModelMode | None = None
    api_key: str | None = None
    api_base: str | None = None
    api_version: str | None = None


def _find_runtime_deployment(app: Any, deployment_id: str) -> Any | None:
    registry = getattr(getattr(app.state, "router", None), "deployment_registry", {}) or {}
    for deployments in registry.values():
        for deployment in deployments:
            if deployment.deployment_id == deployment_id:
                return deployment
    return None


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


async def _deployment_health_flags(app: Any, deployment_ids: list[str]) -> dict[str, bool]:
    if not deployment_ids:
        return {}

    health_backend = getattr(app.state, "router_state_backend", None)
    if health_backend is None:
        return {deployment_id: True for deployment_id in deployment_ids}

    health_by_deployment = await health_backend.get_health_batch(deployment_ids)
    cooldown_by_deployment = await health_backend.get_cooldown_batch(deployment_ids)
    return {
        deployment_id: str(health_by_deployment.get(deployment_id, {}).get("healthy", "true")) != "false"
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

    health = await health_backend.get_health(deployment_id)
    in_cooldown = await health_backend.is_cooled_down(deployment_id)
    healthy = str(health.get("healthy", "true")) != "false" and not in_cooldown
    return {
        "healthy": healthy,
        "in_cooldown": in_cooldown,
        "consecutive_failures": _to_int_or_none(health.get("consecutive_failures")) or 0,
        "last_error": health.get("last_error") or None,
        "last_error_at": _to_int_or_none(health.get("last_error_at")),
        "last_success_at": _to_int_or_none(health.get("last_success_at")),
    }


def _rebuild_runtime_registry(app: Any) -> None:
    model_registry = getattr(app.state, "model_registry", {})
    route_groups = list(getattr(app.state, "route_groups", []))
    app.state.callable_target_catalog = build_callable_target_catalog(
        model_registry,
        route_groups,
    )
    rebuilt = build_deployment_registry(model_registry, route_groups=route_groups)

    runtime_registry = getattr(getattr(app.state, "router", None), "deployment_registry", None)
    if isinstance(runtime_registry, dict):
        runtime_registry.clear()
        runtime_registry.update(rebuilt)

    for attr in ("failover_manager", "router_health_handler", "background_health_checker"):
        holder = getattr(app.state, attr, None)
        registry = getattr(holder, "registry", None)
        if isinstance(registry, dict) and registry is not runtime_registry:
            registry.clear()
            registry.update(rebuilt)


async def _invalidate_route_group_runtime_cache(app: Any) -> None:
    cache = getattr(app.state, "route_group_runtime_cache", None)
    invalidate = getattr(cache, "invalidate", None)
    if callable(invalidate):
        await invalidate()


async def _sync_auto_follow_org_bindings(app: Any) -> None:
    changed = await sync_auto_follow_organization_bindings(
        db=getattr(getattr(app.state, "prisma_manager", None), "client", None),
        callable_target_binding_repository=getattr(app.state, "callable_target_binding_repository", None),
        route_group_repository=getattr(app.state, "route_group_repository", None),
        callable_target_catalog=getattr(app.state, "callable_target_catalog", None),
    )
    if changed > 0:
        await reload_callable_target_grants_for_app(app)


def _validate_model_config_or_400(model_config: dict[str, Any]) -> None:
    try:
        validate_provider_mode_compatibility(model_config)
    except DuplicateModelNameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _normalized_model_payload_or_400(
    payload: dict[str, Any],
    *,
    existing_model_name: str | None = None,
    existing_params: dict[str, Any] | None = None,
    existing_model_info: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    model_name = str(payload.get("model_name") or existing_model_name or "").strip()
    if not model_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model_name is required")

    raw_params = payload.get("deltallm_params")
    if raw_params is None:
        params = dict(existing_params or {})
    elif isinstance(raw_params, dict):
        params = dict(raw_params)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="deltallm_params must be an object")

    provider = str(params.get("provider") or "").strip().lower()
    model = str(params.get("model") or "").strip()
    api_base = str(params.get("api_base") or "").strip()

    if not provider:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="provider is required")
    if not model:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="deltallm_params.model is required")
    if not api_base:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="deltallm_params.api_base is required")

    params["provider"] = provider
    params["model"] = model
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
        model_info = dict(existing_model_info or {})

    return model_name, params, model_info


@router.get("/ui/api/models", dependencies=[Depends(require_authenticated)])
async def list_models(
    request: Request,
    search: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    health_backend = getattr(request.app.state, "router_state_backend", None)
    entries = model_entries(request.app)

    if search:
        q = search.lower()
        entries = [e for e in entries if q in e["model_name"].lower() or q in e["deployment_id"].lower() or q in e.get("provider", "").lower()]
    if provider:
        p = provider.lower()
        entries = [e for e in entries if e.get("provider", "").lower() == p]
    if mode:
        m = mode.lower()
        entries = [e for e in entries if (e.get("mode") or "chat").lower() == m]

    total = len(entries)
    page = entries[offset: offset + limit]

    for entry in page:
        healthy = True
        if health_backend is not None:
            health = await health_backend.get_health(entry["deployment_id"])
            healthy = str(health.get("healthy", "true")) != "false"
        entry["healthy"] = healthy

    return {
        "data": page,
        "pagination": {"total": total, "limit": limit, "offset": offset, "has_more": offset + limit < total},
    }


@router.get("/ui/api/models/provider-health-summary", dependencies=[Depends(require_authenticated)])
async def provider_health_summary(request: Request) -> dict[str, Any]:
    entries = model_entries(request.app)
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
        if healthy_by_deployment.get(deployment_id, True):
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


@router.post("/ui/api/provider-models/discover", dependencies=[Depends(require_authenticated)])
async def discover_models_for_provider(request: Request, payload: ProviderModelDiscoveryRequest) -> dict[str, Any]:
    return await discover_provider_models(
        request.app.state.http_client,
        provider=payload.provider,
        mode=payload.mode,
        api_key=payload.api_key,
        api_base=payload.api_base,
        api_version=payload.api_version,
        default_openai_base_url=getattr(request.app.state.settings, "openai_base_url", "https://api.openai.com/v1"),
    )


@router.get("/ui/api/models/{deployment_id:path}", dependencies=[Depends(require_authenticated)])
async def get_model(request: Request, deployment_id: str) -> dict[str, Any]:
    entries = model_entries(request.app)
    for entry in entries:
        if entry["deployment_id"] == deployment_id:
            health = await _serialize_deployment_health(request.app, deployment_id)
            entry["healthy"] = health["healthy"]
            entry["health"] = health
            return entry
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")


@router.post("/ui/api/models/{deployment_id:path}/health-check", dependencies=[Depends(require_authenticated)])
async def check_model_health(request: Request, deployment_id: str) -> dict[str, Any]:
    deployment = _find_runtime_deployment(request.app, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    health_checker = getattr(request.app.state, "background_health_checker", None)
    if health_checker is not None:
        result = await health_checker.check_deployment_once(deployment)
    else:
        result = await probe_provider_health(
            request.app.state.http_client,
            deployment.deltallm_params,
            default_openai_base_url=request.app.state.settings.openai_base_url,
        )

    health = await _serialize_deployment_health(request.app, deployment_id)
    return {
        "deployment_id": deployment_id,
        "healthy": health["healthy"],
        "health": health,
        "message": "Health check passed" if result.healthy else (result.error or "Health check failed"),
        "status_code": result.status_code,
        "checked_at": result.checked_at,
    }


@router.post("/ui/api/models", dependencies=[Depends(require_master_key)])
async def create_model(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    model_name, deltallm_params, model_info = _normalized_model_payload_or_400(payload)
    deployment_id = str(payload.get("deployment_id") or f"{model_name}-{secrets.token_hex(4)}")
    try:
        ensure_model_name_available(getattr(request.app.state, "model_registry", {}) or {}, model_name=model_name)
    except DuplicateModelNameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    model_config = {
        "deployment_id": deployment_id,
        "model_name": model_name,
        "deltallm_params": deltallm_params,
        "model_info": model_info,
    }
    _validate_model_config_or_400(model_config)

    hot_reload: ModelHotReloadManager | None = getattr(request.app.state, "model_hot_reload_manager", None)
    if hot_reload is not None:
        deployment_id = await hot_reload.add_model(model_config, updated_by="admin_api")
    else:
        request.app.state.model_registry.setdefault(model_name, []).append(
            {"deployment_id": deployment_id, "deltallm_params": deltallm_params, "model_info": model_info}
        )
        await _invalidate_route_group_runtime_cache(request.app)
        _rebuild_runtime_registry(request.app)
        await _sync_auto_follow_org_bindings(request.app)

    response = {
        "deployment_id": deployment_id,
        "model_name": model_name,
        "deltallm_params": deltallm_params,
        "model_info": model_info,
    }
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


@router.put("/ui/api/models/{deployment_id:path}", dependencies=[Depends(require_master_key)])
async def update_model(request: Request, deployment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    hot_reload: ModelHotReloadManager | None = getattr(request.app.state, "model_hot_reload_manager", None)
    registry: dict[str, list[dict[str, Any]]] = request.app.state.model_registry

    found_model_name: str | None = None
    found_deployment: dict[str, Any] | None = None
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

    new_model_name, deltallm_params, model_info = _normalized_model_payload_or_400(
        payload,
        existing_model_name=found_model_name,
        existing_params=found_deployment.get("deltallm_params", {}),
        existing_model_info=found_deployment.get("model_info", {}),
    )
    try:
        ensure_model_name_available(
            getattr(request.app.state, "model_registry", {}) or {},
            model_name=new_model_name,
            exclude_deployment_id=deployment_id,
        )
    except DuplicateModelNameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    model_config = {
        "deployment_id": deployment_id,
        "model_name": new_model_name,
        "deltallm_params": deltallm_params,
        "model_info": model_info,
    }
    _validate_model_config_or_400(model_config)

    if hot_reload is not None:
        updated = await hot_reload.update_model(
            deployment_id,
            model_config,
            updated_by="admin_api",
        )
        if not updated:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    else:
        deployments = registry.get(found_model_name, [])
        for idx, deployment in enumerate(deployments):
            if str(deployment.get("deployment_id") or f"{found_model_name}-{idx}") == deployment_id:
                del deployments[idx]
                if not deployments:
                    registry.pop(found_model_name, None)
                break
        registry.setdefault(new_model_name, []).append(
            {"deployment_id": deployment_id, "deltallm_params": deltallm_params, "model_info": model_info}
        )
        await _invalidate_route_group_runtime_cache(request.app)
        _rebuild_runtime_registry(request.app)
        await _sync_auto_follow_org_bindings(request.app)

    response = {
        "deployment_id": deployment_id,
        "model_name": new_model_name,
        "deltallm_params": deltallm_params,
        "model_info": model_info,
    }
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


@router.delete("/ui/api/models/{deployment_id:path}", dependencies=[Depends(require_master_key)])
async def delete_model(request: Request, deployment_id: str) -> dict[str, bool]:
    request_start = perf_counter()
    hot_reload: ModelHotReloadManager | None = getattr(request.app.state, "model_hot_reload_manager", None)

    if hot_reload is not None:
        removed = await hot_reload.remove_model(deployment_id, updated_by="admin_api")
        if not removed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
        response = {"deleted": True}
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
            response = {"deleted": True}
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
