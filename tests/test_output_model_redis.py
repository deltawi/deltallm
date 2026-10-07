import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from src.models.errors import RateLimitError
from src.models.responses import UserAPIKeyAuth
from src.services.limit_counter import LimitCounter
from src.services.output_admission import prepare_output_policy
from src.services.output_limit_types import OutputAccountingEvent, OutputPolicy, OutputScope
from src.services.output_limit_redis import OutputUsageUnknownError
from tests.test_output_model_policy import compiled, tier_service

from tests.test_output_tpm_redis import redis_client as shared_redis_client
from tests.test_output_tpm_text_redis import output_app as shared_output_app
from tests.test_output_tpm_batch_redis import governed_batch as shared_governed_batch
from tests.test_output_tpm_batch_redis import selected_batch as selected_batch

redis_client = shared_redis_client
output_app = shared_output_app
governed_batch = shared_governed_batch

pytestmark = pytest.mark.redis


async def test_seven_scope_atomic_accounting_and_organization_sharing(redis_client):
    identity = uuid4().hex
    counter = LimitCounter(
        redis_client=redis_client, degraded_mode="fail_closed", environment=identity
    )
    auth = UserAPIKeyAuth(
        api_key="k1",
        user_id="u",
        team_id="t",
        organization_id="org-1",
        org_output_tpm_limit=1000,
        team_output_tpm_limit=1000,
        user_output_tpm_limit=1000,
        key_output_tpm_limit=1000,
        key_model_output_tpm_limit={"m": 1000},
        team_model_output_tpm_limit={"m": 1000},
    )
    service = tier_service(compiled(10))
    policy = prepare_output_policy(auth, model="m", tier_policy_service=service)
    replicas = [
        LimitCounter(redis_client=redis_client, degraded_mode="fail_closed", environment=identity)
        for _ in range(8)
    ]
    results = await asyncio.gather(
        *(r.check_rate_limits_atomic([], output=policy) for r in replicas)
    )
    assert all(result.output_snapshot is not None for result in results)
    events = [OutputAccountingEvent(policy, uuid4().hex, 2) for _ in results]
    await asyncio.gather(
        *(r.account_output(event) for r, event in zip(replicas, events, strict=True))
    )
    replay = await counter.account_output(events[-1])
    assert all(value >= 2 for value in replay.current_values)
    assert [
        int(await redis_client.hget(key, "used")) for key in policy.keys(environment=identity)
    ] == [16] * 7
    other_key = prepare_output_policy(
        auth.model_copy(update={"api_key": "k2", "team_id": "t2"}),
        model="m",
        tier_policy_service=service,
    )
    with pytest.raises(RateLimitError) as error:
        await counter.check_rate_limits_atomic([], output=other_key)
    assert error.value.param == "org_model_output_tpm"
    other_model = prepare_output_policy(auth, model="other", tier_policy_service=service)
    assert (
        await counter.check_rate_limits_atomic([], output=other_model)
    ).output_snapshot is not None
    raised = prepare_output_policy(
        auth, model="m", tier_policy_service=tier_service(compiled(20, "v2"))
    )
    assert (
        await counter.check_rate_limits_atomic([], output=raised)
    ).output_snapshot.current_values == (16,) * 7


async def test_unknown_model_usage_blocks_siblings_without_poisoning_other_models(redis_client):
    environment = uuid4().hex
    counter = LimitCounter(
        redis_client=redis_client, degraded_mode="fail_closed", environment=environment
    )
    policy = OutputPolicy(
        (
            OutputScope("team_model_output_tpm", "t", 10, "m"),
            OutputScope("key_model_output_tpm", "k1", 10, "m"),
        )
    )
    await counter.account_output(OutputAccountingEvent(policy, uuid4().hex, None))
    sibling = OutputPolicy(
        (
            OutputScope("team_model_output_tpm", "t", 10, "m"),
            OutputScope("key_model_output_tpm", "k2", 10, "m"),
        )
    )
    with pytest.raises(OutputUsageUnknownError):
        await counter.check_rate_limits_atomic([], output=sibling)
    other = OutputPolicy((OutputScope("team_model_output_tpm", "t", 10, "other"),))
    assert (await counter.check_rate_limits_atomic([], output=other)).output_snapshot is not None


@pytest.mark.parametrize(
    "endpoint,body",
    [
        ("/v1/chat/completions", {"messages": [{"role": "user", "content": "hello"}]}),
        ("/v1/completions", {"prompt": "hello"}),
        ("/v1/responses", {"input": "hello"}),
        ("/v1/messages", {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 20}),
    ],
)
async def test_model_only_limits_cover_all_text_entry_points(output_app, endpoint, body):
    app, redis, record, environment = output_app
    record.output_tpm_limit = None
    record.model_output_tpm_limit = {"gpt-4o-mini": 1}
    await app.state.key_service.invalidate_key_cache_by_hash(record.token)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json={"model": "gpt-4o-mini", **body},
        )
        second = await client.post(
            endpoint,
            headers={"Authorization": f"Bearer {app.state._test_key}"},
            json={"model": "gpt-4o-mini", **body},
        )
    assert first.status_code == 200, first.text
    assert second.status_code == 429, second.text
    assert second.headers["x-deltallm-ratelimit-output-scope"] == "key_model_output_tpm"


async def test_model_only_batch_policy_uses_individual_attributed_calls(governed_batch):
    from unittest.mock import AsyncMock
    from tests.test_routing_cache_identity import _publish

    h, redis, environment, record = governed_batch
    record.output_tpm_limit = None
    record.model_output_tpm_limit = {h.policy["key"]: 30}
    await h.app.state.key_service.invalidate_key_cache_by_hash(record.token)
    h.policy.pop("selector")
    h.policy["members"] = [{"deployment_id": "quality"}]
    _publish(h.app, [h.policy])
    executor = AsyncMock()
    h.app.state.chat_microbatch_executor = SimpleNamespace(execute_chat_microbatch=executor)
    prepared = [await h.worker._prepare_item_for_execution(h.job, h.item(i)) for i in (1, 2, 3)]
    await h.worker._execution_engine._execute_prepared_chat_microbatch_chunk(h.job, prepared)
    executor.assert_not_awaited()
    assert len(h.repository.completed_calls) == 3
    policy = OutputPolicy((OutputScope("key_model_output_tpm", record.token, 30, h.policy["key"]),))
    assert int(await redis.hget(policy.keys(environment=environment)[0], "used")) == 15
