import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from src.db.callable_targets import CallableTargetBindingRecord
from src.router.runtime_generation import with_authorization_snapshot
from src.models.errors import ServiceUnavailableError
from src.batch.chat_capacity import bind_chat_capacity
from src.router.execution import RequestDeadline

from tests.batch import selector_fixtures, test_chat_capacity
from tests.batch.selector_fixtures import selection_calls
from tests.router.selection.test_realtime_fallbacks import configure_fallback
from tests.test_routing_cache_identity import _publish
from tests.router.selection.provider_fixtures import response_body

pytestmark = pytest.mark.app
selected_batch = selector_fixtures.selected_batch
competing_answer_attempts = test_chat_capacity.competing_answer_attempts


@pytest.mark.parametrize(
    "mode", ["unreached", "economy", "quality", "mixed", "microbatch", "split"]
)
async def test_answer_deployment_cap_is_shared_by_selected_and_ordinary_traffic(
    selected_batch, competing_answer_attempts, mode
):
    h = selected_batch
    if mode == "unreached":
        await configure_fallback(h.app, h.app.state.http_client)
    runtime = h.app.state.routing_runtime_generation_store.require_snapshot()
    groups = deepcopy(list(runtime.route_groups))
    for member in h.app.state.model_registry["backing"]:
        member["deltallm_params"]["chat_batching"] = {"mode": "concurrent", "max_in_flight": 1}
    ordinary_traffic = mode in {"mixed", "microbatch", "split"}
    if ordinary_traffic:
        ordinary = deepcopy(h.policy)
        ordinary["key"] = "ordinary"
        ordinary.pop("selector")
        ordinary["members"] = [ordinary["members"][0]]
        ordinary["members"][0].pop("lane")
        groups.append(ordinary)
        grants = h.app.state.callable_target_grant_service
        grants.repository.bindings.append(
            CallableTargetBindingRecord(
                callable_target_binding_id="ordinary-binding",
                callable_key="ordinary",
                scope_type="organization",
                scope_id="org-default",
                enabled=True,
            )
        )
        await grants.reload()
    published = _publish(h.app, groups, failover_config=runtime.failover_config)
    h.app.state.routing_runtime_generation_store.replace(
        with_authorization_snapshot(published, h.app.state.callable_target_grant_service.snapshot())
    )
    provider = h.provider
    active = peak = 0

    async def observed(request):
        nonlocal active, peak
        if json.loads(request.content).get("max_tokens") == 64:
            return await provider(request)
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.wait_for(competing_answer_attempts.wait(), 1)
            return await provider(request)
        finally:
            active -= 1

    h.provider = observed
    if mode in {"microbatch", "split"}:
        for member in h.app.state.model_registry["backing"]:
            member["deltallm_params"]["chat_batching"].update(
                mode="sync_microbatch", upstream_max_batch_size=8
            )
        _publish(h.app, groups, failover_config=runtime.failover_config)

        async def execute_batch(*, requests, deployment, request_context):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.wait_for(competing_answer_attempts.wait(), 1)
                if mode == "split":
                    raise ServiceUnavailableError(
                        code="chat_microbatch_unsupported", affects_deployment_health=False
                    )
                return [
                    {
                        "index": index,
                        "response_body": response_body(text="answer"),
                        "usage": {"prompt_tokens": 13, "completion_tokens": 5, "total_tokens": 18},
                    }
                    for index in range(len(requests))
                ]
            finally:
                active -= 1

        h.app.state.chat_microbatch_executor = SimpleNamespace(
            execute_chat_microbatch=execute_batch
        )
    content = "complex" if mode == "quality" else "hello"
    items = [h.item(1, content), h.item(2, content)]
    if ordinary_traffic:
        items[1].request_body["model"] = "ordinary"
    if mode in {"microbatch", "split"}:
        items.append(h.item(3, content, model="ordinary"))
    await asyncio.wait_for(h.worker._process_items(h.job, items), 3)
    assert len(h.repository.completed_calls) == len(items)
    assert not h.repository.failed_calls
    assert peak == 1
    assert len(selection_calls(h)) == (0 if mode == "unreached" else 1 if ordinary_traffic else 2)


@pytest.mark.parametrize("lease_loss", [False, True])
async def test_microbatch_cancelled_while_waiting_releases_only_its_caller_leases(
    selected_batch, monkeypatch, lease_loss
):
    h = selected_batch
    h.policy.pop("selector")
    h.policy["members"] = [{"deployment_id": "quality"}]
    for member in h.app.state.model_registry["backing"]:
        member["deltallm_params"]["chat_batching"] = {
            "mode": "sync_microbatch",
            "max_in_flight": 1,
            "upstream_max_batch_size": 8,
        }
    _publish(h.app, [h.policy])
    prepared = [
        await h.worker._prepare_item_for_execution(h.job, h.item(index)) for index in (1, 2)
    ]
    capacity = bind_chat_capacity(prepared, worker_concurrency=2)
    started = asyncio.Event()
    slot = capacity.slot
    executor = AsyncMock()
    h.app.state.chat_microbatch_executor = SimpleNamespace(execute_chat_microbatch=executor)

    @asynccontextmanager
    async def wait_slot(deployment, deadline):
        started.set()
        async with slot(deployment, deadline):
            yield

    async with capacity.slot(prepared[0].primary_deployment, RequestDeadline.after(2)):
        monkeypatch.setattr(capacity, "slot", wait_slot)
        task = asyncio.create_task(
            h.worker._execution_engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
        )
        await asyncio.wait_for(started.wait(), 1)
        if lease_loss:
            h.repository.renew_item_lease = AsyncMock(return_value=False)
            await asyncio.wait_for(task, 1)
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert all(item.policy_lease is None for item in prepared)
        assert capacity._total == 1
        executor.assert_not_awaited()
    assert capacity._total == 0
    assert not h.repository.completed_calls
