from __future__ import annotations

from starlette.datastructures import State
from src.billing.operation_reservation import BillingOperationUnavailable

from src.billing.spend_ingestion import SpendIngestionService
from src.db.billing_operation_recovery import BillingOperationRecovery
from src.db.billing_operations import BillingOperationRepository
from src.router.runtime_generation import require_routing_runtime_generation
from src.router.selection.runtime import SelectorExecutionFactory


def configure_selector_execution(state: State, spend: SpendIngestionService) -> None:
    """Extend the existing spend lifecycle; no extra client, pool, queue or worker."""
    state.selector_execution_factory = None
    state.routing_runtime_generation_store.set_selector_activation_check(_unavailable)
    if not spend.config.enabled or not spend.config.worker_enabled:
        if require_routing_runtime_generation(state).selectors:
            raise RuntimeError("selector activation requires durable spend outbox and its worker")
        return
    operations = BillingOperationRepository(spend.db)
    spend.operation_recovery = BillingOperationRecovery(
        operations,
        max_pending_events=spend.config.max_pending_events,
        max_attempts=spend.config.max_attempts,
        selector_events_only=True,
    )
    state.selector_execution_factory = SelectorExecutionFactory(
        client=state.http_client,
        adapters=state.provider_error_mapper_registry,
        billing=operations,
        default_openai_base_url=state.settings.openai_base_url,
        accounting_ready=lambda: (
            spend.config.enabled and spend.config.worker_enabled and spend.worker_health.ready
        ),
    )
    state.route_group_repository.selector_activation_check = (
        state.selector_execution_factory.require_ready
    )
    state.routing_runtime_generation_store.set_selector_activation_check(
        state.selector_execution_factory.require_ready
    )


def _unavailable() -> None:
    raise BillingOperationUnavailable()
