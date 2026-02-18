from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.api.admin.endpoints.common import model_entries, to_json_value
from src.config import GuardrailConfig
from src.middleware.admin import require_master_key
from src.router import RoutingStrategy

router = APIRouter(tags=["Admin Config"])


@router.get("/ui/api/routing", dependencies=[Depends(require_master_key)])
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


@router.put("/ui/api/routing", dependencies=[Depends(require_master_key)])
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

    router_state = getattr(request.app.state, "router", None)
    if router_state is not None:
        router_state.strategy = RoutingStrategy(app_config.router_settings.routing_strategy)
        router_state.config.timeout = app_config.router_settings.timeout
        router_state.config.num_retries = app_config.router_settings.num_retries
        router_state.config.cooldown_time = app_config.router_settings.cooldown_time
        router_state.config.retry_after = app_config.router_settings.retry_after
        router_state._strategy_impl = router_state._load_strategy(router_state.strategy)

    return await get_routing(request)


@router.get("/ui/api/settings", dependencies=[Depends(require_master_key)])
async def get_settings(request: Request) -> dict[str, Any]:
    app_config = getattr(request.app.state, "app_config", None)
    if app_config is None:
        return {}

    return {
        "general_settings": to_json_value(app_config.general_settings.model_dump()),
        "router_settings": to_json_value(app_config.router_settings.model_dump()),
        "litellm_settings": to_json_value(app_config.litellm_settings.model_dump()),
        "model_count": len(model_entries(request.app)),
    }


@router.put("/ui/api/settings", dependencies=[Depends(require_master_key)])
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
        router_state = getattr(request.app.state, "router", None)
        if router_state is not None:
            router_state.strategy = RoutingStrategy(app_config.router_settings.routing_strategy)
            router_state._strategy_impl = router_state._load_strategy(router_state.strategy)

    settings = getattr(request.app.state, "settings", None)
    if settings is not None and "master_key" in general_updates:
        setattr(settings, "master_key", general_updates["master_key"])

    return await get_settings(request)
