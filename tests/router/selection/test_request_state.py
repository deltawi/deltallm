import asyncio

import pytest

from src.router.selection.contracts import SelectorJoinLimitError
from src.router.selection.request_state import SelectorState
from src.router.selection.service import SelectorService
from tests.router.selection.test_service import hop, select, state


@pytest.mark.asyncio
async def test_owner_and_eight_joiners_share_one_decision_and_overflow_rejects(
    selector_policy, policy_identity
):
    provider, operation = hop(), state()
    entered, release = asyncio.Event(), asyncio.Event()
    response = provider.invoke.return_value

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return response

    provider.invoke.side_effect = delayed
    service = SelectorService(provider)
    async with asyncio.TaskGroup() as group:
        owner = group.create_task(select(service, operation, selector_policy, policy_identity))
        await entered.wait()
        joiners = [
            group.create_task(select(service, operation, selector_policy, policy_identity))
            for _ in range(8)
        ]
        await asyncio.sleep(0)
        with pytest.raises(SelectorJoinLimitError):
            await select(service, operation, selector_policy, policy_identity)
        release.set()
    assert all(joiner.result() is owner.result() for joiner in joiners)
    assert operation._joiners == 0 and operation.state is SelectorState.DECIDED
    provider.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_joiner_cancellation_does_not_cancel_owner(selector_policy, policy_identity):
    provider, operation = hop(), state()
    entered, release = asyncio.Event(), asyncio.Event()
    response = provider.invoke.return_value

    async def delayed(**kwargs):
        entered.set()
        await release.wait()
        return response

    provider.invoke.side_effect = delayed
    service = SelectorService(provider)
    async with asyncio.TaskGroup() as group:
        owner = group.create_task(select(service, operation, selector_policy, policy_identity))
        await entered.wait()
        joiner = group.create_task(select(service, operation, selector_policy, policy_identity))
        await asyncio.sleep(0)
        joiner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await joiner
        assert not owner.done() and not operation._done.cancelled()
        release.set()
    assert owner.result().lane == "economy" and operation._joiners == 0


@pytest.mark.asyncio
async def test_owner_cancel_wakes_joiners_closes_hop_and_never_restarts(
    selector_policy, policy_identity
):
    provider, operation = hop(), state()
    entered, closed = asyncio.Event(), asyncio.Event()

    async def delayed(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()

    provider.invoke.side_effect = delayed
    service = SelectorService(provider)
    async with asyncio.TaskGroup() as group:
        owner = group.create_task(select(service, operation, selector_policy, policy_identity))
        await entered.wait()
        joiner = group.create_task(select(service, operation, selector_policy, policy_identity))
        await asyncio.sleep(0)
        owner.cancel()
        for task in (owner, joiner):
            with pytest.raises(asyncio.CancelledError):
                await task
    with pytest.raises(asyncio.CancelledError):
        await select(service, operation, selector_policy, policy_identity)
    assert closed.is_set() and operation.state is SelectorState.ABORTED
    assert operation._done.result() is None and operation._joiners == 0
    provider.invoke.assert_awaited_once()
