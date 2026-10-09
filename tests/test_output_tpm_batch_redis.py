import asyncio
import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.services.limit_counter import LimitCounter
from src.services.output_limit_types import OutputAccountingEvent, OutputPolicy, OutputScope
from tests.batch import selector_fixtures
from tests.batch.selector_fixtures import answer_calls

pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(
        not os.getenv("DELTALLM_TEST_REDIS_URL"), reason="Redis test URL is required"
    ),
]
selected_batch = selector_fixtures.selected_batch


@pytest.fixture
async def governed_batch(selected_batch):
    h = selected_batch
    from tests.test_routing_cache_identity import _publish

    for members in h.app.state.model_registry.values():
        for member in members:
            member["deltallm_params"]["api_base"] = "https://api.openai.com/v1"
    _publish(h.app, [h.policy])
    redis = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    environment = uuid4().hex
    h.app.state.limit_counter = LimitCounter(
        redis_client=redis, degraded_mode="fail_closed", environment=environment
    )
    record = next(iter(h.app.state._test_repo.records.values()))
    previous_token = record.token
    record.token = uuid4().hex + uuid4().hex
    h.app.state._test_repo.records[record.token] = h.app.state._test_repo.records.pop(
        previous_token
    )
    h.job.created_by_api_key = record.token
    record.rpm_limit = 1_000_000
    record.tpm_limit = 1_000_000_000
    record.output_tpm_limit = 30
    await h.app.state.key_service.invalidate_key_cache_by_hash(record.token)
    yield h, redis, environment, record
    await redis.aclose()


def output(identity):
    return OutputPolicy((OutputScope("key_output_tpm", identity, 30),))


async def test_sync_and_batch_share_output_and_claim_epochs_have_separate_ownership(governed_batch):
    h, redis, environment, record = governed_batch
    item = h.item()
    await h.worker._process_item(h.job, item)
    assert len(h.repository.completed_calls) == 1
    key = output(record.token).keys(environment=environment)[0]
    assert int(await redis.hget(key, "used")) == 5
    sync = await h.app.state.limit_counter.check_rate_limits_atomic([], output=output(record.token))
    assert int(await redis.hget(key, "used")) == 5
    await h.app.state.limit_counter.account_output(
        OutputAccountingEvent(sync.output_snapshot.policy, uuid4().hex, 2)
    )
    await h.worker._process_item(h.job, replace(item, claim_epoch=item.claim_epoch + 1))
    assert len(h.repository.completed_calls) == 2
    assert int(await redis.hget(key, "used")) == 12
    owners = [
        key
        async for key in redis.scan_iter(match=f"deltallm:{environment}:v2:output-tpm:receipt:*")
    ]
    assert len(owners) == 3
    assert all([json.loads(await redis.get(owner))["result"][0] == 1 for owner in owners])


async def test_cancelled_batch_execution_marks_unknown_and_does_not_commit(governed_batch):
    h, redis, environment, record = governed_batch
    started = asyncio.Event()
    provider = h.provider

    async def blocked(request):
        if (data := json.loads(request.content)).get(
            "max_completion_tokens", data.get("max_tokens")
        ) == 8:
            started.set()
            await asyncio.Event().wait()
        return await provider(request)

    h.provider = blocked
    task = asyncio.create_task(h.worker._process_item(h.job, h.item()))
    await asyncio.wait_for(started.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not h.repository.completed_calls
    assert int(await redis.hget(output(record.token).keys(environment=environment)[0], "used")) == 0
    assert (
        int(await redis.hget(output(record.token).keys(environment=environment)[0], "unknown")) == 1
    )
    assert not answer_calls(h)


async def test_governed_items_use_individual_calls_instead_of_native_microbatch(governed_batch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from tests.test_routing_cache_identity import _publish

    h, redis, environment, record = governed_batch
    h.policy.pop("selector")
    h.policy["members"] = [{"deployment_id": "quality"}]
    _publish(h.app, [h.policy])
    executor = AsyncMock()
    h.app.state.chat_microbatch_executor = SimpleNamespace(execute_chat_microbatch=executor)
    prepared = [await h.worker._prepare_item_for_execution(h.job, h.item(i)) for i in (1, 2, 3)]
    await h.worker._execution_engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    assert len(h.repository.completed_calls) == 3
    executor.assert_not_awaited()
    assert (
        int(await redis.hget(output(record.token).keys(environment=environment)[0], "used")) == 15
    )


async def test_stale_worker_known_output_counts_but_result_cannot_commit(
    governed_batch, monkeypatch
):
    from unittest.mock import AsyncMock

    h, redis, environment, record = governed_batch
    provider = h.provider

    async def lose_after_output(request):
        response = await provider(request)
        if json.loads(request.content).get("max_tokens") != 64:
            monkeypatch.setattr(
                h.worker._execution_engine, "_renew_item_lease_once", AsyncMock(return_value=False)
            )
        return response

    h.provider = lose_after_output
    await h.worker._process_item(h.job, h.item())
    assert not h.repository.completed_calls
    assert int(await redis.hget(output(record.token).keys(environment=environment)[0], "used")) == 5
