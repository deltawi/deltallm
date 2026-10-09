from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.models.errors import RateLimitError, ServiceUnavailableError
from src.models.output_limits import MAX_OUTPUT_TOKENS
from src.services.limit_counter import LimitCounter, ParallelLimitCheck, RateLimitCheck
from src.services.output_limit_types import OutputAccountingEvent, OutputPolicy, OutputScope
from src.services.output_limit_redis import OutputUsageUnknownError

pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(
        not os.getenv("DELTALLM_TEST_REDIS_URL"), reason="Redis test URL is required"
    ),
]


@pytest.fixture
async def redis_client():
    client = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    yield client
    await client.aclose()


def policy(identity: str, limit: int = 100) -> OutputPolicy:
    return OutputPolicy(
        tuple(
            OutputScope(scope, identity, limit)
            for scope in ("org_output_tpm", "team_output_tpm", "user_output_tpm", "key_output_tpm")
        )
    )


def limiter(client):
    return LimitCounter(redis_client=client, degraded_mode="fail_closed", environment="test")


def event(output, actual, identity=None):
    return OutputAccountingEvent(output, identity or uuid4().hex, actual)


@pytest.mark.parametrize("unified", [False, True])
async def test_concurrent_replicas_allow_inflight_overage_then_block(redis_client, unified):
    other = Redis.from_url(os.environ["DELTALLM_TEST_REDIS_URL"], decode_responses=True)
    try:
        counters = [limiter(redis_client), limiter(other)]
        output = policy(uuid4().hex)

        async def acquire(i):
            if unified:
                return (
                    await counters[i % 2].check_rate_limits_and_tier_fair_share_atomic(
                        [],
                        [],
                        parallel_checks=[
                            ParallelLimitCheck("team", output.scopes[0].entity_id, 100)
                        ],
                        output=output,
                    )
                ).rate_result
            return await counters[i % 2].check_rate_limits_atomic([], output=output)

        results = await asyncio.gather(*(acquire(i) for i in range(50)))
        assert all(result.output_snapshot.current_values == (0,) * 4 for result in results)
        assert await redis_client.exists(*output.keys(environment="test")) == 0
        await asyncio.gather(
            *(counters[i % 2].account_output(event(output, 10)) for i in range(50))
        )
        assert [
            int(await redis_client.hget(key, "used")) for key in output.keys(environment="test")
        ] == [500] * 4
        with pytest.raises(RateLimitError) as error:
            await acquire(0)
        assert error.value.rate_limit_current == 500
    finally:
        await other.aclose()


async def test_final_receipt_is_idempotent_and_cannot_change_evidence(redis_client):
    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    completed = event(output, 120)
    first = await counter.account_output(completed)
    assert first == await counter.account_output(completed)
    with pytest.raises(ServiceUnavailableError):
        await counter.account_output(replace(completed, actual=121))
    keys = completed.keys(environment="test")
    assert [int(await redis_client.hget(key, "used")) for key in keys[:-1]] == [120] * 4
    assert 0 < await redis_client.ttl(keys[-1]) <= 120
    assert 0 < await redis_client.ttl(keys[0]) <= 90
    assert len(await redis_client.get(keys[-1])) <= 4096
    assert all(len(key.encode()) <= 512 for key in keys)


@pytest.mark.parametrize("unknown", [False, True])
async def test_output_denial_does_not_charge_legacy_or_parallel(redis_client, unknown):
    identity = uuid4().hex
    output = policy(identity)
    counter = limiter(redis_client)
    await counter.account_output(event(output, None if unknown else 100))
    with pytest.raises(OutputUsageUnknownError if unknown else RateLimitError):
        await counter.check_rate_limits_and_tier_fair_share_atomic(
            [RateLimitCheck("key_rpm", identity, 10)],
            [],
            parallel_checks=[ParallelLimitCheck("team", identity, 5)],
            output=output,
        )
    assert await redis_client.get(f"ratelimit:key_rpm:{identity}:{counter._window_id(60)}") is None
    assert await redis_client.exists(f"parallel:team:{identity}:leases") == 0


async def test_wrong_type_rejects_admission_and_finalization_before_any_write(redis_client):
    output = policy(uuid4().hex)
    keys = output.keys(environment="test")
    await redis_client.set(keys[2], "corrupt")
    counter = limiter(redis_client)
    with pytest.raises(ServiceUnavailableError):
        await counter.check_rate_limits_atomic([], output=output)
    completed = event(output, 10)
    with pytest.raises(ServiceUnavailableError):
        await counter.account_output(completed)
    assert (
        await redis_client.exists(keys[0], keys[1], keys[3], completed.keys(environment="test")[-1])
        == 0
    )


