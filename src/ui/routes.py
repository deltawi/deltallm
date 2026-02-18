from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from src.config import GuardrailConfig
from src.middleware.admin import require_master_key
from src.router import RoutingStrategy, build_deployment_registry

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


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} must be an integer") from exc


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


def _guardrail_type_from_class_path(class_path: str) -> str:
    lowered = class_path.lower()
    if "presidio" in lowered:
        return "PII Detection (Presidio)"
    if "lakera" in lowered:
        return "Prompt Injection (Lakera)"
    return "Custom Guardrail"


def _serialize_guardrail(raw: Any) -> dict[str, Any]:
    item = raw.model_dump(mode="python") if hasattr(raw, "model_dump") else dict(raw)
    litellm_params = dict(item.get("litellm_params", {}))
    class_path = str(litellm_params.get("guardrail") or "")
    threshold = litellm_params.get("threshold")
    if threshold is None:
        threshold = litellm_params.get("score_threshold")
    if threshold is None:
        threshold = litellm_params.get("confidence_threshold")

    return {
        "guardrail_name": item.get("guardrail_name"),
        "type": _guardrail_type_from_class_path(class_path),
        "mode": litellm_params.get("mode", "pre_call"),
        "enabled": bool(litellm_params.get("enabled", True)),
        "default_action": litellm_params.get("default_action", "block"),
        "threshold": float(threshold) if threshold is not None else 0.5,
        "litellm_params": _to_json_value(litellm_params),
    }


@ui_router.get("/ui/api/models", dependencies=[Depends(require_master_key)])
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


