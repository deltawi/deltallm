from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from src.middleware.admin import require_master_key, require_authenticated
from src.router import build_deployment_registry

ui_router = APIRouter(tags=["UI"])


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


def _model_entries(app: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    registry: dict[str, list[dict[str, Any]]] = getattr(app.state, "model_registry", {})
    for model_name, deployments in registry.items():
        for index, deployment in enumerate(deployments):
            deployment_id = str(deployment.get("deployment_id") or f"{model_name}-{index}")
            params = dict(deployment.get("litellm_params", {}))
            model_info = dict(deployment.get("model_info", {}))
            entries.append(
                {
                    "deployment_id": deployment_id,
                    "model_name": model_name,
                    "provider": str(params.get("model", "")).split("/")[0] or "unknown",
                    "mode": model_info.get("mode", "chat"),
                    "litellm_params": params,
                    "model_info": model_info,
                }
            )
    return entries


def _rebuild_runtime_registry(app: Any) -> None:
    model_registry = getattr(app.state, "model_registry", {})
    rebuilt = build_deployment_registry(model_registry)

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


@ui_router.get("/ui/api/models", dependencies=[Depends(require_authenticated)])
async def list_models(request: Request) -> list[dict[str, Any]]:
    health_backend = getattr(request.app.state, "router_state_backend", None)
    entries = _model_entries(request.app)

    for entry in entries:
        healthy = True
        if health_backend is not None:
            health = await health_backend.get_health(entry["deployment_id"])
            healthy = str(health.get("healthy", "true")) != "false"
        entry["healthy"] = healthy

    return entries


@ui_router.post("/ui/api/models", dependencies=[Depends(require_master_key)])
async def create_model(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    model_name = str(payload.get("model_name") or "").strip()
    litellm_params = payload.get("litellm_params")
    if not model_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="model_name is required")
    if not isinstance(litellm_params, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="litellm_params must be an object")

    deployment_id = str(payload.get("deployment_id") or f"{model_name}-{secrets.token_hex(4)}")
    model_info = payload.get("model_info") if isinstance(payload.get("model_info"), dict) else {}

    entry = {
        "deployment_id": deployment_id,
        "litellm_params": litellm_params,
        "model_info": model_info,
    }
    request.app.state.model_registry.setdefault(model_name, []).append(entry)
    _rebuild_runtime_registry(request.app)

    return {
        "deployment_id": deployment_id,
        "model_name": model_name,
        "litellm_params": litellm_params,
        "model_info": model_info,
    }


@ui_router.put("/ui/api/models/{deployment_id}", dependencies=[Depends(require_master_key)])
async def update_model(request: Request, deployment_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    registry: dict[str, list[dict[str, Any]]] = request.app.state.model_registry

    for model_name, deployments in list(registry.items()):
        for idx, deployment in enumerate(deployments):
            candidate_id = str(deployment.get("deployment_id") or f"{model_name}-{idx}")
            if candidate_id != deployment_id:
                continue

            new_model_name = str(payload.get("model_name") or model_name)
            litellm_params = payload.get("litellm_params") if isinstance(payload.get("litellm_params"), dict) else deployment.get("litellm_params", {})
            model_info = payload.get("model_info") if isinstance(payload.get("model_info"), dict) else deployment.get("model_info", {})

            updated = {
                "deployment_id": deployment_id,
                "litellm_params": litellm_params,
                "model_info": model_info,
            }

            del deployments[idx]
            if not deployments:
                registry.pop(model_name, None)
            registry.setdefault(new_model_name, []).append(updated)
            _rebuild_runtime_registry(request.app)

            return {
                "deployment_id": deployment_id,
                "model_name": new_model_name,
                "litellm_params": litellm_params,
                "model_info": model_info,
            }

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")


@ui_router.delete("/ui/api/models/{deployment_id}", dependencies=[Depends(require_master_key)])
async def delete_model(request: Request, deployment_id: str) -> dict[str, bool]:
    registry: dict[str, list[dict[str, Any]]] = request.app.state.model_registry

    for model_name, deployments in list(registry.items()):
        kept: list[dict[str, Any]] = []
        removed = False
        for idx, deployment in enumerate(deployments):
            candidate_id = str(deployment.get("deployment_id") or f"{model_name}-{idx}")
            if candidate_id == deployment_id:
                removed = True
                continue
            kept.append(deployment)

        if removed:
            if kept:
                registry[model_name] = kept
            else:
                registry.pop(model_name, None)
            _rebuild_runtime_registry(request.app)
            return {"deleted": True}

    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")


def _date_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


@ui_router.get("/ui/api/spend/summary", dependencies=[Depends(require_authenticated)])
async def spend_summary(
    request: Request,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict[str, Any]:
    db = _db_or_503(request)
    clauses: list[str] = []
    params: list[Any] = []

    if start_date is not None:
        params.append(_date_start(start_date))
        clauses.append(f"start_time >= ${len(params)}")
    if end_date is not None:
        params.append(_date_end(end_date))
        clauses.append(f"start_time <= ${len(params)}")

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    rows = await db.query_raw(
        f"""
        SELECT
            COALESCE(SUM(spend), 0) AS total_spend,
            COALESCE(SUM(total_tokens), 0) AS total_tokens,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COUNT(*) AS total_requests
        FROM litellm_spendlogs
        {where_sql}
        """,
        *params,
    )
    return _to_json_value(dict(rows[0] if rows else {}))


@ui_router.get("/ui/api/spend/report", dependencies=[Depends(require_authenticated)])
async def spend_report(
    request: Request,
    group_by: str = Query(default="day", pattern="^(model|provider|day|user|team)$"),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> dict[str, Any]:
    db = _db_or_503(request)
    group_column = {
        "model": "model",
        "provider": "api_base",
        "day": "DATE(start_time)",
        "user": "COALESCE(\"user\", 'anonymous')",
        "team": "COALESCE(team_id, 'none')",
    }[group_by]

    clauses: list[str] = []
    params: list[Any] = []
    if start_date is not None:
        params.append(_date_start(start_date))
        clauses.append(f"start_time >= ${len(params)}")
    if end_date is not None:
        params.append(_date_end(end_date))
        clauses.append(f"start_time <= ${len(params)}")

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    rows = await db.query_raw(
        f"""
        SELECT
            {group_column} AS group_key,
            COALESCE(SUM(spend), 0) AS total_spend,
            COUNT(*) AS request_count,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM litellm_spendlogs
        {where_sql}
        GROUP BY {group_column}
        ORDER BY group_key ASC
        """,
        *params,
    )

    return {
        "group_by": group_by,
        "breakdown": [_to_json_value(dict(row)) for row in rows],
    }


@ui_router.get("/ui/api/logs", dependencies=[Depends(require_master_key)])
async def request_logs(
    request: Request,
    model: str | None = Query(default=None),
    team_id: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    db = _db_or_503(request)

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
        add_clause('"user" = ${i}', user_id)
    if start_date is not None:
        add_clause("start_time >= ${i}", _date_start(start_date))
    if end_date is not None:
        add_clause("start_time <= ${i}", _date_end(end_date))

    where_sql = ""
    if clauses:
        where_sql = " WHERE " + " AND ".join(clauses)

    limit_idx = len(params) + 1
    offset_idx = len(params) + 2

    logs = await db.query_raw(
        f"""
        SELECT id, request_id, call_type, model, api_base, api_key, spend, total_tokens,
               prompt_tokens, completion_tokens, start_time, end_time, "user", team_id, cache_hit
        FROM litellm_spendlogs
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
        f"SELECT COUNT(*) AS total FROM litellm_spendlogs {where_sql}",
        *params,
    )

    total = int((total_rows[0] if total_rows else {}).get("total") or 0)

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
