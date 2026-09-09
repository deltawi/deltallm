from types import SimpleNamespace

import pytest
from starlette.datastructures import State

from src.billing.operation_reservation import BillingOperationUnavailable
from src.bootstrap.selector import configure_selector_execution
from src.db.billing_operation_recovery import BillingOperationRecovery
from src.router.selection.runtime import SelectorExecutionFactory
from src.router.runtime_generation import RoutingRuntimeGenerationStore


def test_selector_composition_reuses_spend_owner_and_tracks_worker_health():
    spend = SimpleNamespace(
        db=object(),
        config=SimpleNamespace(
            enabled=True, worker_enabled=True, max_pending_events=100, max_attempts=10
        ),
        worker_health=SimpleNamespace(ready=True),
    )
    state = State(
        {
            "http_client": object(),
            "provider_error_mapper_registry": object(),
            "settings": SimpleNamespace(openai_base_url="https://mock.test/v1"),
            "route_group_repository": SimpleNamespace(),
        }
    )
    state.routing_runtime_generation_store = RoutingRuntimeGenerationStore()
    configure_selector_execution(state, spend)
    assert isinstance(state.selector_execution_factory, SelectorExecutionFactory)
    assert isinstance(spend.operation_recovery, BillingOperationRecovery)
    assert spend.operation_recovery.selector_events_only
    state.route_group_repository.selector_activation_check()
    spend.worker_health.ready = False
    with pytest.raises(BillingOperationUnavailable):
        state.route_group_repository.selector_activation_check()


@pytest.mark.parametrize("enabled,worker", [(False, False), (True, False)])
@pytest.mark.parametrize("selectors", [{}, {"selected": object()}])
def test_selector_cannot_start_without_durable_spend_worker(
    monkeypatch, enabled, worker, selectors
):
    state = State()
    state.routing_runtime_generation_store = RoutingRuntimeGenerationStore()
    spend = SimpleNamespace(config=SimpleNamespace(enabled=enabled, worker_enabled=worker))
    monkeypatch.setattr(
        "src.bootstrap.selector.require_routing_runtime_generation",
        lambda _: SimpleNamespace(selectors=selectors),
    )
    if selectors:
        with pytest.raises(RuntimeError, match="durable spend outbox"):
            configure_selector_execution(state, spend)
    else:
        configure_selector_execution(state, spend)
        assert state.selector_execution_factory is None
    previous = SimpleNamespace(selectors={})
    state.routing_runtime_generation_store.replace(previous)
    with pytest.raises(BillingOperationUnavailable):
        state.routing_runtime_generation_store.replace(
            SimpleNamespace(selectors={"selected": object()})
        )
    assert state.routing_runtime_generation_store.require_snapshot() is previous
