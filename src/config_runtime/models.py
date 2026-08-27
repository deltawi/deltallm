from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Generic, TypeVar
from uuid import uuid4

from src.cache import configure_cache_runtime
from src.config import AppConfig, RouterSettings, resolve_salt_key
from src.config_runtime.dynamic import DynamicConfigManager
from src.router.runtime_generation import (
    RoutingRuntimeAppliedState,
    RoutingRuntimeGeneration,
    RoutingRuntimeGenerationStore,
    with_authorization_snapshot,
)
from src.db.named_credentials import NamedCredentialRepository
from src.db.repositories import (
    ModelDeploymentRecord,
    ModelDeploymentRepository,
)
from src.db.route_groups import RouteGroupRepository
from src.metrics import increment_router_health_update_failure
from src.providers.resolution import validate_provider_mode_compatibility
from src.router import (
    CooldownManager,
    DeploymentStateBackend,
    FailoverManager,
    RouterConfig,
    Router,
    RoutingStrategy,
    build_deployment_registry,
    build_route_group_policies,
)
from src.router.registry import DeploymentRegistryStore
from src.router.route_group_validation import resolve_route_group_modes_for_registry
from src.services.callable_targets import build_callable_target_catalog
from src.services.model_deployments import load_model_registry
from src.services.routing_authorization import RoutingAuthorizationReconciler
from src.services.route_groups import (
    RouteGroupRuntimeCache,
    StaleRouteGroupSnapshotError,
    load_route_group_snapshot_result,
)


_THEME_GENERAL_FIELDS = frozenset({"instance_name", "ui_branding"})
logger = logging.getLogger(__name__)
_ROUTING_SNAPSHOT_BUILD_ATTEMPTS = 3
_MODEL_REFRESH_WARNING = "Mutation committed, but local routing runtime refresh failed"
_MutationValue = TypeVar("_MutationValue")


