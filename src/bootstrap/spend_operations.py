"""Construct recovery from centrally owned allocations; no extra worker lifecycle."""

from starlette.datastructures import State

from src.billing.spend_operation_service import SpendOperationService


def build_spend_operations(state: State) -> SpendOperationService | None:
    if not getattr(state, "spend_operation_intents_enabled", False):
        return None
    admission = state.telemetry_prisma_manager.client
    settlement = state.telemetry_settlement_prisma_manager.client
    worker = state.telemetry_worker_prisma_manager.client
    if any(client is None for client in (admission, settlement, worker)):
        raise RuntimeError("Spend recovery requires admission, settlement and worker allocations")
    return SpendOperationService(admission=admission, settlement=settlement, worker=worker)