@ui_router.get("/ui/api/keys", dependencies=[Depends(require_master_key)])
async def list_keys(request: Request) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at
        FROM litellm_verificationtoken
        ORDER BY created_at DESC
        """
    )
    return [_to_json_value(dict(row)) for row in rows]


@ui_router.post("/ui/api/keys", dependencies=[Depends(require_master_key)])
async def create_key(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    raw_key = f"sk-{secrets.token_urlsafe(24)}"
    key_service = request.app.state.key_service
    token_hash = key_service.hash_key(raw_key)

    key_name = payload.get("key_name")
    user_id = payload.get("user_id")
    team_id = payload.get("team_id")
    models = payload.get("models") if isinstance(payload.get("models"), list) else []
    max_budget = payload.get("max_budget")
    rpm_limit = payload.get("rpm_limit")
    tpm_limit = payload.get("tpm_limit")
    expires = payload.get("expires")
    if expires is not None and not isinstance(expires, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expires must be a string datetime")

    await db.execute_raw(
        """
        INSERT INTO litellm_verificationtoken (id, token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at)
        VALUES (gen_random_uuid(), $1, $2, $3, $4, $5::text[], 0, $6, $7, $8, $9::timestamp, NOW(), NOW())
        """,
        token_hash,
        key_name,
        user_id,
        team_id,
        models,
        max_budget,
        rpm_limit,
        tpm_limit,
        expires,
    )

    return {
        "token": token_hash,
        "raw_key": raw_key,
        "key_name": key_name,
        "user_id": user_id,
        "team_id": team_id,
        "models": models,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "expires": expires,
    }


@ui_router.put("/ui/api/keys/{token_hash}", dependencies=[Depends(require_master_key)])
async def update_key(request: Request, token_hash: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at
        FROM litellm_verificationtoken
        WHERE token = $1
        LIMIT 1
        """,
        token_hash,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    existing = dict(rows[0])
    models = payload.get("models", existing.get("models"))
    if not isinstance(models, list):
        models = existing.get("models") or []

    expires = payload.get("expires", existing.get("expires"))
    if expires is not None and not isinstance(expires, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="expires must be a string datetime")

    key_name = payload.get("key_name", existing.get("key_name"))
    user_id = payload.get("user_id", existing.get("user_id"))
    team_id = payload.get("team_id", existing.get("team_id"))
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    rpm_limit = payload.get("rpm_limit", existing.get("rpm_limit"))
    tpm_limit = payload.get("tpm_limit", existing.get("tpm_limit"))

    await db.execute_raw(
        """
        UPDATE litellm_verificationtoken
        SET key_name = $1,
            user_id = $2,
            team_id = $3,
            models = $4::text[],
            max_budget = $5,
            rpm_limit = $6,
            tpm_limit = $7,
            expires = $8::timestamp,
            updated_at = NOW()
        WHERE token = $9
        """,
        key_name,
        user_id,
        team_id,
        models,
        max_budget,
        rpm_limit,
        tpm_limit,
        expires,
        token_hash,
    )

    updated_rows = await db.query_raw(
        """
        SELECT token, key_name, user_id, team_id, models, spend, max_budget, rpm_limit, tpm_limit, expires, created_at, updated_at
        FROM litellm_verificationtoken
        WHERE token = $1
        LIMIT 1
        """,
        token_hash,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")
    return _to_json_value(dict(updated_rows[0]))


@ui_router.post("/ui/api/keys/{token_hash}/regenerate", dependencies=[Depends(require_master_key)])
async def regenerate_key(request: Request, token_hash: str) -> dict[str, Any]:
    db = _db_or_503(request)
    rows = await db.query_raw("SELECT token FROM litellm_verificationtoken WHERE token = $1 LIMIT 1", token_hash)
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key not found")

    raw_key = f"sk-{secrets.token_urlsafe(24)}"
    new_hash = request.app.state.key_service.hash_key(raw_key)
    await db.execute_raw(
        "UPDATE litellm_verificationtoken SET token = $1, updated_at = NOW() WHERE token = $2",
        new_hash,
        token_hash,
    )
    return {"token": new_hash, "raw_key": raw_key}


@ui_router.post("/ui/api/keys/{token_hash}/revoke", dependencies=[Depends(require_master_key)])
async def revoke_key(request: Request, token_hash: str) -> dict[str, bool]:
    db = _db_or_503(request)
    deleted = await db.execute_raw("DELETE FROM litellm_verificationtoken WHERE token = $1", token_hash)
    return {"revoked": int(deleted or 0) > 0}


@ui_router.delete("/ui/api/keys/{token_hash}", dependencies=[Depends(require_master_key)])
async def delete_key(request: Request, token_hash: str) -> dict[str, bool]:
    db = _db_or_503(request)
    deleted = await db.execute_raw("DELETE FROM litellm_verificationtoken WHERE token = $1", token_hash)
    return {"deleted": int(deleted or 0) > 0}


@ui_router.get("/ui/api/teams", dependencies=[Depends(require_master_key)])
async def list_teams(request: Request) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT t.team_id, t.team_alias, t.organization_id, t.max_budget, t.spend, t.models, t.rpm_limit, t.tpm_limit, t.blocked,
               t.created_at, t.updated_at,
               (SELECT COUNT(*) FROM litellm_usertable u WHERE u.team_id = t.team_id) AS member_count
        FROM litellm_teamtable t
        ORDER BY t.created_at DESC
        """
    )
    return [_to_json_value(dict(row)) for row in rows]


@ui_router.get("/ui/api/organizations", dependencies=[Depends(require_master_key)])
async def list_organizations(request: Request) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, created_at, updated_at
        FROM litellm_organizationtable
        ORDER BY created_at DESC
        """
    )
    return [_to_json_value(dict(row)) for row in rows]


