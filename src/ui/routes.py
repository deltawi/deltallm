from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
import logging
from pathlib import Path
import secrets
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from src.audit.actions import AuditAction
from src.middleware.admin import require_admin_permission, require_master_key, require_authenticated
from src.api.audit import emit_control_audit_event
from src.auth.roles import Permission
from src.api.admin.endpoints.common import get_auth_scope
from src.billing.spend_read import SpendReadSource, apply_org_scope, get_spend_read_source
from src.config_runtime.models import ModelHotReloadManager
from src.providers.healthcheck import probe_provider_health
from src.providers.resolution import provider_presets, resolve_provider, validate_provider_mode_compatibility
from src.router import build_deployment_registry
from src.services.model_deployments import DuplicateModelNameError, ensure_model_name_available

ui_router = APIRouter(tags=["UI"])
logger = logging.getLogger(__name__)


def _dist_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "ui" / "dist"


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_to_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_json_value(v) for k, v in value.items()}
    return value


def _db_or_503(request: Request) -> Any:
    db = getattr(getattr(request.app.state, "prisma_manager", None), "client", None)
    if db is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable")
    return db


def _log_admin_query_timing(name: str, started_at: float, **context: Any) -> None:
    elapsed_ms = int((perf_counter() - started_at) * 1000)
    if not logger.isEnabledFor(logging.DEBUG) and elapsed_ms < 500:
        return

    details = " ".join(f"{key}={value}" for key, value in context.items() if value not in (None, "", []))
    message = f"Admin query completed: name={name} latency_ms={elapsed_ms}"
    if details:
        message = f"{message} {details}"

    if elapsed_ms >= 500:
        logger.info(message)
    else:
        logger.debug(message)


