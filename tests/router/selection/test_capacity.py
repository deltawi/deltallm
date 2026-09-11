import asyncio
from unittest.mock import AsyncMock

import pytest

from src.models.errors import ServiceUnavailableError
from src.router.candidates import (
    AttemptPermit,
    AttemptRejectionReason,
    AttemptCapacity,
    AttemptCapacityLimit,
)
from src.router.health_state import DeploymentHealthRef
from src.router.selection.capacity import CapacityAdmittedSelectorHop, SelectorCapacityBounds
from src.router.selection.contracts import (
    SelectorCause,
    SelectorHopSuccess,
    SelectorPrompt,
    UnknownSelectorUsage,
)
from src.router.state import RedisStateBackend


def setup_hop():
    ref = DeploymentHealthRef("classifier", "generation")
    owner, provider = AsyncMock(), AsyncMock()

    async def acquire(health_ref, capacity, **kwargs):
        return AttemptPermit(
            health_ref.deployment_id, health_ref, True, "redis", capacity.owner_token
        )

    owner.acquire_attempt.side_effect = acquire
    provider.invoke.return_value = SelectorHopSuccess(
        text='{"lane":"economy"}', usage=UnknownSelectorUsage()
    )
    hop = CapacityAdmittedSelectorHop(
        owner=owner,
        hop=provider,
        bounds=SelectorCapacityBounds(ref, rpm=10, tpm=1000, concurrency=2, token_allowance=100),
    )
    kwargs = dict(
        deployment_id="classifier",
        prompt=SelectorPrompt(system="s", user="u"),
        expires_at=asyncio.get_running_loop().time() + 1,
    )
    return hop, owner, provider, kwargs


async def test_shared_capacity_has_one_owner_and_consumes_hidden_hop_only():
    hop, owner, provider, kwargs = setup_hop()
    await hop.invoke(**kwargs)
    owner.acquire_attempt.assert_awaited_once()
    capacity = owner.acquire_attempt.call_args.args[1]
    assert [(limit.counter, limit.consume) for limit in capacity.limits] == [
        ("rpm", 1),
        ("tpm", 100),
    ]
    assert capacity.max_concurrency == 2 and capacity.require_shared
    provider.invoke.assert_awaited_once_with(**kwargs)
    permit = owner.release_attempt.call_args.args[0]
    assert permit.owner_token == capacity.owner_token and permit.acquired
    owner.release_attempt.assert_awaited_once()


@pytest.mark.parametrize("failure", ["denied", "unavailable", "cancelled", "deadline", "provider"])
async def test_capacity_terminal_paths_release_owned_or_ambiguous_permit(failure):
    hop, owner, provider, kwargs = setup_hop()
    if failure == "denied":
        owner.acquire_attempt.side_effect = None
        owner.acquire_attempt.return_value = AttemptPermit(
            "classifier",
            DeploymentHealthRef("classifier"),
            False,
            rejection_reason=AttemptRejectionReason.CAPACITY,
        )
        assert (await hop.invoke(**kwargs)).cause == SelectorCause.CAPACITY_DENIED
        owner.release_attempt.assert_not_awaited()
        provider.invoke.assert_not_awaited()
        return
    if failure == "unavailable":
        owner.acquire_attempt.side_effect = ServiceUnavailableError()
        assert (await hop.invoke(**kwargs)).cause == SelectorCause.CAPACITY_UNAVAILABLE
        provider.invoke.assert_not_awaited()
    else:
        error = {
            "cancelled": asyncio.CancelledError,
            "deadline": TimeoutError,
            "provider": RuntimeError,
        }[failure]
        if failure == "cancelled":
            owner.acquire_attempt.side_effect = error()
        else:
            provider.invoke.side_effect = error()
        with pytest.raises(error):
            await hop.invoke(**kwargs)
    owner.release_attempt.assert_awaited_once()
    assert owner.release_attempt.call_args.args[0].owner_token


async def test_reserved_capacity_never_falls_back_to_process_local_admission():
    hop, _, provider, kwargs = setup_hop()
    hop._owner = RedisStateBackend(None, degraded_mode="fail_open")
    result = await hop.invoke(**kwargs)
    assert result.cause == SelectorCause.CAPACITY_UNAVAILABLE
    provider.invoke.assert_not_awaited()


@pytest.mark.parametrize("invalid", [True, 1.1, 0, 2**31])
def test_selector_capacity_rejects_non_integral_or_unbounded_concurrency(invalid):
    with pytest.raises(ValueError):
        AttemptCapacity(max_concurrency=invalid, require_shared=True)


@pytest.mark.parametrize("invalid", [True, 1.1, 2**31])
def test_selector_capacity_rejects_non_integral_or_unbounded_counters(invalid):
    with pytest.raises(ValueError):
        AttemptCapacity(limits=(AttemptCapacityLimit("rpm", invalid, 1),), require_shared=True)
