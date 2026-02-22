from __future__ import annotations

from typing import Any
from uuid import uuid4

from src.config import AppConfig, ModelDeployment, RouterSettings
from src.config_runtime.dynamic import DynamicConfigManager
from src.router import RouterConfig, RoutingStrategy, build_deployment_registry


def _build_model_registry(cfg: AppConfig, settings: Any) -> dict[str, list[dict[str, Any]]]:
    model_registry: dict[str, list[dict[str, Any]]] = {}
    for entry in cfg.model_list:
        params = entry.litellm_params.model_dump(exclude_none=True)
        if not params.get("api_key") and settings.openai_api_key:
            params["api_key"] = settings.openai_api_key
        if not params.get("api_base"):
            params["api_base"] = settings.openai_base_url
        model_info = entry.model_info.model_dump(exclude_none=True) if entry.model_info else {}
        model_registry.setdefault(entry.model_name, []).append(
            {
                "litellm_params": params,
                "model_info": model_info,
                "deployment_id": getattr(entry, "deployment_id", None),
            }
        )
    return model_registry


def _normalize_fallbacks(items: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for item in items:
        for key, value in item.items():
            merged[key] = list(value)
    return merged


class ModelHotReloadManager:
    """Handles dynamic model lifecycle and in-place runtime reloads."""

    def __init__(self, app: Any, dynamic_config: DynamicConfigManager) -> None:
        self.app = app
        self.dynamic_config = dynamic_config
        self.dynamic_config.subscribe(self._on_config_change)

    async def add_model(self, model_config: dict[str, Any], updated_by: str = "admin_api") -> str:
        deployment = model_config.copy()
        deployment_id = str(deployment.get("deployment_id") or uuid4())
        deployment["deployment_id"] = deployment_id

        self._validate_model_config(deployment)

        current = self.dynamic_config.get_config()
        model_list = list(current.get("model_list", []))
        model_list.append(deployment)

        await self.dynamic_config.update_config({"model_list": model_list}, updated_by=updated_by)
        return deployment_id

    async def remove_model(self, deployment_id: str, updated_by: str = "admin_api") -> bool:
        current = self.dynamic_config.get_config()
        model_list = list(current.get("model_list", []))
        filtered = [item for item in model_list if item.get("deployment_id") != deployment_id]

        if len(filtered) == len(model_list):
            return False

        await self.dynamic_config.update_config({"model_list": filtered}, updated_by=updated_by)
        return True

    async def _on_config_change(self, new_config: AppConfig, changes: dict[str, list[str]]) -> None:
        if not self._has_runtime_changes(changes):
            return

        self._apply_runtime_config(new_config)

    def _apply_runtime_config(self, app_config: AppConfig) -> None:
        app = self.app
        settings = app.state.settings

        app.state.app_config = app_config
        app.state.model_registry = _build_model_registry(app_config, settings)

        new_deployments = build_deployment_registry(app.state.model_registry)
        registries = [
            getattr(app.state.router, "deployment_registry", None),
            getattr(app.state.failover_manager, "registry", None),
            getattr(app.state.router_health_handler, "registry", None),
            getattr(app.state.background_health_checker, "registry", None),
        ]
        for registry in registries:
            if isinstance(registry, dict):
                registry.clear()
                registry.update(new_deployments)

        router_settings = app_config.router_settings
        app.state.router.strategy = RoutingStrategy(router_settings.routing_strategy)
        app.state.router._strategy_impl = app.state.router._load_strategy(app.state.router.strategy)
        app.state.router.config = self._build_router_config(router_settings)

        app.state.cooldown_manager.cooldown_time = router_settings.cooldown_time
        app.state.cooldown_manager.allowed_fails = router_settings.allowed_fails

        app.state.failover_manager.config.num_retries = router_settings.num_retries
        app.state.failover_manager.config.retry_after = router_settings.retry_after
        app.state.failover_manager.config.timeout = router_settings.timeout
        app.state.failover_manager.config.fallbacks = _normalize_fallbacks(app_config.litellm_settings.fallbacks)

        if app_config.litellm_settings.guardrails:
            app.state.guardrail_registry.load_from_config(app_config.litellm_settings.guardrails)

        app.state.callback_manager.load_from_settings(
            success_callbacks=app_config.litellm_settings.success_callback,
            failure_callbacks=app_config.litellm_settings.failure_callback,
            callbacks=app_config.litellm_settings.callbacks,
            callback_settings=app_config.litellm_settings.callback_settings,
        )
        app.state.turn_off_message_logging = app_config.litellm_settings.turn_off_message_logging

    @staticmethod
    def _build_router_config(router_settings: RouterSettings) -> RouterConfig:
        data = router_settings.model_dump()
        allowed = {
            "num_retries",
            "retry_after",
            "timeout",
            "cooldown_time",
            "allowed_fails",
            "enable_pre_call_checks",
            "model_group_alias",
        }
        return RouterConfig(**{key: value for key, value in data.items() if key in allowed})

    @staticmethod
    def _has_runtime_changes(changes: dict[str, list[str]]) -> bool:
        interesting = {"model_list", "router_settings", "litellm_settings", "general_settings"}
        touched = set(changes.get("added", [])) | set(changes.get("removed", [])) | set(changes.get("modified", []))
        return bool(touched & interesting)

    @staticmethod
    def _validate_model_config(config: dict[str, Any]) -> None:
        required = {"model_name", "litellm_params"}
        missing = sorted(required - set(config.keys()))
        if missing:
            raise ValueError(f"Missing required model fields: {', '.join(missing)}")

        # Validate against schema for early feedback.
        ModelDeployment.model_validate(config)
