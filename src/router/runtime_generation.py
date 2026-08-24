from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4

from src.config import AppConfig
from src.router.cooldown import CooldownManager
from src.router.failover import FallbackConfig, FailoverManager
from src.router.registry import DeploymentRegistryStore
from src.router.router import Router, RouterConfig, RoutingStrategy
from src.router.runtime_authorization import CallableTargetGrantSnapshot


@dataclass(frozen=True, slots=True)
class RoutingRuntimeGeneration:
    """A completely validated routing generation ready for synchronous publication."""

    generation_id: str
    revision: int
    app_config: AppConfig
    model_registry: Mapping[str, tuple[dict[str, Any], ...]]
    route_groups: tuple[dict[str, Any], ...]
    callable_target_catalog: Mapping[str, Any]
    authorization_snapshot: CallableTargetGrantSnapshot
    deployment_registry: DeploymentRegistryStore
    strategy: RoutingStrategy
    router_config: RouterConfig
    failover_config: FallbackConfig
    salt_key: str
    router: Router
    failover_manager: FailoverManager
    cooldown_manager: CooldownManager
    source: str = "config_only"
    requires_reconciliation: bool = False

    @classmethod
    def create(
        cls,
        *,
        revision: int,
        app_config: AppConfig,
        model_registry: Mapping[str, list[dict[str, Any]]],
        route_groups: list[dict[str, Any]],
        callable_target_catalog: Mapping[str, Any],
        authorization_snapshot: CallableTargetGrantSnapshot | None = None,
        deployment_registry: DeploymentRegistryStore,
        strategy: RoutingStrategy,
        router_config: RouterConfig,
        failover_config: FallbackConfig,
        salt_key: str,
        router: Router,
        failover_manager: FailoverManager,
        cooldown_manager: CooldownManager,
        source: str = "config_only",
        requires_reconciliation: bool = False,
    ) -> RoutingRuntimeGeneration:
        return cls(
            generation_id=uuid4().hex,
            revision=revision,
            app_config=app_config,
            model_registry=MappingProxyType(
                {key: tuple(entries) for key, entries in model_registry.items()}
            ),
            route_groups=tuple(route_groups),
            callable_target_catalog=MappingProxyType(dict(callable_target_catalog)),
            authorization_snapshot=authorization_snapshot or CallableTargetGrantSnapshot.empty(),
            deployment_registry=deployment_registry,
            strategy=strategy,
            router_config=router_config,
            failover_config=failover_config,
            salt_key=salt_key,
            router=router,
            failover_manager=failover_manager,
            cooldown_manager=cooldown_manager,
            source=source,
            requires_reconciliation=requires_reconciliation,
        )


@dataclass(frozen=True, slots=True)
class RoutingRuntimeAppliedState:
    revision: int
    source: str
    requires_reconciliation: bool = False


class RoutingRuntimeGenerationStore:
    """Owns the one live routing-generation reference."""

    def __init__(self, generation: RoutingRuntimeGeneration | None = None) -> None:
        self._current = generation

    def snapshot(self) -> RoutingRuntimeGeneration | None:
        return self._current

    def require_snapshot(self) -> RoutingRuntimeGeneration:
        generation = self._current
        if generation is None:
            raise RuntimeError("routing runtime generation is unavailable")
        return generation

    def replace(self, generation: RoutingRuntimeGeneration) -> None:
        self._current = generation


class RoutingRuntimeRouterProvider:
    """Resolve the router from the current immutable generation on demand."""

    def __init__(self, store: RoutingRuntimeGenerationStore) -> None:
        self._store = store

    def snapshot(self) -> Router:
        return self._store.require_snapshot().router

    def resolve_model_group(self, model_name: str) -> str:
        return self.snapshot().resolve_model_group(model_name)

    @property
    def config(self) -> RouterConfig:
        return self.snapshot().config

    @property
    def deployment_registry(self) -> DeploymentRegistryStore:
        return self.snapshot().deployment_registry

    @property
    def state(self) -> Any:
        return self.snapshot().state


def require_routing_runtime_generation(app_state: Any) -> RoutingRuntimeGeneration:
    """Resolve the request/worker runtime through its one authoritative store."""

    store = getattr(app_state, "routing_runtime_generation_store", None)
    if not isinstance(store, RoutingRuntimeGenerationStore):
        raise RuntimeError("routing runtime generation store is unavailable")
    return store.require_snapshot()


def pin_routing_runtime_generation(
    app_state: Any,
    operation_state: Any,
) -> RoutingRuntimeGeneration:
    """Pin one generation before any asynchronous policy or routing work."""

    pinned = getattr(operation_state, "routing_runtime_generation", None)
    if isinstance(pinned, RoutingRuntimeGeneration):
        return pinned
    generation = require_routing_runtime_generation(app_state)
    operation_state.routing_runtime_generation = generation
    return generation


def with_authorization_snapshot(
    generation: RoutingRuntimeGeneration,
    snapshot: CallableTargetGrantSnapshot,
    *,
    callable_target_catalog: Mapping[str, Any] | None = None,
) -> RoutingRuntimeGeneration:
    """Return a new publishable identity containing prepared authorization state."""

    return replace(
        generation,
        generation_id=uuid4().hex,
        authorization_snapshot=snapshot,
        callable_target_catalog=(
            MappingProxyType(dict(callable_target_catalog))
            if callable_target_catalog is not None
            else generation.callable_target_catalog
        ),
    )


def rebuild_routing_runtime_generation(
    current: RoutingRuntimeGeneration,
    *,
    model_registry: Mapping[str, list[dict[str, Any]]],
    route_groups: list[dict[str, Any]],
    callable_target_catalog: Mapping[str, Any],
    deployment_registry: DeploymentRegistryStore,
) -> RoutingRuntimeGeneration:
    """Build a compatibility replacement without mutating the live generation."""

    router = Router(
        strategy=current.strategy,
        state_backend=current.router.state,
        config=current.router_config,
        deployment_registry=deployment_registry,
    )
    failover_manager = FailoverManager(
        config=current.failover_config,
        candidate_planner=router,
        state_backend=current.failover_manager.state,
        cooldown_manager=current.cooldown_manager,
        event_journal=current.failover_manager.event_journal,
    )
    return RoutingRuntimeGeneration.create(
        revision=current.revision,
        app_config=current.app_config,
        model_registry=model_registry,
        route_groups=route_groups,
        callable_target_catalog=callable_target_catalog,
        authorization_snapshot=current.authorization_snapshot,
        deployment_registry=deployment_registry,
        strategy=current.strategy,
        router_config=current.router_config,
        failover_config=current.failover_config,
        salt_key=current.salt_key,
        router=router,
        failover_manager=failover_manager,
        cooldown_manager=current.cooldown_manager,
        source=current.source,
        requires_reconciliation=current.requires_reconciliation,
    )
