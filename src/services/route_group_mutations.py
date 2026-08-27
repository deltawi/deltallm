from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import logging
from typing import Protocol

from src.db.callable_key_locks import lock_callable_keys
from src.db.callable_targets import CallableTargetBindingRepository
from src.db.repositories import ModelDeploymentRepository
from src.db.route_groups import RouteGroupRepository

logger = logging.getLogger(__name__)


class RouteGroupDeleteRepository(Protocol):
    async def delete_group(self, group_key: str) -> bool: ...


class CallableBindingDeleteRepository(Protocol):
    async def delete_by_callable_key(self, callable_key: str) -> int: ...


@dataclass(frozen=True, slots=True)
class RouteGroupDeleteResult:
    deleted: bool
    callable_bindings_deleted: int = 0
    warnings: tuple[str, ...] = ()


class RouteGroupMutationService:
    """Own durable multi-table route-group mutations."""

    def __init__(
        self,
        *,
        route_groups: RouteGroupDeleteRepository,
        callable_bindings: CallableBindingDeleteRepository | None,
        model_deployments: ModelDeploymentRepository | None = None,
        model_registry_getter: Callable[[], Mapping[str, Sequence[object]] | None] | None = None,
    ) -> None:
        self.route_groups = route_groups
        self.callable_bindings = callable_bindings
        self.model_deployments = model_deployments
        self.model_registry_getter = model_registry_getter

    async def delete_group(self, group_key: str) -> RouteGroupDeleteResult:
        route_groups = self.route_groups
        if isinstance(route_groups, RouteGroupRepository) and route_groups.supports_transactions():
            return await self._delete_transactionally(route_groups, group_key)

        # Reduced/in-memory configurations have no shared transaction owner.
        # Preserve their compatibility while classifying post-delete cleanup.
        deleted = await route_groups.delete_group(group_key)
        if not deleted:
            return RouteGroupDeleteResult(deleted=False)
        if await self._has_replacement_model(group_key):
            return RouteGroupDeleteResult(deleted=True)
        try:
            bindings_deleted = await self._delete_callable_bindings(group_key)
        except Exception:
            logger.warning(
                "callable-target binding cleanup failed after route-group deletion",
                exc_info=True,
            )
            return RouteGroupDeleteResult(
                deleted=True,
                warnings=("Mutation committed, but callable-target binding cleanup failed",),
            )
        return RouteGroupDeleteResult(
            deleted=True,
            callable_bindings_deleted=bindings_deleted,
        )

    async def _delete_transactionally(
        self,
        route_groups: RouteGroupRepository,
        group_key: str,
    ) -> RouteGroupDeleteResult:
        prisma = route_groups.prisma
        if prisma is None or not hasattr(prisma, "tx"):
            raise RuntimeError("route-group deletion requires transaction support")
        async with prisma.tx() as tx:
            await lock_callable_keys(tx, group_key)
            deleted = await route_groups.with_db(tx).delete_group(group_key)
            if not deleted:
                return RouteGroupDeleteResult(deleted=False)
            if await self._has_replacement_model(group_key, transaction=tx):
                bindings_deleted = 0
            else:
                bindings_deleted = await CallableTargetBindingRepository(tx).delete_by_callable_key(
                    group_key
                )
        return RouteGroupDeleteResult(
            deleted=True,
            callable_bindings_deleted=bindings_deleted,
        )

    async def _delete_callable_bindings(self, group_key: str) -> int:
        if self.callable_bindings is None:
            return 0
        return await self.callable_bindings.delete_by_callable_key(group_key)

    async def _has_replacement_model(
        self,
        group_key: str,
        *,
        transaction: object | None = None,
    ) -> bool:
        if self.model_deployments is not None:
            repository = (
                self.model_deployments.with_db(transaction)
                if transaction is not None
                else self.model_deployments
            )
            if await repository.has_model_name(
                group_key,
                lock_for_share=transaction is not None,
            ):
                return True

        registry = self.model_registry_getter() if self.model_registry_getter is not None else None
        return bool(registry is not None and registry.get(group_key))