@ui_router.post("/ui/api/organizations", dependencies=[Depends(require_master_key)])
async def create_organization(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    organization_id = str(payload.get("organization_id") or f"org-{secrets.token_hex(6)}")
    organization_name = payload.get("organization_name")
    max_budget = payload.get("max_budget")
    rpm_limit = _optional_int(payload.get("rpm_limit"), "rpm_limit")
    tpm_limit = _optional_int(payload.get("tpm_limit"), "tpm_limit")

    await db.execute_raw(
        """
        INSERT INTO litellm_organizationtable (
            id,
            organization_id,
            organization_name,
            max_budget,
            spend,
            rpm_limit,
            tpm_limit,
            created_at,
            updated_at
        )
        VALUES (gen_random_uuid(), $1, $2, $3, 0, $4, $5, NOW(), NOW())
        ON CONFLICT (organization_id)
        DO UPDATE SET
            organization_name = EXCLUDED.organization_name,
            max_budget = EXCLUDED.max_budget,
            rpm_limit = EXCLUDED.rpm_limit,
            tpm_limit = EXCLUDED.tpm_limit,
            updated_at = NOW()
        """,
        organization_id,
        organization_name,
        max_budget,
        rpm_limit,
        tpm_limit,
    )
    return {
        "organization_id": organization_id,
        "organization_name": organization_name,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
    }


@ui_router.put("/ui/api/organizations/{organization_id}", dependencies=[Depends(require_master_key)])
async def update_organization(request: Request, organization_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, created_at, updated_at
        FROM litellm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")

    existing = dict(rows[0])
    organization_name = payload.get("organization_name", existing.get("organization_name"))
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    rpm_limit = _optional_int(payload.get("rpm_limit", existing.get("rpm_limit")), "rpm_limit")
    tpm_limit = _optional_int(payload.get("tpm_limit", existing.get("tpm_limit")), "tpm_limit")

    await db.execute_raw(
        """
        UPDATE litellm_organizationtable
        SET organization_name = $1,
            max_budget = $2,
            rpm_limit = $3,
            tpm_limit = $4,
            updated_at = NOW()
        WHERE organization_id = $5
        """,
        organization_name,
        max_budget,
        rpm_limit,
        tpm_limit,
        organization_id,
    )
    updated_rows = await db.query_raw(
        """
        SELECT organization_id, organization_name, max_budget, spend, rpm_limit, tpm_limit, created_at, updated_at
        FROM litellm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return _to_json_value(dict(updated_rows[0]))


@ui_router.post("/ui/api/teams", dependencies=[Depends(require_master_key)])
async def create_team(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    team_id = str(payload.get("team_id") or f"team-{secrets.token_hex(6)}")
    team_alias = payload.get("team_alias")
    organization_id = payload.get("organization_id")
    max_budget = payload.get("max_budget")
    rpm_limit = _optional_int(payload.get("rpm_limit"), "rpm_limit")
    tpm_limit = _optional_int(payload.get("tpm_limit"), "tpm_limit")
    models = payload.get("models") if isinstance(payload.get("models"), list) else []

    await db.execute_raw(
        """
        INSERT INTO litellm_teamtable (team_id, team_alias, organization_id, max_budget, spend, rpm_limit, tpm_limit, models, blocked, created_at, updated_at)
        VALUES ($1, $2, $3, $4, 0, $5, $6, $7::text[], false, NOW(), NOW())
        """,
        team_id,
        team_alias,
        organization_id,
        max_budget,
        rpm_limit,
        tpm_limit,
        models,
    )
    return {
        "team_id": team_id,
        "team_alias": team_alias,
        "organization_id": organization_id,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "models": models,
        "blocked": False,
    }


@ui_router.put("/ui/api/teams/{team_id}", dependencies=[Depends(require_master_key)])
async def update_team(request: Request, team_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT team_id, team_alias, organization_id, max_budget, spend, models, rpm_limit, tpm_limit, blocked, created_at, updated_at
        FROM litellm_teamtable
        WHERE team_id = $1
        LIMIT 1
        """,
        team_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")

    existing = dict(rows[0])
    team_alias = payload.get("team_alias", existing.get("team_alias"))
    organization_id = payload.get("organization_id", existing.get("organization_id"))
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    rpm_limit = _optional_int(payload.get("rpm_limit", existing.get("rpm_limit")), "rpm_limit")
    tpm_limit = _optional_int(payload.get("tpm_limit", existing.get("tpm_limit")), "tpm_limit")
    models = payload.get("models", existing.get("models"))
    if not isinstance(models, list):
        models = existing.get("models") or []

    await db.execute_raw(
        """
        UPDATE litellm_teamtable
        SET team_alias = $1,
            organization_id = $2,
            max_budget = $3,
            rpm_limit = $4,
            tpm_limit = $5,
            models = $6::text[],
            updated_at = NOW()
        WHERE team_id = $7
        """,
        team_alias,
        organization_id,
        max_budget,
        rpm_limit,
        tpm_limit,
        models,
        team_id,
    )
    updated_rows = await db.query_raw(
        """
        SELECT team_id, team_alias, organization_id, max_budget, spend, models, rpm_limit, tpm_limit, blocked, created_at, updated_at
        FROM litellm_teamtable
        WHERE team_id = $1
        LIMIT 1
        """,
        team_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Team not found")
    return _to_json_value(dict(updated_rows[0]))


@ui_router.get("/ui/api/teams/{team_id}/members", dependencies=[Depends(require_master_key)])
async def list_team_members(request: Request, team_id: str) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT user_id, user_email, user_role, spend, max_budget, team_id, created_at, updated_at
        FROM litellm_usertable
        WHERE team_id = $1
        ORDER BY created_at DESC
        """,
        team_id,
    )
    return [_to_json_value(dict(row)) for row in rows]


@ui_router.post("/ui/api/teams/{team_id}/members", dependencies=[Depends(require_master_key)])
async def add_team_member(request: Request, team_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    user_id = str(payload.get("user_id") or "").strip()
    user_email = payload.get("user_email")
    user_role = payload.get("user_role") or "internal_user"
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id is required")

    await db.execute_raw(
        """
        INSERT INTO litellm_usertable (user_id, user_email, user_role, spend, models, team_id, created_at, updated_at)
        VALUES ($1, $2, $3, 0, '{}'::text[], $4, NOW(), NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET user_email = EXCLUDED.user_email, user_role = EXCLUDED.user_role, team_id = EXCLUDED.team_id, updated_at = NOW()
        """,
        user_id,
        user_email,
        user_role,
        team_id,
    )
    return {
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "team_id": team_id,
    }


@ui_router.delete("/ui/api/teams/{team_id}/members/{user_id}", dependencies=[Depends(require_master_key)])
async def remove_team_member(request: Request, team_id: str, user_id: str) -> dict[str, bool]:
    db = _db_or_503(request)
    updated = await db.execute_raw(
        "UPDATE litellm_usertable SET team_id = NULL, updated_at = NOW() WHERE team_id = $1 AND user_id = $2",
        team_id,
        user_id,
    )
    return {"removed": int(updated or 0) > 0}


@ui_router.get("/ui/api/users", dependencies=[Depends(require_master_key)])
async def list_users(request: Request, team_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    db = _db_or_503(request)
    if team_id:
        rows = await db.query_raw(
            """
            SELECT user_id, user_email, user_role, team_id, spend, max_budget, models, tpm_limit, rpm_limit,
                   COALESCE((metadata->>'blocked')::boolean, false) AS blocked, created_at, updated_at
            FROM litellm_usertable
            WHERE team_id = $1
            ORDER BY created_at DESC
            """,
            team_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT user_id, user_email, user_role, team_id, spend, max_budget, models, tpm_limit, rpm_limit,
                   COALESCE((metadata->>'blocked')::boolean, false) AS blocked, created_at, updated_at
            FROM litellm_usertable
            ORDER BY created_at DESC
            """
        )
    return [_to_json_value(dict(row)) for row in rows]


@ui_router.post("/ui/api/users", dependencies=[Depends(require_master_key)])
async def create_user(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="user_id is required")

    user_email = payload.get("user_email")
    user_role = payload.get("user_role") or "user"
    team_id = payload.get("team_id")
    max_budget = payload.get("max_budget")
    rpm_limit = _optional_int(payload.get("rpm_limit"), "rpm_limit")
    tpm_limit = _optional_int(payload.get("tpm_limit"), "tpm_limit")
    models = payload.get("models") if isinstance(payload.get("models"), list) else []

    await db.execute_raw(
        """
        INSERT INTO litellm_usertable (
            user_id,
            user_email,
            user_role,
            spend,
            models,
            team_id,
            max_budget,
            rpm_limit,
            tpm_limit,
            metadata,
            created_at,
            updated_at
        )
        VALUES ($1, $2, $3, 0, $4::text[], $5, $6, $7, $8, '{}'::jsonb, NOW(), NOW())
        ON CONFLICT (user_id)
        DO UPDATE SET
            user_email = EXCLUDED.user_email,
            user_role = EXCLUDED.user_role,
            team_id = EXCLUDED.team_id,
            max_budget = EXCLUDED.max_budget,
            models = EXCLUDED.models,
            rpm_limit = EXCLUDED.rpm_limit,
            tpm_limit = EXCLUDED.tpm_limit,
            updated_at = NOW()
        """,
        user_id,
        user_email,
        user_role,
        models,
        team_id,
        max_budget,
        rpm_limit,
        tpm_limit,
    )

    return {
        "user_id": user_id,
        "user_email": user_email,
        "user_role": user_role,
        "team_id": team_id,
        "max_budget": max_budget,
        "rpm_limit": rpm_limit,
        "tpm_limit": tpm_limit,
        "models": models,
        "blocked": False,
    }


@ui_router.put("/ui/api/users/{user_id}", dependencies=[Depends(require_master_key)])
async def update_user(request: Request, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    rows = await db.query_raw(
        """
        SELECT user_id, user_email, user_role, team_id, spend, max_budget, models, tpm_limit, rpm_limit,
               COALESCE((metadata->>'blocked')::boolean, false) AS blocked, created_at, updated_at
        FROM litellm_usertable
        WHERE user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing = dict(rows[0])
    user_email = payload.get("user_email", existing.get("user_email"))
    user_role = payload.get("user_role", existing.get("user_role"))
    team_id = payload.get("team_id", existing.get("team_id"))
    max_budget = payload.get("max_budget", existing.get("max_budget"))
    models = payload.get("models", existing.get("models"))
    if not isinstance(models, list):
        models = existing.get("models") or []
    rpm_limit = _optional_int(payload.get("rpm_limit", existing.get("rpm_limit")), "rpm_limit")
    tpm_limit = _optional_int(payload.get("tpm_limit", existing.get("tpm_limit")), "tpm_limit")

    await db.execute_raw(
        """
        UPDATE litellm_usertable
        SET user_email = $1,
            user_role = $2,
            team_id = $3,
            max_budget = $4,
            models = $5::text[],
            rpm_limit = $6,
            tpm_limit = $7,
            updated_at = NOW()
        WHERE user_id = $8
        """,
        user_email,
        user_role,
        team_id,
        max_budget,
        models,
        rpm_limit,
        tpm_limit,
        user_id,
    )
    updated_rows = await db.query_raw(
        """
        SELECT user_id, user_email, user_role, team_id, spend, max_budget, models, tpm_limit, rpm_limit,
               COALESCE((metadata->>'blocked')::boolean, false) AS blocked, created_at, updated_at
        FROM litellm_usertable
        WHERE user_id = $1
        LIMIT 1
        """,
        user_id,
    )
    if not updated_rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _to_json_value(dict(updated_rows[0]))


@ui_router.post("/ui/api/users/{user_id}/block", dependencies=[Depends(require_master_key)])
async def block_user(request: Request, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    db = _db_or_503(request)
    blocked = bool(payload.get("blocked", True))

    updated = await db.execute_raw(
        """
        UPDATE litellm_usertable
        SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{blocked}', to_jsonb($1::boolean)),
            updated_at = NOW()
        WHERE user_id = $2
        """,
        blocked,
        user_id,
    )
    if int(updated or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return {"user_id": user_id, "blocked": blocked}


def _date_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.min, tzinfo=UTC)


def _date_end(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, time.max, tzinfo=UTC)


@ui_router.get("/ui/api/spend/summary", dependencies=[Depends(require_master_key)])
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


@ui_router.get("/ui/api/spend/report", dependencies=[Depends(require_master_key)])
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


@ui_router.get("/ui/api/guardrails", dependencies=[Depends(require_master_key)])
async def get_guardrails(request: Request) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        return {"guardrails": []}

    items = [_serialize_guardrail(guardrail) for guardrail in app_config.litellm_settings.guardrails]
    return {"guardrails": items}


@ui_router.put("/ui/api/guardrails", dependencies=[Depends(require_master_key)])
async def update_guardrails(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="App config unavailable")

    raw_guardrails = payload.get("guardrails")
    if not isinstance(raw_guardrails, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="guardrails must be an array")

    updated: list[GuardrailConfig] = []
    for raw in raw_guardrails:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("guardrail_name") or "").strip()
        litellm_params = raw.get("litellm_params")
        if not name or not isinstance(litellm_params, dict):
            continue
        updated.append(GuardrailConfig(guardrail_name=name, litellm_params=litellm_params))

    app_config.litellm_settings.guardrails = updated
    registry = getattr(request.app.state, "guardrail_registry", None)
    if registry is not None:
        if hasattr(registry, "_guardrails"):
            registry._guardrails.clear()
        if hasattr(registry, "_by_mode"):
            for mode in list(registry._by_mode):
                registry._by_mode[mode] = []
        registry.load_from_config(updated)

    return await get_guardrails(request)


@ui_router.get("/ui/api/routing", dependencies=[Depends(require_master_key)])
async def get_routing(request: Request) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    router_settings = getattr(app_config, "router_settings", None)
    general_settings = getattr(app_config, "general_settings", None)

    health_handler = getattr(request.app.state, "router_health_handler", None)
    health_payload = None
    if health_handler is not None:
        health_payload = await health_handler.get_health_status()

    deployments: list[dict[str, Any]] = []
    health_by_id: dict[str, dict[str, Any]] = {}
    if isinstance(health_payload, dict):
        health_by_id = {str(item.get("deployment_id")): item for item in health_payload.get("deployments", [])}

    for model_name, entries in getattr(request.app.state, "model_registry", {}).items():
        for index, entry in enumerate(entries):
            deployment_id = str(entry.get("deployment_id") or f"{model_name}-{index}")
            params = dict(entry.get("litellm_params", {}))
            health = health_by_id.get(deployment_id, {})
            deployments.append(
                {
                    "deployment_id": deployment_id,
                    "model": model_name,
                    "provider": str(params.get("model", "")).split("/")[0] or "unknown",
                    "status": "healthy" if bool(health.get("healthy", True)) else "degraded",
                    "latency_ms": health.get("avg_latency_ms"),
                    "last_check": health.get("last_success_at") or health.get("last_error_at"),
                }
            )

    fallback_map = {}
    failover_manager = getattr(request.app.state, "failover_manager", None)
    config = getattr(failover_manager, "config", None)
    if config is not None and isinstance(getattr(config, "fallbacks", None), dict):
        fallback_map = config.fallbacks

    failover_chains = [{"model_group": model, "chain": [model, *fallbacks]} for model, fallbacks in fallback_map.items()]
    for model_name in getattr(request.app.state, "model_registry", {}):
        if model_name not in fallback_map:
            failover_chains.append({"model_group": model_name, "chain": [model_name]})

    return {
        "strategy": str(getattr(router_settings, "routing_strategy", "simple-shuffle")),
        "available_strategies": [
            "simple-shuffle",
            "least-busy",
            "latency-based-routing",
            "cost-based-routing",
            "priority-based-routing",
        ],
        "config": {
            "timeout": getattr(router_settings, "timeout", 600),
            "retries": getattr(router_settings, "num_retries", 0),
            "cooldown": getattr(router_settings, "cooldown_time", 60),
            "retry_after": getattr(router_settings, "retry_after", 0),
            "health_check_enabled": getattr(general_settings, "background_health_checks", False),
            "health_check_interval": getattr(general_settings, "health_check_interval", 300),
        },
        "deployments": deployments,
        "failover_chains": failover_chains,
    }


@ui_router.put("/ui/api/routing", dependencies=[Depends(require_master_key)])
async def update_routing(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="App config unavailable")

    strategy = payload.get("strategy")
    if isinstance(strategy, str) and strategy:
        app_config.router_settings.routing_strategy = strategy

    config_updates = payload.get("config")
    if isinstance(config_updates, dict):
        if "timeout" in config_updates:
            app_config.router_settings.timeout = float(config_updates["timeout"])
        if "retries" in config_updates:
            app_config.router_settings.num_retries = int(config_updates["retries"])
        if "cooldown" in config_updates:
            app_config.router_settings.cooldown_time = int(config_updates["cooldown"])
        if "retry_after" in config_updates:
            app_config.router_settings.retry_after = float(config_updates["retry_after"])
        if "health_check_enabled" in config_updates:
            app_config.general_settings.background_health_checks = bool(config_updates["health_check_enabled"])
        if "health_check_interval" in config_updates:
            app_config.general_settings.health_check_interval = int(config_updates["health_check_interval"])

    router = getattr(request.app.state, "router", None)
    if router is not None:
        router.strategy = RoutingStrategy(app_config.router_settings.routing_strategy)
        router.config.timeout = app_config.router_settings.timeout
        router.config.num_retries = app_config.router_settings.num_retries
        router.config.cooldown_time = app_config.router_settings.cooldown_time
        router.config.retry_after = app_config.router_settings.retry_after
        router._strategy_impl = router._load_strategy(router.strategy)

    return await get_routing(request)


@ui_router.get("/ui/api/settings", dependencies=[Depends(require_master_key)])
async def get_settings(request: Request) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        return {}

    return {
        "general_settings": _to_json_value(app_config.general_settings.model_dump()),
        "router_settings": _to_json_value(app_config.router_settings.model_dump()),
        "litellm_settings": _to_json_value(app_config.litellm_settings.model_dump()),
        "model_count": len(_model_entries(request.app)),
    }


@ui_router.put("/ui/api/settings", dependencies=[Depends(require_master_key)])
async def update_settings(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="App config unavailable")

    general_updates = payload.get("general_settings") if isinstance(payload.get("general_settings"), dict) else {}
    router_updates = payload.get("router_settings") if isinstance(payload.get("router_settings"), dict) else {}
    litellm_updates = payload.get("litellm_settings") if isinstance(payload.get("litellm_settings"), dict) else {}

    for key, value in general_updates.items():
        if hasattr(app_config.general_settings, key):
            setattr(app_config.general_settings, key, value)
    for key, value in router_updates.items():
        if hasattr(app_config.router_settings, key):
            setattr(app_config.router_settings, key, value)
    for key, value in litellm_updates.items():
        if key == "guardrails" and isinstance(value, list):
            app_config.litellm_settings.guardrails = [
                GuardrailConfig(guardrail_name=str(item.get("guardrail_name")), litellm_params=item.get("litellm_params", {}))
                for item in value
                if isinstance(item, dict) and isinstance(item.get("litellm_params"), dict)
            ]
            continue
        if hasattr(app_config.litellm_settings, key):
            setattr(app_config.litellm_settings, key, value)

    if "routing_strategy" in router_updates:
        router = getattr(request.app.state, "router", None)
        if router is not None:
            router.strategy = RoutingStrategy(app_config.router_settings.routing_strategy)
            router._strategy_impl = router._load_strategy(router.strategy)

    settings = getattr(request.app.state, "settings", None)
    if settings is not None and "master_key" in general_updates:
        setattr(settings, "master_key", general_updates["master_key"])

    return await get_settings(request)


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
