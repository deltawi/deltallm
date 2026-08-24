from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from src.db.callable_targets import CallableTargetBindingRepository
from src.db.route_groups import RouteGroupRepository
from src.router.runtime_authorization import CallableTargetGrantSnapshot
from src.services.callable_targets import CallableTarget
from src.services.organization_callable_target_sync import (
    sync_auto_follow_organization_bindings,
)


class CallableTargetGrantSnapshotBuilder(Protocol):
    async def build_snapshot(
        self,
        *,
        callable_target_catalog: Mapping[str, Any] | None = None,
    ) -> CallableTargetGrantSnapshot: ...

    def replace_snapshot(self, snapshot: CallableTargetGrantSnapshot) -> None: ...


class RoutingAuthorizationReconciler:
    """Reconcile authorization state after one routing generation is published."""

    def __init__(
        self,
        *,
        db: Any | None,
        callable_target_bindings: CallableTargetBindingRepository | None,
        route_groups: RouteGroupRepository | None,
        callable_target_grants: CallableTargetGrantSnapshotBuilder | None,
    ) -> None:
        self.db = db
        self.callable_target_bindings = callable_target_bindings
        self.route_groups = route_groups
        self.callable_target_grants = callable_target_grants

    async def prepare(
        self,
        catalog: Mapping[str, CallableTarget],
    ) -> tuple[int, CallableTargetGrantSnapshot]:
        changed = await sync_auto_follow_organization_bindings(
            db=self.db,
            callable_target_binding_repository=self.callable_target_bindings,
            route_group_repository=self.route_groups,
            callable_target_catalog=dict(catalog),
        )
        snapshot = (
            await self.callable_target_grants.build_snapshot(
                callable_target_catalog=catalog,
            )
            if self.callable_target_grants is not None
            else CallableTargetGrantSnapshot.empty()
        )
        return changed, snapshot

    async def reconcile(self, catalog: Mapping[str, CallableTarget]) -> int:
        """Compatibility entry point for non-routing callers during migration."""

        changed, snapshot = await self.prepare(catalog)
        if self.callable_target_grants is not None:
            self.callable_target_grants.replace_snapshot(snapshot)
        return changed
