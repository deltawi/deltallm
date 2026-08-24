from __future__ import annotations

from src.services.callable_targets import CallableTarget
from src.router.runtime_authorization import CallableTargetGrantSnapshot
from src.services.routing_authorization import RoutingAuthorizationReconciler


class _GrantReloader:
    def __init__(self) -> None:
        self.calls = 0

        self.published: CallableTargetGrantSnapshot | None = None

    async def build_snapshot(self, *, callable_target_catalog=None):  # noqa: ANN001, ANN201
        assert callable_target_catalog is not None
        self.calls += 1
        return CallableTargetGrantSnapshot.empty()

    def replace_snapshot(self, snapshot: CallableTargetGrantSnapshot) -> None:
        self.published = snapshot


async def test_routing_authorization_reconciler_reloads_grants_without_invalidation() -> None:
    grants = _GrantReloader()
    reconciler = RoutingAuthorizationReconciler(
        db=None,
        callable_target_bindings=None,
        route_groups=None,
        callable_target_grants=grants,
    )

    changed = await reconciler.reconcile(
        {"support": CallableTarget(key="support", target_type="route_group")}
    )

    assert changed == 0
    assert grants.calls == 1
    assert grants.published is not None