@dataclass(frozen=True, slots=True)
class ModelMutationResult(Generic[_MutationValue]):
    value: _MutationValue
    warnings: tuple[str, ...] = ()


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
        named_credential_repository: NamedCredentialRepository | None = None,
        route_group_repository: RouteGroupRepository | None = None,
        route_group_cache: RouteGroupRuntimeCache | None = None,
        router_state_backend: DeploymentStateBackend | None = None,
    ) -> None:
        self.app = app
        self.dynamic_config = dynamic_config
        self.model_repository = model_repository
        self.named_credential_repository = named_credential_repository
        self.route_group_repository = route_group_repository
        self.route_group_cache = route_group_cache
        self.router_state_backend = router_state_backend
        self.routing_authorization_reconciler: RoutingAuthorizationReconciler | None = None
        self._route_reload_lock = asyncio.Lock()
        self._requested_route_reload = 0
        self._applied_route_reload = 0
        self._applied_route_revision = int(
            getattr(app.state, "route_group_runtime_revision", 0) or 0
        )
        self._applied_routing_state = RoutingRuntimeAppliedState(
            revision=self._applied_route_revision,
            source=str(
                getattr(app.state, "route_group_runtime_source", "config_only") or "config_only"
            ),
            requires_reconciliation=bool(
                getattr(
                    app.state,
                    "route_group_runtime_requires_reconciliation",
                    False,
                )
            ),
        )
        if not isinstance(
            getattr(app.state, "routing_runtime_generation_store", None),
            RoutingRuntimeGenerationStore,
        ):
            app.state.routing_runtime_generation_store = RoutingRuntimeGenerationStore()
        self.dynamic_config.subscribe_finalizer(self._on_config_change)

    def set_routing_authorization_reconciler(
        self,
        reconciler: RoutingAuthorizationReconciler,
    ) -> None:
        """Complete bootstrap wiring before the application begins serving requests."""

        self.routing_authorization_reconciler = reconciler

    async def add_model(
        self,
        model_config: dict[str, Any],
        updated_by: str = "admin_api",
    ) -> ModelMutationResult[str]:
        deployment = model_config.copy()
        deployment_id = str(deployment.get("deployment_id") or uuid4())
        deployment["deployment_id"] = deployment_id
        deployment["routing_state_incarnation"] = str(uuid4())

        self._validate_model_config(deployment)
        if self.model_repository is None:
            current = self.dynamic_config.get_config()
            model_list = list(current.get("model_list", []))
            model_list.append(deployment)
            await self.dynamic_config.update_config(
                {"model_list": model_list}, updated_by=updated_by
            )
            warnings: tuple[str, ...] = ()
        else:
            await self.model_repository.create(
                ModelDeploymentRecord(
                    deployment_id=deployment_id,
                    model_name=str(deployment["model_name"]),
                    named_credential_id=(
                        str(deployment.get("named_credential_id")).strip() or None
                        if deployment.get("named_credential_id") is not None
                        else None
                    ),
                    deltallm_params=dict(deployment["deltallm_params"]),
                    model_info=dict(deployment.get("model_info", {})),
                )
            )
            warnings = await self._refresh_committed_model_runtime()
        return ModelMutationResult(value=deployment_id, warnings=warnings)

    async def update_model(
        self, deployment_id: str, model_config: dict[str, Any], updated_by: str = "admin_api"
    ) -> ModelMutationResult[bool]:
        deployment = model_config.copy()
        deployment["deployment_id"] = deployment_id
        self._validate_model_config(deployment)

        if self.model_repository is None:
            current = self.dynamic_config.get_config()
            model_list = list(current.get("model_list", []))
            updated = False
            for idx, item in enumerate(model_list):
                if item.get("deployment_id") == deployment_id:
                    deployment["routing_state_incarnation"] = str(
                        item.get("routing_state_incarnation") or deployment_id
                    )
                    model_list[idx] = deployment
                    updated = True
                    break
            if not updated:
                return ModelMutationResult(value=False)
            await self.dynamic_config.update_config(
                {"model_list": model_list}, updated_by=updated_by
            )
            return ModelMutationResult(value=True)

        updated_record = await self.model_repository.update(
            deployment_id,
            **self._repository_update_kwargs(self.model_repository, deployment),
        )
        if updated_record is None:
            return ModelMutationResult(value=False)
        warnings = await self._refresh_committed_model_runtime()
        return ModelMutationResult(value=True, warnings=warnings)

    async def remove_model(
        self,
        deployment_id: str,
        updated_by: str = "admin_api",
    ) -> ModelMutationResult[bool]:
        if self.model_repository is None:
            current = self.dynamic_config.get_config()
            model_list = list(current.get("model_list", []))
            filtered = [item for item in model_list if item.get("deployment_id") != deployment_id]
            if len(filtered) == len(model_list):
                return ModelMutationResult(value=False)
            await self.dynamic_config.update_config({"model_list": filtered}, updated_by=updated_by)
            return ModelMutationResult(value=True)

        removed = await self.model_repository.delete(deployment_id)
        if not removed:
            return ModelMutationResult(value=False)
        warnings = await self._refresh_committed_model_runtime()
        return ModelMutationResult(value=True, warnings=warnings)

    async def _refresh_committed_model_runtime(self) -> tuple[str, ...]:
        try:
            await self.reload_runtime()
        except Exception:
            self._mark_reconciliation_required()
            logger.warning("model runtime refresh failed after commit", exc_info=True)
            return (_MODEL_REFRESH_WARNING,)
        return ()

    async def _on_config_change(self, new_config: AppConfig, changes: dict[str, list[str]]) -> None:
        if not self._has_runtime_changes(changes):
            return

        current_config = getattr(self.app.state, "app_config", None)
        if self._is_theme_only_change(current_config, new_config, changes):
            self._apply_theme_identity_config(new_config)
            return

        await self._apply_runtime_config(new_config)

    def _apply_theme_identity_config(self, app_config: AppConfig) -> None:
        self.app.state.app_config = app_config
        identity_service = getattr(self.app.state, "platform_identity_service", None)
        if identity_service is not None and hasattr(identity_service, "totp_issuer"):
            identity_service.totp_issuer = app_config.general_settings.instance_name

    async def _apply_runtime_config(self, app_config: AppConfig) -> None:
        app = self.app
        settings = app.state.settings
        generation_store = app.state.routing_runtime_generation_store

        async with self._route_reload_lock:
            publication_base = generation_store.snapshot()
            publication_base_id = (
                publication_base.generation_id if publication_base is not None else None
            )
            await self._invalidate_route_group_cache()
            generation = await self._load_complete_routing_generation(
                app_config=app_config,
                settings=settings,
            )
        salt_key = generation.salt_key

        if app_config.deltallm_settings.guardrails:
            app.state.guardrail_registry.load_from_config(app_config.deltallm_settings.guardrails)

        app.state.callback_manager.load_from_settings(
            success_callbacks=app_config.deltallm_settings.success_callback,
            failure_callbacks=app_config.deltallm_settings.failure_callback,
            callbacks=app_config.deltallm_settings.callbacks,
            callback_settings=app_config.deltallm_settings.callback_settings,
        )
        app.state.turn_off_message_logging = app_config.deltallm_settings.turn_off_message_logging
        general = app_config.general_settings
        prompt_service = getattr(app.state, "prompt_registry_service", None)
        if prompt_service is not None:
            prompt_service.configure_cache(
                l1_ttl_seconds=general.prompt_cache_l1_ttl_seconds,
                l2_ttl_seconds=general.prompt_cache_l2_ttl_seconds,
                negative_cache_enabled=general.prompt_negative_cache_enabled,
                negative_l1_ttl_seconds=general.prompt_negative_l1_ttl_seconds,
                negative_l2_ttl_seconds=general.prompt_negative_l2_ttl_seconds,
                l1_max_entries=general.prompt_cache_l1_max_entries,
            )
        budget_service = getattr(app.state, "budget_service", None)
        if budget_service is not None:
            budget_service.query_mode = general.budget_enforcement_query_mode
            budget_service.shadow_sample_rate = general.budget_enforcement_shadow_sample_rate
            budget_service.query_timeout_seconds = general.budget_enforcement_query_timeout_seconds
        spend_service = getattr(app.state, "spend_tracking_service", None)
        if spend_service is not None and callable(getattr(spend_service, "reconfigure", None)):
            await spend_service.reconfigure(
                replace(
                    spend_service.config,
                    # Ingestion mode owns a dedicated startup-time database pool.
                    # A rolling restart is required to switch modes safely.
                    enabled=spend_service.config.enabled,
                    batch_size=general.spend_ingestion_batch_size,
                    flush_interval_seconds=general.spend_ingestion_flush_interval_ms / 1000.0,
                    lease_seconds=general.spend_ingestion_lease_seconds,
                    max_attempts=general.spend_ingestion_max_attempts,
                    worker_enabled=general.spend_ingestion_worker_enabled,
                    max_pending_events=general.spend_ingestion_max_pending_events,
                    overload_policy=general.spend_ingestion_overload_policy,
                    fallback_max_concurrency=general.spend_ingestion_fallback_max_concurrency,
                    fallback_max_waiters=general.spend_ingestion_fallback_max_waiters,
                    fallback_queue_timeout_seconds=(
                        general.spend_ingestion_fallback_queue_timeout_ms / 1000.0
                    ),
                    fallback_execution_timeout_seconds=(
                        general.spend_ingestion_fallback_execution_timeout_seconds
                    ),
                    completed_retention_hours=general.spend_ingestion_completed_retention_hours,
                    failed_retention_days=general.spend_ingestion_failed_retention_days,
                    cleanup_interval_seconds=general.spend_ingestion_cleanup_interval_seconds,
                    cleanup_batch_size=general.spend_ingestion_cleanup_batch_size,
                    cleanup_max_batches_per_run=(
                        general.spend_ingestion_cleanup_max_batches_per_run
                    ),
                    cleanup_time_budget_seconds=(
                        general.spend_ingestion_cleanup_time_budget_seconds
                    ),
                    worker_startup_timeout_seconds=(
                        general.telemetry_worker_startup_timeout_seconds
                    ),
                    shutdown_drain_timeout_seconds=general.telemetry_shutdown_drain_timeout_seconds,
                )
            )
        audit_service = getattr(app.state, "audit_service", None)
        if audit_service is not None and callable(getattr(audit_service, "reconfigure", None)):
            await audit_service.reconfigure(
                replace(
                    audit_service.ingestion_config,
                    # Ingestion mode owns a dedicated startup-time database pool.
                    # A rolling restart is required to switch modes safely.
                    enabled=audit_service.ingestion_config.enabled,
                    worker_enabled=general.audit_ingestion_worker_enabled,
                    batch_size=general.audit_ingestion_batch_size,
                    flush_interval_seconds=general.audit_ingestion_flush_interval_ms / 1000.0,
                    lease_seconds=general.audit_ingestion_lease_seconds,
                    max_attempts=general.audit_ingestion_max_attempts,
                    max_pending_events=general.audit_ingestion_max_pending_events,
                    required_reserve=general.audit_ingestion_required_reserve,
                    completed_retention_hours=(general.audit_ingestion_completed_retention_hours),
                    failed_retention_days=general.audit_ingestion_failed_retention_days,
                    cleanup_interval_seconds=(general.audit_ingestion_cleanup_interval_seconds),
                    cleanup_batch_size=general.audit_ingestion_cleanup_batch_size,
                    cleanup_max_batches_per_run=(
                        general.audit_ingestion_cleanup_max_batches_per_run
                    ),
                    cleanup_time_budget_seconds=(
                        general.audit_ingestion_cleanup_time_budget_seconds
                    ),
                    worker_startup_timeout_seconds=(
                        general.telemetry_worker_startup_timeout_seconds
                    ),
                    shutdown_drain_timeout_seconds=(
                        general.telemetry_shutdown_drain_timeout_seconds
                    ),
                )
            )
        configure_cache_runtime(
            app,
            app_config=app_config,
            redis_client=getattr(app.state, "redis", None),
            salt_key=salt_key,
        )

        async with self._route_reload_lock:
            generation, current_deployments = await self._publish_stable_config_generation(
                app_config=app_config,
                settings=settings,
                candidate=generation,
                candidate_base_id=publication_base_id,
            )
        new_deployments = generation.deployment_registry.snapshot()
        await self._cleanup_replaced_deployment_health(
            current=current_deployments,
            replacement=new_deployments,
        )

    async def _publish_stable_config_generation(
        self,
        *,
        app_config: AppConfig,
        settings: Any,
        candidate: RoutingRuntimeGeneration,
        candidate_base_id: str | None,
    ) -> tuple[RoutingRuntimeGeneration, Mapping[str, Sequence[Any]]]:
        """Publish only a candidate built from the still-current generation identity."""

        generation_store = self.app.state.routing_runtime_generation_store
        expected_generation_id = candidate_base_id
        for attempt in range(_ROUTING_SNAPSHOT_BUILD_ATTEMPTS + 1):
            current_generation = generation_store.snapshot()
            current_generation_id = (
                current_generation.generation_id if current_generation is not None else None
            )
            if (
                current_generation_id == expected_generation_id
                and candidate.revision >= self._applied_route_revision
            ):
                current_registry = (
                    current_generation.deployment_registry
                    if current_generation is not None
                    else getattr(self.app.state.router, "deployment_registry", None)
                )
                current_deployments = (
                    current_registry.snapshot()
                    if isinstance(current_registry, DeploymentRegistryStore)
                    else current_registry or {}
                )
                # No await is allowed between this identity check and publication.
                self._publish_routing_generation(candidate)
                return candidate, current_deployments
            if attempt == _ROUTING_SNAPSHOT_BUILD_ATTEMPTS:
                break
            expected_generation_id = current_generation_id
            await self._invalidate_route_group_cache()
            candidate = await self._load_complete_routing_generation(
                app_config=app_config,
                settings=settings,
            )

        self._mark_reconciliation_required()
        raise StaleRouteGroupSnapshotError(
            "routing generation changed while config publication was being prepared"
        )

    async def _build_routing_generation(
        self,
        *,
        app_config: AppConfig,
        model_registry: dict[str, list[dict[str, Any]]],
    ) -> RoutingRuntimeGeneration:
        route_group_load = await load_route_group_snapshot_result(
            self.route_group_repository,
            app_config,
            route_group_cache=self.route_group_cache,
            allow_config_fallback=self.route_group_repository is None,
        )
        snapshot = route_group_load.snapshot
        mode_resolution = resolve_route_group_modes_for_registry(
            snapshot.groups,
            model_registry,
        )
        route_groups = mode_resolution.groups
        if mode_resolution.inferred_group_keys:
            logger.warning(
                "inferred omitted route-group workload modes; declare mode explicitly groups=%s",
                ",".join(mode_resolution.inferred_group_keys),
            )
        callable_target_catalog = build_callable_target_catalog(model_registry, route_groups)
        deployments = build_deployment_registry(model_registry, route_groups=route_groups)
        router_settings = app_config.router_settings
        current_failover_manager = self.app.state.failover_manager
        current_failover = current_failover_manager.config
        failover_config = replace(
            current_failover,
            num_retries=router_settings.num_retries,
            retry_after=router_settings.retry_after,
            timeout=router_settings.timeout,
            fallbacks=_normalize_fallbacks(app_config.deltallm_settings.fallbacks),
            context_window_fallbacks=_normalize_fallbacks(
                app_config.deltallm_settings.context_window_fallbacks
            ),
            content_policy_fallbacks=_normalize_fallbacks(
                app_config.deltallm_settings.content_policy_fallbacks
            ),
            event_history_size=app_config.general_settings.failover_event_history_size,
        )
        deployment_registry = DeploymentRegistryStore(deployments)
        router_config = self._build_router_config(router_settings, route_groups)
        state_backend = self.router_state_backend or self.app.state.router.state
        cooldown_manager = CooldownManager(
            state_backend=state_backend,
            cooldown_time=router_settings.cooldown_time,
            allowed_fails=router_settings.allowed_fails,
        )
        router = Router(
            strategy=RoutingStrategy(router_settings.routing_strategy),
            state_backend=state_backend,
            config=router_config,
            deployment_registry=deployment_registry,
        )
        failover_manager = FailoverManager(
            config=failover_config,
            candidate_planner=router,
            state_backend=state_backend,
            cooldown_manager=cooldown_manager,
            event_journal=current_failover_manager.event_journal,
        )
        return RoutingRuntimeGeneration.create(
            revision=snapshot.revision,
            app_config=app_config,
            model_registry=model_registry,
            route_groups=route_groups,
            callable_target_catalog=callable_target_catalog,
            deployment_registry=deployment_registry,
            strategy=router.strategy,
            router_config=router_config,
            failover_config=failover_config,
            salt_key=resolve_salt_key(app_config, self.app.state.settings),
            router=router,
            failover_manager=failover_manager,
            cooldown_manager=cooldown_manager,
            source=route_group_load.source,
            requires_reconciliation=route_group_load.requires_reconciliation,
        )

    async def _load_complete_routing_generation(
        self,
        *,
        app_config: AppConfig,
        settings: Any,
    ) -> RoutingRuntimeGeneration:
        """Load every routing input behind one durable revision fence."""

        for _ in range(_ROUTING_SNAPSHOT_BUILD_ATTEMPTS):
            revision_before = (
                await self.route_group_repository.get_runtime_revision()
                if self.route_group_repository is not None
                else 0
            )
            model_registry, _ = await self._load_model_registry_compat(
                app_config=app_config,
                settings=settings,
            )
            generation = await self._build_routing_generation(
                app_config=app_config,
                model_registry=model_registry,
            )
            generation = await self._prepare_routing_authorization(generation)
            revision_after = (
                await self.route_group_repository.get_runtime_revision()
                if self.route_group_repository is not None
                else 0
            )
            if self.route_group_repository is None or (
                revision_before == generation.revision == revision_after
            ):
                return generation
            await self._invalidate_route_group_cache()
        raise StaleRouteGroupSnapshotError(
            "routing inputs changed while a complete runtime generation was loading"
        )

    def _publish_routing_generation(self, generation: RoutingRuntimeGeneration) -> None:
        """Publish one validated generation without yielding to request tasks."""

        app = self.app
        # Data-plane callers consume the store replaced at the end. Prepare the
        # compatibility aliases first so a failed assignment cannot expose the candidate.
        grant_service = getattr(app.state, "callable_target_grant_service", None)
        if grant_service is not None:
            grant_service.replace_snapshot(generation.authorization_snapshot)
        app.state.router = generation.router
        app.state.router_health_handler.registry = generation.deployment_registry
        app.state.background_health_checker.registry = generation.deployment_registry
        app.state.background_health_checker.health = generation.cooldown_manager
        app.state.cooldown_manager = generation.cooldown_manager
        app.state.failover_manager = generation.failover_manager
        app.state.model_registry = {
            key: list(entries) for key, entries in generation.model_registry.items()
        }
        app.state.route_groups = list(generation.route_groups)
        app.state.route_group_runtime_revision = generation.revision
        app.state.callable_target_catalog = generation.callable_target_catalog
        self._apply_theme_identity_config(generation.app_config)
        self._applied_route_revision = max(
            self._applied_route_revision,
            generation.revision,
        )
        self._applied_routing_state = RoutingRuntimeAppliedState(
            revision=self._applied_route_revision,
            source=generation.source,
            requires_reconciliation=generation.requires_reconciliation,
        )
        app.state.routing_runtime_generation_store.replace(generation)

    async def _cleanup_replaced_deployment_health(
        self,
        *,
        current: Mapping[str, Sequence[Any]],
        replacement: Mapping[str, Sequence[Any]],
    ) -> None:
        if self.router_state_backend is None:
            return
        current_by_id = self._deployments_by_id(current)
        replacement_by_id = self._deployments_by_id(replacement)
        retired_refs = [
            deployment.health_ref
            for deployment_id, deployment in current_by_id.items()
            if deployment_id not in replacement_by_id
            or deployment.health_ref != replacement_by_id[deployment_id].health_ref
        ]
        if not retired_refs:
            return
        try:
            invalidated = await self.router_state_backend.invalidate_health_state(retired_refs)
        except Exception:
            increment_router_health_update_failure()
            logger.warning(
                "router health invalidation failed during model runtime replacement count=%s",
                len(retired_refs),
                exc_info=True,
            )
            return
        if not invalidated:
            increment_router_health_update_failure()
            logger.warning(
                "router health invalidation degraded during model runtime replacement count=%s",
                len(retired_refs),
            )

    @staticmethod
    def _deployments_by_id(registry: Mapping[str, Sequence[Any]]) -> dict[str, Any]:
        return {
            str(deployment.deployment_id): deployment
            for deployments in registry.values()
            for deployment in deployments
        }

    async def _reload_runtime(self) -> None:
        app_config = self.dynamic_config.get_app_config()
        await self._apply_runtime_config(app_config)

    async def reload_runtime(self) -> None:
        try:
            await self._reload_runtime()
        except Exception:
            self._mark_reconciliation_required()
            raise
        await self.dynamic_config.publish_model_updated()

    def get_applied_route_revision(self) -> int:
        return self._applied_route_revision

    def get_applied_routing_state(self) -> RoutingRuntimeAppliedState:
        return self._applied_routing_state

    def _mark_reconciliation_required(self) -> None:
        current = self._applied_routing_state
        self._applied_routing_state = RoutingRuntimeAppliedState(
            revision=current.revision,
            source=current.source,
            requires_reconciliation=True,
        )

    async def reload_route_groups(self) -> None:
        """Coalesce reloads and discard any generation built by an older request."""

        self._requested_route_reload += 1
        async with self._route_reload_lock:
            while self._applied_route_reload < self._requested_route_reload:
                requested = self._requested_route_reload
                await self._invalidate_route_group_cache()
                app_config = self.dynamic_config.get_app_config()
                try:
                    generation = await self._load_complete_routing_generation(
                        app_config=app_config,
                        settings=self.app.state.settings,
                    )
                except Exception:
                    self._applied_route_reload = requested
                    self._mark_reconciliation_required()
                    raise
                if requested != self._requested_route_reload:
                    continue
                if generation.revision < self._applied_route_revision:
                    self._applied_route_reload = requested
                    continue
                current_registry = getattr(self.app.state.router, "deployment_registry", None)
                current_deployments = (
                    current_registry.snapshot()
                    if isinstance(current_registry, DeploymentRegistryStore)
                    else current_registry or {}
                )
                self._publish_routing_generation(generation)
                self._applied_route_reload = requested
                await self._cleanup_replaced_deployment_health(
                    current=current_deployments,
                    replacement=generation.deployment_registry.snapshot(),
                )

    async def _prepare_routing_authorization(
        self,
        generation: RoutingRuntimeGeneration,
    ) -> RoutingRuntimeGeneration:
        reconciler = self.routing_authorization_reconciler
        if reconciler is None:
            store = getattr(self.app.state, "routing_runtime_generation_store", None)
            current = store.snapshot() if isinstance(store, RoutingRuntimeGenerationStore) else None
            snapshot = (
                current.authorization_snapshot
                if current is not None
                else generation.authorization_snapshot
            )
            return with_authorization_snapshot(generation, snapshot)
        try:
            _changed, snapshot = await reconciler.prepare(generation.callable_target_catalog)
        except Exception:
            self._mark_reconciliation_required()
            raise
        return with_authorization_snapshot(generation, snapshot)

    async def _invalidate_route_group_cache(self) -> None:
        if self.route_group_cache is None:
            return
        await self.route_group_cache.invalidate()

    async def _load_model_registry_compat(
        self,
        *,
        app_config: AppConfig,
        settings: Any,
    ) -> tuple[dict[str, list[dict[str, Any]]], str]:
        kwargs = {
            "source_mode": app_config.general_settings.model_deployment_source,
            "named_credential_repository": self.named_credential_repository,
            "secret_resolver": getattr(self.dynamic_config, "secret_resolver", None),
            "allow_db_error_fallback": False,
        }
        signature = inspect.signature(load_model_registry)
        supported_kwargs = {
            key: value for key, value in kwargs.items() if key in signature.parameters
        }
        return await load_model_registry(
            self.model_repository,
            app_config,
            settings,
            **supported_kwargs,
        )

    @staticmethod
    def _repository_update_kwargs(repository: Any, deployment: dict[str, Any]) -> dict[str, Any]:
        kwargs = {
            "model_name": str(deployment["model_name"]),
            "named_credential_id": str(deployment.get("named_credential_id")).strip() or None
            if deployment.get("named_credential_id") is not None
            else None,
            "deltallm_params": dict(deployment["deltallm_params"]),
            "model_info": dict(deployment.get("model_info", {})),
        }
        signature = inspect.signature(repository.update)
        return {key: value for key, value in kwargs.items() if key in signature.parameters}

    @staticmethod
    def _build_router_config(
        router_settings: RouterSettings, route_groups: list[dict[str, Any]] | None = None
    ) -> RouterConfig:
        data = router_settings.model_dump()
        allowed = {
            "enable_pre_call_checks",
            "model_group_alias",
        }
        effective_route_groups = (
            route_groups if route_groups is not None else data.get("route_groups", [])
        )
        return RouterConfig(
            **{key: value for key, value in data.items() if key in allowed},
            route_group_policies=build_route_group_policies(effective_route_groups),
        )

    @staticmethod
    def _has_runtime_changes(changes: dict[str, list[str]]) -> bool:
        interesting = {
            "model_list",
            "router_settings",
            "deltallm_settings",
            "litellm_settings",
            "general_settings",
        }
        touched = (
            set(changes.get("added", []))
            | set(changes.get("removed", []))
            | set(changes.get("modified", []))
        )
        return bool(touched & interesting)

    @staticmethod
    def _is_theme_only_change(
        current_config: AppConfig | None,
        new_config: AppConfig,
        changes: dict[str, list[str]],
    ) -> bool:
        if current_config is None:
            return False
        touched = (
            set(changes.get("added", []))
            | set(changes.get("removed", []))
            | set(changes.get("modified", []))
        )
        if touched != {"general_settings"}:
            return False

        current_general = current_config.general_settings.model_dump(mode="python")
        next_general = new_config.general_settings.model_dump(mode="python")
        changed_general_fields = {
            key
            for key in current_general.keys() | next_general.keys()
            if current_general.get(key) != next_general.get(key)
        }
        return bool(changed_general_fields) and changed_general_fields <= _THEME_GENERAL_FIELDS

    @staticmethod
    def _validate_model_config(config: dict[str, Any]) -> None:
        has_params = "deltallm_params" in config or "litellm_params" in config
        if "model_name" not in config or not has_params:
            raise ValueError("Missing required model fields: model_name, deltallm_params")

        validate_provider_mode_compatibility(config)
