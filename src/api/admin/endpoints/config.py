from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from src.auth.roles import Permission
from src.api.admin.endpoints.common import model_entries, to_json_value, get_auth_scope
from src.middleware.admin import require_admin_permission

router = APIRouter(tags=["Admin Config"])


@router.get("/ui/api/routing", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
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
            params = dict(entry.get("deltallm_params", {}))
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
            "usage-based-routing",
            "tag-based-routing",
            "priority-based-routing",
            "weighted",
            "rate-limit-aware",
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


@router.put("/ui/api/routing", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def update_routing(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable")

    config_update: dict[str, Any] = {}
    router_updates: dict[str, Any] = {}
    general_updates: dict[str, Any] = {}

    strategy = payload.get("strategy")
    if isinstance(strategy, str) and strategy:
        router_updates["routing_strategy"] = strategy

    config_fields = payload.get("config")
    if isinstance(config_fields, dict):
        if "timeout" in config_fields:
            router_updates["timeout"] = float(config_fields["timeout"])
        if "retries" in config_fields:
            router_updates["num_retries"] = int(config_fields["retries"])
        if "cooldown" in config_fields:
            router_updates["cooldown_time"] = int(config_fields["cooldown"])
        if "retry_after" in config_fields:
            router_updates["retry_after"] = float(config_fields["retry_after"])
        if "health_check_enabled" in config_fields:
            general_updates["background_health_checks"] = bool(config_fields["health_check_enabled"])
        if "health_check_interval" in config_fields:
            general_updates["health_check_interval"] = int(config_fields["health_check_interval"])

    if router_updates:
        config_update["router_settings"] = router_updates
    if general_updates:
        config_update["general_settings"] = general_updates

    if config_update:
        await dynamic_config.update_config(config_update, updated_by="admin_api")

    return await get_routing(request)


@router.get("/ui/api/settings", dependencies=[Depends(require_admin_permission(Permission.CONFIG_READ))])
async def get_settings(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        return {}

    scope = get_auth_scope(request, authorization, x_master_key)
    general = to_json_value(app_config.general_settings.model_dump())
    if not scope.is_platform_admin:
        general.pop("master_key", None)

    return {
        "general_settings": general,
        "router_settings": to_json_value(app_config.router_settings.model_dump()),
        "deltallm_settings": to_json_value(app_config.deltallm_settings.model_dump()),
        "model_count": len(model_entries(request.app)),
    }


@router.put("/ui/api/settings", dependencies=[Depends(require_admin_permission(Permission.PLATFORM_ADMIN))])
async def update_settings(
    request: Request,
    payload: dict[str, Any],
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_master_key: str | None = Header(default=None, alias="X-Master-Key"),
) -> dict[str, Any]:
    dynamic_config = getattr(request.app.state, "dynamic_config_manager", None)
    if dynamic_config is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Config manager unavailable")

    config_update: dict[str, Any] = {}

    general_updates = payload.get("general_settings") if isinstance(payload.get("general_settings"), dict) else {}
    router_updates = payload.get("router_settings") if isinstance(payload.get("router_settings"), dict) else {}
    deltallm_updates = payload.get("deltallm_settings") if isinstance(payload.get("deltallm_settings"), dict) else {}

    if general_updates:
        config_update["general_settings"] = general_updates
    if router_updates:
        config_update["router_settings"] = router_updates
    if deltallm_updates:
        if "guardrails" in deltallm_updates and isinstance(deltallm_updates["guardrails"], list):
            deltallm_updates["guardrails"] = [
                {"guardrail_name": str(item.get("guardrail_name")), "deltallm_params": item.get("deltallm_params", {})}
                for item in deltallm_updates["guardrails"]
                if isinstance(item, dict) and isinstance(item.get("deltallm_params"), dict)
            ]
        config_update["deltallm_settings"] = deltallm_updates

    if config_update:
        await dynamic_config.update_config(config_update, updated_by="admin_api")

    settings = getattr(request.app.state, "settings", None)
    if settings is not None and "master_key" in general_updates:
        setattr(settings, "master_key", general_updates["master_key"])

    if "log_level" in general_updates:
        level = str(general_updates["log_level"]).upper()
        if level in ("DEBUG", "INFO", "WARNING", "ERROR"):
            logging.getLogger().setLevel(getattr(logging, level))

    return await get_settings(request, authorization=authorization, x_master_key=x_master_key)