async def test_completion_charges_current_minute_and_duplicate_keeps_original_receipt(redis_client):
    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    admitted = (await counter.check_rate_limits_atomic([], output=output)).output_snapshot
    keys = output.keys(environment="test")
    for key in keys:
        await redis_client.hset(
            key, mapping={"window_id": admitted.window_id - 1, "used": 100, "unknown": 1}
        )
    assert (
        await counter.check_rate_limits_atomic([], output=output)
    ).output_snapshot.current_values == (0,) * 4
    completed = event(output, 20)
    current = await counter.account_output(completed)
    assert current.window_id == admitted.window_id
    assert current.current_values == (20,) * 4 and current.unknown == (False,) * 4
    receipt_key = completed.keys(environment="test")[-1]
    record = json.loads(await redis_client.get(receipt_key))
    record["result"][1] -= 1
    record["result"][2] -= 60
    await redis_client.set(receipt_key, json.dumps(record), keepttl=True)
    old = await counter.account_output(completed)
    assert old.window_id == current.window_id - 1
    assert [int(await redis_client.hget(key, "used")) for key in keys] == [20] * 4


async def test_unknown_marks_all_scopes_survives_known_charge_and_resets(redis_client):
    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    await counter.account_output(event(output, None))
    known = await counter.account_output(event(output, 10))
    assert known.current_values == (10,) * 4 and known.unknown == (True,) * 4
    with pytest.raises(OutputUsageUnknownError) as error:
        await counter.check_rate_limits_atomic([], output=output)
    assert 1 <= error.value.retry_after <= 60
    for key in output.keys(environment="test"):
        await redis_client.hincrby(key, "window_id", -1)
    result = (await counter.check_rate_limits_atomic([], output=output)).output_snapshot
    assert result.current_values == (0,) * 4 and result.unknown == (False,) * 4


async def test_saturation_is_global_and_limit_increase_retains_usage(redis_client):
    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    await counter.account_output(event(output, 150))
    higher = policy(output.scopes[0].entity_id, 200)
    assert (
        await counter.check_rate_limits_atomic([], output=higher)
    ).output_snapshot.current_values == (150,) * 4
    await counter.account_output(event(output, MAX_OUTPUT_TOKENS))
    final = await counter.account_output(event(output, 1))
    assert final.current_values == (MAX_OUTPUT_TOKENS,) * 4


@pytest.mark.parametrize("corruption", ["negative", "fractional", "future", "used", "unknown"])
async def test_corrupt_bucket_denies_admission_and_accounting(redis_client, corruption):
    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    first = await counter.account_output(event(output, 10))
    field, value = {
        "negative": ("window_id", -1),
        "fractional": ("window_id", first.window_id + 0.5),
        "future": ("window_id", first.window_id + 2),
        "used": ("used", MAX_OUTPUT_TOKENS + 1),
        "unknown": ("unknown", 2),
    }[corruption]
    await redis_client.hset(output.keys(environment="test")[0], field, value)
    with pytest.raises(ServiceUnavailableError):
        await counter.check_rate_limits_atomic([], output=output)
    completed = event(output, 20)
    with pytest.raises(ServiceUnavailableError):
        await counter.account_output(completed)
    assert await redis_client.exists(completed.keys(environment="test")[-1]) == 0
    assert [
        int(await redis_client.hget(key, "used")) for key in output.keys(environment="test")[1:]
    ] == [10] * 3


async def test_serialization_bound_rejects_before_legacy_writes(redis_client):
    identity = uuid4().hex
    output = policy(identity)
    counter = limiter(redis_client)
    with pytest.raises(ServiceUnavailableError):
        await counter.check_rate_limits_atomic(
            [RateLimitCheck("key_rpm", f"{identity}-{i}", 10) for i in range(115)], output=output
        )
    assert await redis_client.exists(*output.keys(environment="test")) == 0
    assert (
        await redis_client.get(f"ratelimit:key_rpm:{identity}-0:{counter._window_id(60)}") is None
    )


