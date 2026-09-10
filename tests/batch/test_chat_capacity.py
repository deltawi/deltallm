import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.batch.chat_capacity import ChatDeploymentCapacity, bind_chat_capacity
from src.models.errors import ServiceUnavailableError
from src.router.execution import RequestDeadline
from src.router.router import Deployment


def deployment(cap=1, identity="answer"):
    return Deployment(
        identity,
        "answer",
        {"model": "openai/answer", "chat_batching": {"mode": "concurrent", "max_in_flight": cap}},
    )


async def test_shared_deployment_caps_and_worker_bound_release_after_fallback():
    capacity = ChatDeploymentCapacity(2)
    first, second = deployment(), deployment(identity="other")
    entered = asyncio.Event()
    waiting = asyncio.Event()

    async def fallback():
        waiting.set()
        async with capacity.slot(first, RequestDeadline.after(1)):
            entered.set()
        async with capacity.slot(second, RequestDeadline.after(1)):
            pass

    async with capacity.slot(first, RequestDeadline.after(1)):
        async with capacity.slot(second, RequestDeadline.after(1)):
            task = asyncio.create_task(fallback())
            await waiting.wait()
            assert not entered.is_set()
    await asyncio.wait_for(task, 1)
    assert capacity._total == 0


@pytest.mark.parametrize("cancel", [False, True])
async def test_wait_failure_is_local_and_never_releases_an_unowned_slot(cancel):
    capacity = ChatDeploymentCapacity(1)
    target = deployment()
    waiting = asyncio.Event()

    async def blocked():
        waiting.set()
        async with capacity.slot(target, RequestDeadline.after(0.02)):
            pytest.fail("admitted while the only slot is owned")

    async with capacity.slot(target, RequestDeadline.after(1)):
        task = asyncio.create_task(blocked())
        await waiting.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ServiceUnavailableError) as error:
                await task
            assert error.value.affects_deployment_health is False
        assert capacity._total == 1
    assert capacity._total == 0
    async with capacity.slot(target, RequestDeadline.after(1)):
        assert capacity._total == 1


async def test_mixed_generations_register_one_conservative_deployment_allowance():
    first = deployment(2)
    prepared = [
        SimpleNamespace(
            primary_deployment=target,
            request_context={},
            routing_generation=SimpleNamespace(generation_id=str(index), router=None),
        )
        for index, target in enumerate(
            [first, replace(first, deltallm_params=deployment(1).deltallm_params)]
        )
    ]
    capacity = bind_chat_capacity(prepared, worker_concurrency=4)
    assert capacity._limits == {"answer": 1}
    assert prepared[0].request_context == prepared[1].request_context


async def test_provider_failure_and_cancellation_release_owned_slots():
    capacity = ChatDeploymentCapacity(1)
    target = deployment()
    for error in [RuntimeError(), asyncio.CancelledError()]:
        with pytest.raises(type(error)):
            async with capacity.slot(target, RequestDeadline.after(1)):
                raise error
        assert capacity._total == 0


async def test_split_microbatch_stays_within_one_worker_pipeline():
    from src.batch.chat_worker_execution import ChatWorkerExecutionMixin

    started, release = asyncio.Event(), asyncio.Event()
    active = peak = completed = 0

    async def execute(job, prepared, **kwargs):
        nonlocal active, peak, completed
        active += 1
        peak = max(peak, active)
        started.set()
        try:
            await release.wait()
            completed += 1
        finally:
            active -= 1

    host = SimpleNamespace(
        _execute_prepared_chat_item=execute,
        config=SimpleNamespace(worker_concurrency=4),
    )
    from src.router.attempt_capacity import bind_attempt_capacity

    items = [
        SimpleNamespace(item=SimpleNamespace(item_id=str(i)), request_context={}) for i in range(3)
    ]
    for item in items:
        bind_attempt_capacity(item.request_context, ChatDeploymentCapacity(4))
    watches = {item.item.item_id: SimpleNamespace(lost=asyncio.Event()) for item in items}
    task = asyncio.create_task(
        ChatWorkerExecutionMixin._execute_chat_microbatch_fallback_items(
            host, object(), items, watches=watches, deadline=RequestDeadline.after(1)
        )
    )
    await started.wait()
    assert active == peak == 1
    release.set()
    await asyncio.wait_for(task, 1)
    assert completed == 3 and peak == 1


@pytest.fixture
def competing_answer_attempts(monkeypatch):
    """Hold the first provider until another answer reaches local admission."""
    second_attempt = asyncio.Event()
    slot = ChatDeploymentCapacity.slot
    count = 0

    @asynccontextmanager
    async def observe(self, deployment, deadline):
        nonlocal count
        count += 1
        if count >= 2:
            second_attempt.set()
        async with slot(self, deployment, deadline):
            yield

    monkeypatch.setattr(ChatDeploymentCapacity, "slot", observe)
    return second_attempt
