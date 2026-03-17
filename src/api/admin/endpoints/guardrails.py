from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.auth.roles import Permission
from src.audit.actions import AuditAction
from src.api.admin.endpoints.common import db_or_503, serialize_guardrail, emit_admin_mutation_audit
from src.config import GuardrailConfig
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Admin Guardrails"])


@router.get("/ui/api/guardrails", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def get_guardrails(request: Request) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        return {"guardrails": []}

    items = [serialize_guardrail(guardrail) for guardrail in app_config.deltallm_settings.guardrails]
    return {"guardrails": items}


@router.put("/ui/api/guardrails", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def update_guardrails(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="App config unavailable")

    raw_guardrails = payload.get("guardrails")
    if not isinstance(raw_guardrails, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="guardrails must be an array")

    previous = [serialize_guardrail(guardrail) for guardrail in app_config.deltallm_settings.guardrails]
    updated: list[GuardrailConfig] = []
    for raw in raw_guardrails:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("guardrail_name") or "").strip()
        deltallm_params = raw.get("deltallm_params")
        if not name or not isinstance(deltallm_params, dict):
            continue
        updated.append(GuardrailConfig(guardrail_name=name, deltallm_params=deltallm_params))

    app_config.deltallm_settings.guardrails = updated
    registry = getattr(request.app.state, "guardrail_registry", None)
    if registry is not None:
        if hasattr(registry, "_guardrails"):
            registry._guardrails.clear()
        if hasattr(registry, "_by_mode"):
            for mode in list(registry._by_mode):
                registry._by_mode[mode] = []
        registry.load_from_config(updated)

    response = await get_guardrails(request)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_GUARDRAILS_UPDATE,
        resource_type="guardrails",
        request_payload=payload,
        response_payload=response,
        before={"guardrails": previous},
        after=response,
    )
    return response


_SCOPE_TABLE_MAP = {
    "organization": "deltallm_organizationtable",
    "team": "deltallm_teamtable",
    "key": "deltallm_verificationtoken",
}
_SCOPE_ID_COL = {
    "organization": "organization_id",
    "team": "team_id",
    "key": "token",
}


def _validate_scope(scope: str) -> None:
    if scope not in _SCOPE_TABLE_MAP:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid scope: {scope}")


@router.get("/ui/api/guardrails/scope/{scope}/{entity_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def get_scoped_guardrails(request: Request, scope: str, entity_id: str) -> dict[str, Any]:
    _validate_scope(scope)
    db = db_or_503(request)
    table = _SCOPE_TABLE_MAP[scope]
    id_col = _SCOPE_ID_COL[scope]

    rows = await db.query_raw(f"SELECT metadata FROM {table} WHERE {id_col} = $1 LIMIT 1", entity_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{scope} not found")

    metadata = rows[0].get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    guardrails_config = metadata.get("guardrails_config") or {}

    registry = getattr(request.app.state, "guardrail_registry", None)
    available = registry.get_all_names() if registry else []

    return {
        "scope": scope,
        "entity_id": entity_id,
        "guardrails_config": guardrails_config,
        "available_guardrails": available,
    }


@router.put("/ui/api/guardrails/scope/{scope}/{entity_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def update_scoped_guardrails(request: Request, scope: str, entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    request_start = perf_counter()
    _validate_scope(scope)
    db = db_or_503(request)
    table = _SCOPE_TABLE_MAP[scope]
    id_col = _SCOPE_ID_COL[scope]

    rows = await db.query_raw(f"SELECT metadata FROM {table} WHERE {id_col} = $1 LIMIT 1", entity_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{scope} not found")

    metadata = rows[0].get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    guardrails_config = payload.get("guardrails_config")
    before_cfg = metadata.get("guardrails_config")
    if guardrails_config is None:
        metadata.pop("guardrails_config", None)
    else:
        if not isinstance(guardrails_config, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="guardrails_config must be an object")
        mode = guardrails_config.get("mode", "inherit")
        if mode not in ("inherit", "override"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="mode must be 'inherit' or 'override'")
        metadata["guardrails_config"] = {
            "mode": mode,
            "include": list(guardrails_config.get("include", [])),
            "exclude": list(guardrails_config.get("exclude", [])),
        }

    meta_json = json.dumps(metadata)
    await db.execute_raw(f"UPDATE {table} SET metadata = $1::jsonb WHERE {id_col} = $2", meta_json, entity_id)

    response = await get_scoped_guardrails(request, scope, entity_id)
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_GUARDRAILS_SCOPED_UPDATE,
        resource_type=scope,
        resource_id=entity_id,
        request_payload=payload,
        response_payload=response,
        before={"guardrails_config": before_cfg},
        after={"guardrails_config": response.get("guardrails_config")},
    )
    return response


@router.delete("/ui/api/guardrails/scope/{scope}/{entity_id}", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def delete_scoped_guardrails(request: Request, scope: str, entity_id: str) -> dict[str, Any]:
    request_start = perf_counter()
    _validate_scope(scope)
    db = db_or_503(request)
    table = _SCOPE_TABLE_MAP[scope]
    id_col = _SCOPE_ID_COL[scope]

    rows = await db.query_raw(f"SELECT metadata FROM {table} WHERE {id_col} = $1 LIMIT 1", entity_id)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{scope} not found")

    metadata = rows[0].get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    before_cfg = metadata.get("guardrails_config")

    metadata.pop("guardrails_config", None)
    meta_json = json.dumps(metadata)
    await db.execute_raw(f"UPDATE {table} SET metadata = $1::jsonb WHERE {id_col} = $2", meta_json, entity_id)

    response = {"status": "ok", "scope": scope, "entity_id": entity_id}
    await emit_admin_mutation_audit(
        request=request,
        request_start=request_start,
        action=AuditAction.ADMIN_GUARDRAILS_SCOPED_DELETE,
        resource_type=scope,
        resource_id=entity_id,
        response_payload=response,
        before={"guardrails_config": before_cfg},
        after={"guardrails_config": None},
    )
    return response