@pytest.mark.parametrize("corruption", ["fingerprint", "result", "oversized", "type"])
async def test_corrupt_receipt_never_replays_accounting(redis_client, corruption):
    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    completed = event(output, 10)
    await counter.account_output(completed)
    key = completed.keys(environment="test")[-1]
    record = json.loads(await redis_client.get(key))
    if corruption == "fingerprint":
        record["fingerprint"] = "wrong"
    if corruption == "result":
        record["result"][4] = -1
    await redis_client.set(key, "x" * 4097 if corruption == "oversized" else json.dumps(record))
    if corruption == "type":
        await redis_client.delete(key)
        await redis_client.hset(key, "field", 1)
    with pytest.raises(ServiceUnavailableError):
        await counter.account_output(completed)
    assert [int(await redis_client.hget(k, "used")) for k in output.keys(environment="test")] == [
        10
    ] * 4


async def test_noscript_recovery_and_lost_ack_replay(redis_client):
    from src.services.output_limit_lua import OUTPUT_ACCOUNTING_LUA
    from src.services.rate_limit_admission_lua import RATE_LIMIT_OUTPUT_LUA

    await OUTPUT_ACCOUNTING_LUA.load(redis_client)
    await RATE_LIMIT_OUTPUT_LUA.load(redis_client)
    await redis_client.script_flush()
    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    await counter.check_rate_limits_atomic([], output=output)
    completed = event(output, 10)
    # The first caller can lose this result; replay uses the same server-owned event.
    await counter.account_output(completed)
    await redis_client.script_flush()
    assert (await counter.account_output(completed)).current_values == (10,) * 4


async def test_lost_ack_preserves_answer_state_and_receipt_allows_safe_replay(redis_client):
    from types import SimpleNamespace
    from redis.exceptions import ConnectionError
    from src.services.output_token_context import OutputTokenContext
    from src.services.rate_limit_lease import RateLimitState

    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    initial = (await counter.check_rate_limits_atomic([], output=output)).output_snapshot
    lost = False

    async def lost_ack(*args):
        nonlocal lost
        result = await redis_client.evalsha(*args)
        if not lost:
            lost = True
            raise ConnectionError("Local test: lost acknowledgement")
        return result

    counter.redis = SimpleNamespace(
        eval=redis_client.eval, script_load=redis_client.script_load, evalsha=lost_ack
    )
    state = RateLimitState(output_tpm_limit=100)
    context = OutputTokenContext(counter, initial, state)
    context.mark_dispatched()
    await context.finish(10)
    assert context.closed and state.output_tpm_remaining is None
    counter.redis = redis_client
    replayed = await counter.account_output(OutputAccountingEvent(output, context.event_id, 10))
    assert replayed.current_values == (10,) * 4
    await context.finish(10)
    assert (
        await counter.check_rate_limits_atomic([], output=output)
    ).output_snapshot.current_values == (10,) * 4


async def test_unknown_parent_blocks_another_child_but_not_unrelated_scope(redis_client):
    parent = uuid4().hex
    output = policy(parent)
    counter = limiter(redis_client)
    await counter.account_output(event(output, None))
    sibling = OutputPolicy(
        (
            OutputScope("org_output_tpm", parent, 100),
            OutputScope("key_output_tpm", uuid4().hex, 100),
        )
    )
    with pytest.raises(OutputUsageUnknownError) as error:
        await counter.check_rate_limits_atomic([], output=sibling)
    assert error.value.param == "org_output_tpm"
    unrelated = OutputPolicy((OutputScope("key_output_tpm", uuid4().hex, 100),))
    assert (
        await counter.check_rate_limits_atomic([], output=unrelated)
    ).output_snapshot.current_values == (0,)


async def test_unreachable_redis_fails_closed_and_recovery_preserves_shared_usage(redis_client):
    from redis.exceptions import ConnectionError
    from types import SimpleNamespace

    output = policy(uuid4().hex)
    counter = limiter(redis_client)
    await counter.account_output(event(output, 20))

    async def disconnected(*args):
        raise ConnectionError("Local test: unavailable connection")

    counter.redis = SimpleNamespace(eval=disconnected)
    with pytest.raises(ServiceUnavailableError) as error:
        await counter.check_rate_limits_atomic([], output=output)
    assert error.value.code == "output_tpm_unavailable"
    assert error.value.affects_deployment_health is False
    counter.redis = redis_client
    assert (
        await counter.check_rate_limits_atomic([], output=output)
    ).output_snapshot.current_values == (20,) * 4
