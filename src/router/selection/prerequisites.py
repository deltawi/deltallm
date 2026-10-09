from src.billing.operation_reservation import OperationReservation, OperationReservationStore
from src.cache.execution_eligibility import ResponseCacheEligibility
from src.router.selection.capacity import (
    CapacityAdmittedSelectorHop,
    SelectorCapacityBounds,
    SelectorCapacityOwner,
)
from src.router.selection.contracts import SelectorInvariantError, SelectorModelHop
from src.router.selection.economics import AccountedSelectorHop, ReservedSelectorAdmission
from src.router.selection.service import SelectorService


def admitted_selector_service(
    *,
    provider: SelectorModelHop,
    capacity_owner: SelectorCapacityOwner,
    capacity: SelectorCapacityBounds,
    billing: OperationReservationStore,
    operation: OperationReservation,
    cache: ResponseCacheEligibility,
) -> SelectorService:
    """The PR 3 execution boundary: dormant until the PR 4 authenticated edge.

    Composition order is budget/cache admission -> shared provider capacity ->
    durable dispatch intent -> one direct provider hop -> durable receipt -> release.
    This factory never constructs clients, starts workers or authorizes identities.
    """
    if capacity.health_ref.deployment_id != operation.attribution.deployment_id:
        raise SelectorInvariantError()
    if (
        capacity.token_allowance
        < operation.selector.max_input_tokens + operation.selector.max_output_tokens
    ):
        raise SelectorInvariantError()
    return SelectorService(
        CapacityAdmittedSelectorHop(
            owner=capacity_owner,
            bounds=capacity,
            hop=AccountedSelectorHop(store=billing, operation=operation, hop=provider),
        ),
        admission=ReservedSelectorAdmission(store=billing, operation=operation, cache=cache),
    )