def _model_entries(app: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    registry: dict[str, list[dict[str, Any]]] = getattr(app.state, "model_registry", {})
    for model_name, deployments in registry.items():
        for index, deployment in enumerate(deployments):
            deployment_id = str(deployment.get("deployment_id") or f"{model_name}-{index}")
            params = dict(deployment.get("deltallm_params", {}))
            model_info = dict(deployment.get("model_info", {}))
            entries.append(
                {
                    "deployment_id": deployment_id,
                    "model_name": model_name,
                    "provider": resolve_provider(params),
                    "mode": model_info.get("mode", "chat"),
                    "deltallm_params": params,
                    "model_info": model_info,
                }
            )
    return entries


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


@ui_router.get("/ui/api/models", dependencies=[Depends(require_authenticated)])
async def list_models(
    request: Request,
    search: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    health_backend = getattr(request.app.state, "router_state_backend", None)
    entries = _model_entries(request.app)

    if search:
        q = search.lower()
        entries = [e for e in entries if q in e["model_name"].lower() or q in e["deployment_id"].lower() or q in e.get("provider", "").lower()]
    if provider:
        p = provider.lower()
        entries = [e for e in entries if e.get("provider", "").lower() == p]
    if mode:
        m = mode.lower()
        entries = [e for e in entries if (e.get("model_info", {}).get("mode") or "chat").lower() == m]

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


@ui_router.get("/ui/api/provider-presets", dependencies=[Depends(require_authenticated)])
async def list_provider_presets() -> dict[str, Any]:
    return {"data": provider_presets()}


@ui_router.get("/ui/api/models/{deployment_id:path}", dependencies=[Depends(require_authenticated)])
async def get_model(request: Request, deployment_id: str) -> dict[str, Any]:
    entries = _model_entries(request.app)
    for entry in entries:
        if entry["deployment_id"] == deployment_id:
            health = await _serialize_deployment_health(request.app, deployment_id)
            entry["healthy"] = health["healthy"]
            entry["health"] = health
            return entry
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")


@ui_router.post("/ui/api/models/{deployment_id:path}/health-check", dependencies=[Depends(require_authenticated)])
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


@ui_router.post("/ui/api/models", dependencies=[Depends(require_master_key)])
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


@ui_router.put("/ui/api/models/{deployment_id:path}", dependencies=[Depends(require_master_key)])
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
        for idx, d in enumerate(deployments):
            if str(d.get("deployment_id") or f"{found_model_name}-{idx}") == deployment_id:
                del deployments[idx]
                if not deployments:
                    registry.pop(found_model_name, None)
                break
        registry.setdefault(new_model_name, []).append(
            {"deployment_id": deployment_id, "deltallm_params": deltallm_params, "model_info": model_info}
        )
        await _invalidate_route_group_runtime_cache(request.app)
        _rebuild_runtime_registry(request.app)

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


@ui_router.delete("/ui/api/models/{deployment_id:path}", dependencies=[Depends(require_master_key)])
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


def _date_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


def _grouped_spend_config(group_by: str, source: SpendReadSource) -> dict[str, Any]:
    if group_by == "model":
        return {
            "group_expr": "s.model",
            "display_expr": "NULL",
            "group_by_exprs": ["s.model"],
            "search_clause": "s.model ILIKE ${i}",
        }
    if group_by == "organization":
        return {
            "joins": [
                "LEFT JOIN deltallm_teamtable t ON t.team_id = s.team_id",
                "LEFT JOIN deltallm_organizationtable o ON o.organization_id = t.organization_id",
            ],
            "group_expr": "COALESCE(t.organization_id, 'none')",
            "display_expr": "NULLIF(TRIM(COALESCE(o.organization_name, '')), '')",
            "group_by_exprs": [
                "COALESCE(t.organization_id, 'none')",
                "NULLIF(TRIM(COALESCE(o.organization_name, '')), '')",
            ],
            "search_clause": "(COALESCE(t.organization_id, 'none') ILIKE ${i} OR COALESCE(o.organization_name, '') ILIKE ${i})",
        }
    if group_by == "team":
        return {
            "joins": ["LEFT JOIN deltallm_teamtable t ON t.team_id = s.team_id"],
            "group_expr": "COALESCE(s.team_id, 'none')",
            "display_expr": "NULLIF(TRIM(COALESCE(t.team_alias, '')), '')",
            "group_by_exprs": [
                "COALESCE(s.team_id, 'none')",
                "NULLIF(TRIM(COALESCE(t.team_alias, '')), '')",
            ],
            "search_clause": "(COALESCE(s.team_id, 'none') ILIKE ${i} OR COALESCE(t.team_alias, '') ILIKE ${i})",
        }
    if group_by == "api_key":
        return {
            "joins": ["LEFT JOIN deltallm_verificationtoken vt ON vt.token = s.api_key"],
            "group_expr": "s.api_key",
            "display_expr": "NULLIF(TRIM(COALESCE(vt.key_name, '')), '')",
            "group_by_exprs": [
                "s.api_key",
                "NULLIF(TRIM(COALESCE(vt.key_name, '')), '')",
            ],
            "search_clause": "(s.api_key ILIKE ${i} OR COALESCE(vt.key_name, '') ILIKE ${i})",
        }
    if group_by == "provider":
        return {
            "group_expr": "COALESCE(s.api_base, 'unknown')",
            "display_expr": "NULL",
            "group_by_exprs": ["COALESCE(s.api_base, 'unknown')"],
            "search_clause": "COALESCE(s.api_base, 'unknown') ILIKE ${i}",
        }
    return {
        "group_expr": f"COALESCE({source.column('user_column', table_alias='s')}, 'anonymous')",
        "display_expr": "NULL",
        "group_by_exprs": [f"COALESCE({source.column('user_column', table_alias='s')}, 'anonymous')"],
        "search_clause": f"COALESCE({source.column('user_column', table_alias='s')}, 'anonymous') ILIKE ${{i}}",
    }


@ui_router.get("/ui/api/spend/summary", dependencies=[Depends(require_admin_permission(Permission.SPEND_READ))])
async def spend_summary(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.SPEND_READ)
    db = _db_or_503(request)
    source = get_spend_read_source()
    clauses: list[str] = []
    params: list[Any] = []

    if start_date is not None:
        params.append(_date_start(start_date))
        clauses.append(f"start_time >= ${len(params)}::timestamp")
    if end_date is not None:
        params.append(_date_end(end_date))
        clauses.append(f"start_time <= ${len(params)}::timestamp")
    if not scope.is_platform_admin:
        apply_org_scope(clauses=clauses, params=params, org_ids=scope.org_ids, source=source)

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    rows = await db.query_raw(
        f"""
        SELECT
            COALESCE(SUM(spend), 0) AS total_spend,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM({source.prompt_tokens_column}), 0) AS prompt_tokens,
            COALESCE(SUM({source.completion_tokens_column}), 0) AS completion_tokens,
            COUNT(*) AS total_requests
        FROM {source.table}
        {where_sql}
        """,
        *params,
    )
    _log_admin_query_timing(
        "spend_summary",
        started_at,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
        scoped=not scope.is_platform_admin,
    )
    return _to_json_value(dict(rows[0] if rows else {}))


@ui_router.get("/ui/api/spend/report", dependencies=[Depends(require_admin_permission(Permission.SPEND_READ))])
async def spend_report(
    request: Request,
    group_by: str = Query(default="day", pattern="^(model|provider|day|user|team|organization|api_key)$"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=5, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.SPEND_READ)
    db = _db_or_503(request)
    source = get_spend_read_source()
    if group_by == "day":
        clauses: list[str] = []
        params: list[Any] = []
        if start_date is not None:
            params.append(_date_start(start_date))
            clauses.append(f"start_time >= ${len(params)}::timestamp")
        if end_date is not None:
            params.append(_date_end(end_date))
            clauses.append(f"start_time <= ${len(params)}::timestamp")
        if not scope.is_platform_admin:
            apply_org_scope(clauses=clauses, params=params, org_ids=scope.org_ids, source=source)

        where_sql = ""
        if clauses:
            where_sql = " WHERE " + " AND ".join(clauses)

        rows = await db.query_raw(
            f"""
            SELECT
                DATE(start_time) AS group_key,
                COALESCE(SUM(spend), 0) AS total_spend,
                COUNT(*) AS request_count,
                COALESCE(SUM(total_tokens), 0) AS total_tokens
            FROM {source.table}
            {where_sql}
            GROUP BY DATE(start_time)
            ORDER BY group_key ASC
            """,
            *params,
        )
        _log_admin_query_timing(
            "spend_report_day",
            started_at,
            start_date=start_date.isoformat() if start_date else None,
            end_date=end_date.isoformat() if end_date else None,
            scoped=not scope.is_platform_admin,
        )
        return {
            "group_by": group_by,
            "breakdown": [_to_json_value(dict(row)) for row in rows],
        }

    config = _grouped_spend_config(group_by, source)
    clauses: list[str] = []
    params: list[Any] = []
    joins = list(config.get("joins", []))
    group_expr = config["group_expr"]
    display_expr = config["display_expr"]
    group_by_sql = ", ".join(config.get("group_by_exprs", [group_expr]))

    if start_date is not None:
        params.append(_date_start(start_date))
        clauses.append(f"s.start_time >= ${len(params)}::timestamp")
    if end_date is not None:
        params.append(_date_end(end_date))
        clauses.append(f"s.start_time <= ${len(params)}::timestamp")
    if search:
        params.append(f"%{search.strip()}%")
        clauses.append(config["search_clause"].format(i=len(params)))
    if not scope.is_platform_admin:
        apply_org_scope(clauses=clauses, params=params, org_ids=scope.org_ids, source=source, table_alias="s")

    join_sql = ""
    if joins:
        join_sql = "\n        " + "\n        ".join(joins)

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    page_params = [*params, limit, offset]
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    rows = await db.query_raw(
        f"""
        WITH grouped AS (
            SELECT
                {group_expr} AS group_key,
                {display_expr} AS display_name,
                COALESCE(SUM(s.spend), 0) AS total_spend,
                COUNT(*) AS request_count,
                COALESCE(SUM(s.total_tokens), 0) AS total_tokens
            FROM {source.table} s
            {join_sql}
            {where_sql}
            GROUP BY {group_by_sql}
        )
        SELECT
            group_key,
            display_name,
            total_spend,
            request_count,
            total_tokens,
            COUNT(*) OVER() AS total_count
        FROM grouped
        ORDER BY total_spend DESC, group_key ASC
        LIMIT ${limit_idx}
        OFFSET ${offset_idx}
        """,
        *page_params,
    )
    total = int((rows[0] if rows else {}).get("total_count") or 0)
    _log_admin_query_timing(
        "spend_report_grouped",
        started_at,
        group_by=group_by,
        search=search.strip() if search else None,
        limit=limit,
        offset=offset,
        scoped=not scope.is_platform_admin,
    )

    return {
        "group_by": group_by,
        "data": [_to_json_value({k: v for k, v in dict(row).items() if k != "total_count"}) for row in rows],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@ui_router.get("/ui/api/logs", dependencies=[Depends(require_admin_permission(Permission.SPEND_READ))])
async def request_logs(
    request: Request,
    model: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    started_at = perf_counter()
    scope = get_auth_scope(request, authorization, x_master_key, required_permission=Permission.SPEND_READ)
    db = _db_or_503(request)
    source = get_spend_read_source()

    clauses: list[str] = []
    params: list[Any] = []

    def add_clause(template: str, value: Any) -> None:
        params.append(value)
        clauses.append(template.format(i=len(params)))

    if model:
        add_clause("model = ${i}", model)
    if team_id:
        add_clause("team_id = ${i}", team_id)
    if user_id:
        add_clause(f"{source.user_column} = ${{i}}", user_id)
    if start_date is not None:
        add_clause("start_time >= ${i}::timestamp", _date_start(start_date))
    if end_date is not None:
        add_clause("start_time <= ${i}::timestamp", _date_end(end_date))
    if not scope.is_platform_admin:
        apply_org_scope(clauses=clauses, params=params, org_ids=scope.org_ids, source=source)

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    limit_idx = len(params) + 1
    offset_idx = len(params) + 2

    logs = await db.query_raw(
        f"""
        SELECT id, request_id, call_type, model, api_base, api_key, spend, total_tokens,
               {source.prompt_tokens_column} AS prompt_tokens,
               {source.completion_tokens_column} AS completion_tokens,
               {source.cached_prompt_tokens_column} AS prompt_tokens_cached,
               {source.cached_completion_tokens_column} AS completion_tokens_cached,
               start_time, end_time, {source.user_column} AS "user", team_id, {source.end_user_column} AS end_user,
               metadata, cache_hit, cache_key, request_tags
        FROM {source.table}
        {where_sql}
        ORDER BY start_time DESC
        LIMIT ${limit_idx}
        OFFSET ${offset_idx}
        """,
        *params,
        limit,
        offset,
    )

    total_rows = await db.query_raw(
        f"SELECT COUNT(*) AS total FROM {source.table} {where_sql}",
        *params,
    )

    total = int((total_rows[0] if total_rows else {}).get("total") or 0)
    _log_admin_query_timing(
        "request_logs",
        started_at,
        model=model,
        team_id=team_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
        scoped=not scope.is_platform_admin,
    )

    return {
        "logs": [_to_json_value(dict(row)) for row in logs],
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
        },
    }


@ui_router.get("/ui/api/auth/sso-url")
async def get_sso_url() -> dict[str, str]:
    return {"url": "/auth/login"}


@ui_router.get("/ui")
async def serve_ui_root() -> Response:
    index_file = _dist_dir() / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UI bundle not found. Run: npm --prefix ui run build")
    return FileResponse(index_file)


@ui_router.get("/ui/{path:path}")
async def serve_ui(path: str) -> Response:
    if path.startswith("api/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    dist = _dist_dir()
    if not dist.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UI bundle not found. Run: npm --prefix ui run build")

    requested = (dist / path).resolve()
    try:
        requested.relative_to(dist.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path") from exc

    if requested.exists() and requested.is_file():
        return FileResponse(requested)

    return FileResponse(dist / "index.html")
