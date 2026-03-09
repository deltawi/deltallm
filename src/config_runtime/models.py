from __future__ import annotations

from typing import Any
from uuid import uuid4

from src.cache import configure_cache_runtime
from src.config import AppConfig, RouterSettings, resolve_salt_key
from src.config_runtime.dynamic import DynamicConfigManager
from src.db.repositories import ModelDeploymentRecord, ModelDeploymentRepository
from src.db.route_groups import RouteGroupRepository
from src.providers.resolution import validate_provider_mode_compatibility
from src.router import RouterConfig, RoutingStrategy, build_deployment_registry, build_route_group_policies
from src.services.model_deployments import load_model_registry
from src.services.route_groups import RouteGroupRuntimeCache, load_route_groups


def _normalize_fallbacks(items: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for item in items:
        for key, value in item.items():
            merged[key] = list(value)
    return merged


class ModelHotReloadManager:
    """Handles dynamic model lifecycle and in-place runtime reloads."""

    def __init__(
        self,
        app: Any,
        dynamic_config: DynamicConfigManager,
        model_repository: ModelDeploymentRepository | None = None,
        route_group_repository: RouteGroupRepository | None = None,
        route_group_cache: RouteGroupRuntimeCache | None = None,
    ) -> None:
        self.app = app
        self.dynamic_config = dynamic_config
        self.model_repository = model_repository
        self.route_group_repository = route_group_repository
        self.route_group_cache = route_group_cache
        self.dynamic_config.subscribe(self._on_config_change)

    async def add_model(self, model_config: dict[str, Any], updated_by: str = "admin_api") -> str:
        deployment = model_config.copy()
        deployment_id = str(deployment.get("deployment_id") or uuid4())
        deployment["deployment_id"] = deployment_id

        self._validate_model_config(deployment)
        if self.model_repository is None:
            current = self.dynamic_config.get_config()
            model_list = list(current.get("model_list", []))
            model_list.append(deployment)
            await self.dynamic_config.update_config({"model_list": model_list}, updated_by=updated_by)
        else:
            await self.model_repository.create(
                ModelDeploymentRecord(
                    deployment_id=deployment_id,
                    model_name=str(deployment["model_name"]),
                    deltallm_params=dict(deployment["deltallm_params"]),
                    model_info=dict(deployment.get("model_info", {})),
                )
            )
            await self._invalidate_route_group_cache()
            await self._reload_runtime()
        return deployment_id

    async def update_model(self, deployment_id: str, model_config: dict[str, Any], updated_by: str = "admin_api") -> bool:
        deployment = model_config.copy()
        deployment["deployment_id"] = deployment_id
        self._validate_model_config(deployment)

        if self.model_repository is None:
            current = self.dynamic_config.get_config()
            model_list = list(current.get("model_list", []))
            updated = False
            for idx, item in enumerate(model_list):
                if item.get("deployment_id") == deployment_id:
                    model_list[idx] = deployment
                    updated = True
                    break
            if not updated:
                return False
            await self.dynamic_config.update_config({"model_list": model_list}, updated_by=updated_by)
            return True

        updated_record = await self.model_repository.update(
            deployment_id,
            model_name=str(deployment["model_name"]),
            deltallm_params=dict(deployment["deltallm_params"]),
            model_info=dict(deployment.get("model_info", {})),
        )
        if updated_record is None:
            return False
        await self._invalidate_route_group_cache()
        await self._reload_runtime()
        return True

    async def remove_model(self, deployment_id: str, updated_by: str = "admin_api") -> bool:
        if self.model_repository is None:
            current = self.dynamic_config.get_config()
            model_list = list(current.get("model_list", []))
            filtered = [item for item in model_list if item.get("deployment_id") != deployment_id]
            if len(filtered) == len(model_list):
                return False
            await self.dynamic_config.update_config({"model_list": filtered}, updated_by=updated_by)
            return True

        removed = await self.model_repository.delete(deployment_id)
        if not removed:
            return False
        await self._invalidate_route_group_cache()
        await self._reload_runtime()
        return True

    async def _on_config_change(self, new_config: AppConfig, changes: dict[str, list[str]]) -> None:
        if not self._has_runtime_changes(changes):
            return

        await self._apply_runtime_config(new_config)

    async def _apply_runtime_config(self, app_config: AppConfig) -> None:
        app = self.app
        settings = app.state.settings

        app.state.app_config = app_config
        salt_key = resolve_salt_key(app_config, settings)
        model_registry, _ = await load_model_registry(
            self.model_repository,
            app_config,
            settings,
            source_mode=app_config.general_settings.model_deployment_source,
        )
        app.state.model_registry = model_registry

        route_groups, _ = await load_route_groups(
            self.route_group_repository,
            app_config,
            route_group_cache=self.route_group_cache,
        )
        app.state.route_groups = route_groups
        new_deployments = build_deployment_registry(app.state.model_registry, route_groups=route_groups)
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
        app.state.router.config = self._build_router_config(router_settings, route_groups)

        app.state.cooldown_manager.cooldown_time = router_settings.cooldown_time
        app.state.cooldown_manager.allowed_fails = router_settings.allowed_fails

        app.state.failover_manager.config.num_retries = router_settings.num_retries
        app.state.failover_manager.config.retry_after = router_settings.retry_after
        app.state.failover_manager.config.timeout = router_settings.timeout
        app.state.failover_manager.config.fallbacks = _normalize_fallbacks(app_config.deltallm_settings.fallbacks)

        if app_config.deltallm_settings.guardrails:
            app.state.guardrail_registry.load_from_config(app_config.deltallm_settings.guardrails)

        app.state.callback_manager.load_from_settings(
            success_callbacks=app_config.deltallm_settings.success_callback,
            failure_callbacks=app_config.deltallm_settings.failure_callback,
            callbacks=app_config.deltallm_settings.callbacks,
            callback_settings=app_config.deltallm_settings.callback_settings,
        )
        app.state.turn_off_message_logging = app_config.deltallm_settings.turn_off_message_logging
        configure_cache_runtime(
            app,
            app_config=app_config,
            redis_client=getattr(app.state, "redis", None),
            salt_key=salt_key,
        )

    async def _reload_runtime(self) -> None:
        app_config = self.dynamic_config.get_app_config()
        await self._apply_runtime_config(app_config)
        await self.dynamic_config.publish_model_updated()

    async def reload_runtime(self) -> None:
        await self._reload_runtime()

    async def _invalidate_route_group_cache(self) -> None:
        if self.route_group_cache is None:
            return
        await self.route_group_cache.invalidate()

    @staticmethod
    def _build_router_config(router_settings: RouterSettings, route_groups: list[dict[str, Any]] | None = None) -> RouterConfig:
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
        effective_route_groups = route_groups if route_groups is not None else data.get("route_groups", [])
        return RouterConfig(
            **{key: value for key, value in data.items() if key in allowed},
            route_group_policies=build_route_group_policies(effective_route_groups),
        )

    @staticmethod
    def _has_runtime_changes(changes: dict[str, list[str]]) -> bool:
        interesting = {"model_list", "router_settings", "deltallm_settings", "litellm_settings", "general_settings"}
        touched = set(changes.get("added", [])) | set(changes.get("removed", [])) | set(changes.get("modified", []))
        return bool(touched & interesting)

    @staticmethod
    def _validate_model_config(config: dict[str, Any]) -> None:
        has_params = "deltallm_params" in config or "litellm_params" in config
        if "model_name" not in config or not has_params:
            raise ValueError("Missing required model fields: model_name, deltallm_params")

        validate_provider_mode_compatibility(config)
