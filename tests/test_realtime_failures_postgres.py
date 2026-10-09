"""Production admission failures with actual shared leases and durable intents."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from src.billing.charges.operation_reservation import BillingOperationUnavailable
from src.realtime.routing import resolve_realtime_target
from src.router.candidates import AttemptCapacity
from src.services.limit_counter import _parallel_lease_key
from tests import test_realtime_runtime_postgres as fixtures
from tests.test_stream_accounting_commit import loopback_gateway

realtime_dependencies = fixtures.realtime_dependencies
pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@asynccontextmanager
async def connected(app, dependencies, monkeypatch):
    runtime, spend = await fixtures.bootstrap(app, dependencies, "realtime")
    observed = SimpleNamespace(events=[], received=asyncio.Event())

    async def provider(socket):
        observed.socket = socket
        await socket.send(
            json.dumps(
                {
                    "type": "session.created",
                    "session": {"type": "realtime", "model": "provider-model"},
                }
            )
        )
        async for message in socket:
            event = json.loads(message)
            if event["type"] == "session.update":
                await socket.send(
                    json.dumps(
                        {
                            "type": "session.updated",
                            "session": {**event["session"], "model": "provider-model"},
                        }
                    )
                )
            else:
                observed.events.append(event)
                observed.received.set()

    try:
        async with serve(provider, "127.0.0.1", 0) as upstream:
            port = upstream.sockets[0].getsockname()[1]
            monkeypatch.setattr(
                "src.realtime.routing.resolve_realtime_target",
                lambda *a, **kw: replace(
                    resolve_realtime_target(*a, **kw), url=f"ws://127.0.0.1:{port}/v1/realtime"
                ),
            )
            async with loopback_gateway(app) as base:
                async with connect(
                    base.replace("http:", "ws:") + "/v1/realtime?model=voice",
                    additional_headers={"Authorization": "Bearer " + dependencies[4]},
                    proxy=None,
                ) as client:
                    assert json.loads(await client.recv())["type"] == "session.created"
                    assert json.loads(await client.recv())["type"] == "session.updated"
                    yield client, runtime, observed
    finally:
        await runtime.close()
        await spend.shutdown()


@pytest.mark.parametrize("failure", ["lease_lost", "user_blocked", "budget_added"])
async def test_active_session_closes_and_preserves_intent_on_shared_policy_failure(
    test_app, realtime_dependencies, monkeypatch, failure
):
    db, redis, identity, token, _ = realtime_dependencies
    async with connected(test_app, realtime_dependencies, monkeypatch) as (
        client,
        runtime,
        observed,
    ):
        await client.send('{"type":"response.create"}')
        await asyncio.wait_for(observed.received.wait(), 5)
        if failure == "lease_lost":
            await redis.delete(_parallel_lease_key("realtime_org", identity))
        elif failure == "user_blocked":
            await db.execute_raw(
                "UPDATE deltallm_usertable SET blocked=true WHERE user_id=$1", identity
            )
        else:
            await db.execute_raw(
                "UPDATE deltallm_teamtable SET max_budget=100 WHERE team_id=$1", identity
            )
        terminal = json.loads(await asyncio.wait_for(client.recv(), 5))
        assert terminal["type"] == "error"
        await asyncio.wait_for(client.wait_closed(), 5)
        assert observed.events == [{"type": "response.create"}]
    rows = await db.query_raw(
        "SELECT state,pending_reason FROM deltallm_realtime_billing_intents "
        "WHERE snapshot->'attribution'->>'api_key'=$1",
        token,
    )
    assert rows == [{"state": "pending", "pending_reason": "terminal_usage_missing"}]
    assert runtime.active_sessions == 0


async def test_failed_durable_dispatch_never_reaches_provider(
    test_app, realtime_dependencies, monkeypatch
):
    async with connected(test_app, realtime_dependencies, monkeypatch) as (
        client,
        runtime,
        observed,
    ):

        async def unavailable(*args, **kwargs):
            raise BillingOperationUnavailable()

        monkeypatch.setattr(runtime.admission.billing, "dispatch", unavailable)
        await client.send('{"type":"response.create","event_id":"client_1"}')
        assert json.loads(await asyncio.wait_for(client.recv(), 5))["type"] == "error"
        await asyncio.wait_for(client.wait_closed(), 5)
        assert observed.events == []
    db, _, _, token, _ = realtime_dependencies
    assert (
        await db.query_raw(
            "SELECT operation_id FROM deltallm_realtime_billing_intents "
            "WHERE snapshot->'attribution'->>'api_key'=$1",
            token,
        )
        == []
    )


@pytest.mark.parametrize("recovering", [False, True])
async def test_transient_release_failure_closes_socket_and_retries_cleanup(
    test_app, realtime_dependencies, monkeypatch, recovering
):
    db, redis, identity, token, _ = realtime_dependencies
    async with connected(test_app, realtime_dependencies, monkeypatch) as (
        client,
        runtime,
        observed,
    ):
        generation = test_app.state.routing_runtime_generation_store.require_snapshot()
        backend = generation.router.state
        assert backend.degraded_mode == "fail_open"
        ref = generation.deployment_registry.physical_deployments[identity].health_ref
        if recovering:
            await generation.cooldown_manager.manual_cooldown(ref, 60)
            await redis.delete(backend.keyspace.cooldown(identity, ref.generation))
        original = backend._redis_call
        releases = 0

        async def transient_failure(method, *args, **kwargs):
            nonlocal releases
            if method == "eval" and "router_attempt_release_v2" in args[0]:
                releases += 1
                if releases == 1:
                    raise ConnectionError("one transient release outage")
            return await original(method, *args, **kwargs)

        monkeypatch.setattr(backend, "_redis_call", transient_failure)
        owners = tuple(runtime._sessions)
        await client.send('{"type":"response.create"}')
        await asyncio.wait_for(observed.received.wait(), 5)
        event = fixtures.response_event(1)
        if recovering:
            event["response"]["status"] = "cancelled"
        await observed.socket.send(json.dumps(event))
        error = json.loads(await asyncio.wait_for(client.recv(), 5))
        assert error["type"] == "error"
        assert "transient release outage" not in json.dumps(error)
        await asyncio.wait_for(client.wait_closed(), 5)
        await asyncio.wait_for(asyncio.gather(*owners), 5)
        assert releases == 2
        assert runtime.active_sessions == 0 and runtime.ready
        assert await backend.get_active_requests(identity) == 0
        next_attempt = await backend.acquire_attempt(ref, AttemptCapacity(require_shared=True))
        assert next_attempt.acquired and next_attempt.recovery == recovering
        await backend.release_attempt(next_attempt)
    rows = await db.query_raw(
        "SELECT state FROM deltallm_realtime_billing_intents "
        "WHERE snapshot->'attribution'->>'api_key'=$1",
        token,
    )
    assert len(rows) == 1 and rows[0]["state"] in {"accepted", "settled"}


async def test_request_quota_applies_to_each_turn_before_upstream_dispatch(
    test_app, realtime_dependencies, monkeypatch
):
    db, _, _, token, _ = realtime_dependencies
    await db.execute_raw("UPDATE deltallm_verificationtoken SET rpd_limit=1 WHERE token=$1", token)
    async with connected(test_app, realtime_dependencies, monkeypatch) as (client, _, observed):
        await client.send('{"type":"response.create"}')
        await asyncio.wait_for(observed.received.wait(), 5)
        await observed.socket.send(json.dumps(fixtures.response_event(1)))
        assert json.loads(await asyncio.wait_for(client.recv(), 5))["type"] == "response.done"
        await client.send('{"type":"response.create"}')
        assert json.loads(await asyncio.wait_for(client.recv(), 5))["type"] == "error"
        await asyncio.wait_for(client.wait_closed(), 5)
        assert observed.events == [{"type": "response.create"}]
    assert (
        len(
            await db.query_raw(
                "SELECT operation_id FROM deltallm_realtime_billing_intents WHERE snapshot->'attribution'->>'api_key'=$1",
                token,
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "change", ["quota", "credentials", "reconciliation", "route", "guardrails"]
)
async def test_generation_change_stops_the_pinned_session(
    test_app, realtime_dependencies, monkeypatch, change
):
    async with connected(test_app, realtime_dependencies, monkeypatch) as (client, _, _observed):
        store = test_app.state.routing_runtime_generation_store
        before = store.require_snapshot()
        if change == "reconciliation":
            after = replace(before, requires_reconciliation=True)
        elif change == "route":
            after = replace(before, routing_fingerprints={"voice": "changed-membership"})
        elif change == "guardrails":
            config = before.app_config.model_copy(deep=True)
            config.deltallm_settings.guardrails = [object()]
            after = replace(before, app_config=config)
        else:
            deployment_id = realtime_dependencies[2]
            prior = before.deployment_registry.physical_deployments[deployment_id]
            changed = replace(
                prior,
                **(
                    {"rpm_limit": 1}
                    if change == "quota"
                    else {"deltallm_params": {**prior.deltallm_params, "api_key": "rotated-secret"}}
                ),
            )
            registry = SimpleNamespace(physical_deployments={deployment_id: changed})
            after = replace(before, deployment_registry=registry)
        store.replace(after)
        terminal = json.loads(await asyncio.wait_for(client.recv(), 5))
        assert terminal["type"] == "error"
        await asyncio.wait_for(client.wait_closed(), 5)
