from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import Task, create_task
from dataclasses import dataclass
from typing import Any

from src.bootstrap.status import BootstrapStatus
from src.cache import configure_cache_runtime
from src.config_runtime import ModelHotReloadManager
from src.providers.healthcheck import probe_provider_health
from src.router import (
    BackgroundHealthChecker,
    CooldownManager,
    FallbackConfig,
    FailoverManager,
    HealthCheckConfig,
    HealthEndpointHandler,
    PassiveHealthTracker,
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.services.callable_targets import build_callable_target_catalog
from src.services.model_deployments import bootstrap_model_deployments_from_config, load_model_registry
from src.services.route_groups import load_route_groups

logger = logging.getLogger(__name__)


@dataclass
class RoutingRuntime:
    health_checker: BackgroundHealthChecker
    health_task: Task[None] | None = None
    statuses: tuple[BootstrapStatus, ...] = ()


def _normalize_fallbacks(items: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for item in items:
        for key, value in item.items():
            merged[key] = list(value)
    return merged


async def init_routing_runtime(
    app: Any,
    *,
    cfg: Any,
    settings: Any,
    dynamic_config_manager: Any,
    redis_client: Any,
    salt_key: str,
) -> RoutingRuntime:
    if cfg.general_settings.model_deployment_bootstrap_from_config:
        did_bootstrap = await bootstrap_model_deployments_from_config(app.state.model_deployment_repository, cfg)
        if did_bootstrap:
            logger.info("bootstrapped model deployments from config into database")

    app.state.model_registry, model_registry_source = await load_model_registry(
        app.state.model_deployment_repository,
        cfg,
        settings,
        source_mode=cfg.general_settings.model_deployment_source,
    )
    logger.info("loaded model registry from %s source", model_registry_source)

    app.state.route_groups, route_group_source = await load_route_groups(
        app.state.route_group_repository,
        cfg,
        route_group_cache=app.state.route_group_runtime_cache,
    )
    logger.info("loaded route groups from %s source", route_group_source)
    app.state.callable_target_catalog = build_callable_target_catalog(
        app.state.model_registry,
        app.state.route_groups,
    )

    degraded_mode = str(
        getattr(cfg.general_settings, "redis_degraded_mode", None)
        or getattr(settings, "redis_degraded_mode", "fail_open")
    )
    state_backend = RedisStateBackend(redis_client, degraded_mode=degraded_mode)
    route_groups = list(getattr(app.state, "route_groups", []))
    deployment_registry = build_deployment_registry(app.state.model_registry, route_groups=route_groups)
    router_config = RouterConfig(
        num_retries=cfg.router_settings.num_retries,
        retry_after=cfg.router_settings.retry_after,
        timeout=cfg.router_settings.timeout,
        cooldown_time=cfg.router_settings.cooldown_time,
        allowed_fails=cfg.router_settings.allowed_fails,
        enable_pre_call_checks=cfg.router_settings.enable_pre_call_checks,
        model_group_alias=cfg.router_settings.model_group_alias,
        route_group_policies=build_route_group_policies(route_groups),
    )
    app.state.router = Router(
        strategy=RoutingStrategy(cfg.router_settings.routing_strategy),
        state_backend=state_backend,
        config=router_config,
        deployment_registry=deployment_registry,
    )
    app.state.router_state_backend = state_backend
    app.state.cooldown_manager = CooldownManager(
        state_backend=state_backend,
        cooldown_time=cfg.router_settings.cooldown_time,
        allowed_fails=cfg.router_settings.allowed_fails,
    )
    app.state.failover_manager = FailoverManager(
        config=FallbackConfig(
            num_retries=cfg.router_settings.num_retries,
            retry_after=cfg.router_settings.retry_after,
            timeout=cfg.router_settings.timeout,
            fallbacks=_normalize_fallbacks(cfg.deltallm_settings.fallbacks),
            context_window_fallbacks=_normalize_fallbacks(cfg.deltallm_settings.context_window_fallbacks),
            content_policy_fallbacks=_normalize_fallbacks(cfg.deltallm_settings.content_policy_fallbacks),
            event_history_size=cfg.general_settings.failover_event_history_size,
        ),
        deployment_registry=deployment_registry,
        state_backend=state_backend,
        cooldown_manager=app.state.cooldown_manager,
    )
    app.state.passive_health_tracker = PassiveHealthTracker(state_backend=state_backend)
    app.state.router_health_handler = HealthEndpointHandler(
        deployment_registry=deployment_registry,
        state_backend=state_backend,
    )
    configure_cache_runtime(
        app,
        app_config=cfg,
        redis_client=redis_client,
        salt_key=salt_key,
    )

    async def _deployment_health_check(deployment):  # noqa: ANN001, ANN202
        return await probe_provider_health(
            app.state.http_client,
            deployment.deltallm_params,
            default_openai_base_url=app.state.settings.openai_base_url,
        )

    health_checker = BackgroundHealthChecker(
        config=HealthCheckConfig(
            enabled=cfg.general_settings.background_health_checks,
            interval_seconds=cfg.general_settings.health_check_interval,
            timeout_seconds=30,
        ),
        deployment_registry=deployment_registry,
        state_backend=state_backend,
        checker=_deployment_health_check,
    )
    app.state.background_health_checker = health_checker
    app.state.model_hot_reload_manager = ModelHotReloadManager(
        app=app,
        dynamic_config=dynamic_config_manager,
        model_repository=app.state.model_deployment_repository,
        route_group_repository=app.state.route_group_repository,
        route_group_cache=app.state.route_group_runtime_cache,
    )

    health_task: Task[None] | None = None
    if cfg.general_settings.background_health_checks:
        health_task = create_task(health_checker.start())

    return RoutingRuntime(
        health_checker=health_checker,
        health_task=health_task,
        statuses=(
            BootstrapStatus(
                "routing",
                "ready",
                f"models={len(app.state.model_registry)} route_groups={len(app.state.route_groups)} callable_targets={len(app.state.callable_target_catalog)}",
            ),
            BootstrapStatus("background_health_checks", "ready" if health_task is not None else "disabled"),
        ),
    )


async def shutdown_routing_runtime(runtime: RoutingRuntime) -> None:
    runtime.health_checker.stop()
    if runtime.health_task is not None:
        runtime.health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.health_task
