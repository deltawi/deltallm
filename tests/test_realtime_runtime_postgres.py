"""Normal Realtime bootstrap with real Redis, PostgreSQL, and a local wire peer."""

import asyncio
import hashlib
import json
import os
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from src.billing.spend import SpendTrackingService
from src.billing.spend_ingestion import SpendIngestionConfig, SpendIngestionService
from src.bootstrap.realtime import init_realtime_runtime
from src.config import AppConfig
from src.db.key_repository import KeyRepository
from src.db.spend_ingestion import SpendIngestionRepository
from src.realtime.config import RealtimeSettings
from src.router import (
    build_deployment_registry,
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
)
from src.router import CooldownManager, FailoverManager, FallbackConfig
from src.router.runtime_generation import RoutingRuntimeGeneration, RoutingRuntimeGenerationStore
from src.services.callable_targets import build_callable_target_catalog
from src.services.key_service import KeyService
from src.services.tier_policy_service import TierPolicyService
from tests.test_telemetry_ingestion_db_integration import _connect_prisma
from tests.test_stream_accounting_commit import loopback_gateway
from tests.test_selector_charge_db_integration import _scope_totals

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


@pytest.fixture
async def realtime_dependencies():
    url = os.getenv("DELTALLM_TEST_REDIS_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("Combined Realtime tests require Redis in the PostgreSQL lane")
        pytest.skip("DELTALLM_TEST_REDIS_URL is required")
    db = await _connect_prisma()
    redis = Redis.from_url(url, decode_responses=True)
    identity = "realtime-test-" + uuid4().hex
    raw_key = "sk-" + identity
    token = hashlib.sha256(f"test-salt:{raw_key}".encode()).hexdigest()
    await db.execute_raw(
        "WITH org AS (INSERT INTO deltallm_organizationtable(id,organization_id) VALUES ($1,$1) RETURNING organization_id), "
        "team AS (INSERT INTO deltallm_teamtable(team_id,organization_id,models) SELECT $1,organization_id,ARRAY[]::text[] FROM org RETURNING team_id), "
        "principal AS (INSERT INTO deltallm_usertable(user_id,team_id,models) SELECT $1,team_id,ARRAY[]::text[] FROM team RETURNING user_id,team_id) "
        "INSERT INTO deltallm_verificationtoken(id,token,user_id,team_id,models) SELECT $1,$2,user_id,team_id,ARRAY['voice']::text[] FROM principal",
        identity,
        token,
    )
    try:
        yield db, redis, identity, token, raw_key
    finally:
        async with db.tx() as tx:
            await tx.execute_raw(
                "DELETE FROM deltallm_spend_ingestion_outbox WHERE event_id IN (SELECT event_id FROM deltallm_realtime_billing_intents WHERE snapshot->'attribution'->>'api_key'=$1)",
                token,
            )
            await tx.execute_raw(
                "WITH removed AS (DELETE FROM deltallm_realtime_billing_intents WHERE snapshot->'attribution'->>'api_key'=$1 RETURNING state) UPDATE deltallm_telemetry_ingestion_capacity SET pending_count=pending_count-(SELECT COUNT(*) FROM removed WHERE state<>'settled') WHERE queue_name='realtime_billing'",
                token,
            )
            await tx.execute_raw("DELETE FROM deltallm_spendlog_events WHERE api_key=$1", token)
            await tx.execute_raw("DELETE FROM deltallm_teammodelspend WHERE team_id=$1", identity)
            await tx.execute_raw("DELETE FROM deltallm_verificationtoken WHERE token=$1", token)
            await tx.execute_raw("DELETE FROM deltallm_usertable WHERE user_id=$1", identity)
            await tx.execute_raw("DELETE FROM deltallm_teamtable WHERE team_id=$1", identity)
            await tx.execute_raw(
                "DELETE FROM deltallm_organizationtable WHERE organization_id=$1", identity
            )
        await SpendIngestionRepository(db).reconcile_capacity()
        keys = [key async for key in redis.scan_iter(match=f"*{identity}*")]
        keys.extend([key async for key in redis.scan_iter(match=f"*{token}*")])
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await db.disconnect()


async def bootstrap(app, dependencies, profile):
    db, redis, identity, _, _ = dependencies
    cfg = AppConfig()
    cfg.general_settings.callable_target_scope_policy_mode = "enforce"
    cfg.general_settings.realtime = RealtimeSettings(
        enabled=True, default_transcription_model="voice", health_seconds=1
    )
    state = app.state
    state.redis, state.app_config = redis, cfg
    state.settings = SimpleNamespace(realtime=RealtimeSettings())
    state.key_service = KeyService(
        repository=KeyRepository(db),
        redis_client=redis,
        salt="test-salt",
        lifecycle_authorizer=state.organization_lifecycle_authorizer,
    )
    from src.db.callable_targets import CallableTargetBindingRecord
    from src.services.callable_target_grants import CallableTargetGrantService
    from tests.conftest import InMemoryCallableTargetBindingRepository

    state.callable_target_grant_service = CallableTargetGrantService(
        repository=InMemoryCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    "voice-grant", "voice", "organization", identity, enabled=True
                )
            ]
        ),
        policy_repository=None,
    )
    await state.callable_target_grant_service.reload()
    state.tier_policy_service = TierPolicyService(repository=None, mode="disabled")
    info = {
        "mode": "realtime",
        "realtime_profile": profile,
        "realtime_usage_type": "duration" if profile == "transcription" else "tokens",
        "input_cost_per_token": "0.000001",
        "output_cost_per_token": "0.000002",
        "input_cost_per_audio_token": "0.000003",
        "output_cost_per_audio_token": "0.000004",
        "input_cost_per_second": "0.0001",
    }
    models = {
        "voice": [
            {
                "deployment_id": identity,
                "deltallm_params": {"model": "openai/provider-model", "api_key": "provider-secret"},
                "model_info": info,
            }
        ]
    }
    backend = RedisStateBackend(redis, degraded_mode="fail_closed")
    registry = build_deployment_registry(models)
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=backend,
        config=RouterConfig(),
        deployment_registry=registry,
    )
    cooldown = CooldownManager(state_backend=backend)
    failover = FailoverManager(
        config=FallbackConfig(),
        candidate_planner=router,
        state_backend=backend,
        cooldown_manager=cooldown,
    )
    state.routing_runtime_generation_store = RoutingRuntimeGenerationStore(
        RoutingRuntimeGeneration.create(
            authorization_snapshot=state.callable_target_grant_service.snapshot(),
            revision=1,
            app_config=cfg,
            model_registry=models,
            route_groups=[],
            callable_target_catalog=build_callable_target_catalog(models),
            deployment_registry=registry,
            strategy=router.strategy,
            router_config=router.config,
            failover_config=failover.config,
            salt_key="",
            router=router,
            failover_manager=failover,
            cooldown_manager=cooldown,
        )
    )
    spend = SpendIngestionService(
        db_client=db,
        writer=SpendTrackingService(db),
        config=SpendIngestionConfig(enabled=True, flush_interval_seconds=0.02),
    )
    await spend.start()
    state.spend_tracking_service = spend
    runtime = await init_realtime_runtime(state, cfg)
    return runtime, spend


def response_event(index):
    return {
        "type": "response.done",
        "response": {
            "id": f"resp_{index}",
            "status": "completed",
            "usage": {
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 3,
                "input_token_details": {"text_tokens": 1, "audio_tokens": 0},
                "output_token_details": {"text_tokens": 0, "audio_tokens": 2},
            },
        },
    }


@pytest.mark.parametrize("profile", ["realtime", "transcription"])
@pytest.mark.parametrize("client_kind", ["wire", "sdk"])
@pytest.mark.parametrize("recovering", [False, True])
async def test_bootstrapped_two_turn_session_reaches_canonical_ledger(
    test_app, realtime_dependencies, monkeypatch, profile, client_kind, recovering
):
    if client_kind == "sdk" and not os.getenv("DELTALLM_REALTIME_SDK_PYTHON"):
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires the isolated official SDK environment")
        pytest.skip("DELTALLM_REALTIME_SDK_PYTHON is required for official SDK tests")
    runtime, spend = await bootstrap(test_app, realtime_dependencies, profile)
    db, _, identity, token, raw_key = realtime_dependencies
    operations = []
    generation = test_app.state.routing_runtime_generation_store.require_snapshot()
    ref = generation.deployment_registry.physical_deployments[identity].health_ref
    backend = generation.router.state
    if recovering:
        await generation.cooldown_manager.manual_cooldown(ref, 60)
        await realtime_dependencies[1].delete(backend.keyspace.cooldown(identity, ref.generation))

    async def provider(socket):
        assert socket.request.headers["Authorization"] == "Bearer provider-secret"
        await socket.send(
            json.dumps(
                {"type": "session.created", "session": {"type": profile, "model": "provider-model"}}
            )
        )
        turn = 0
        async for message in socket:
            event = json.loads(message)
            if event["type"] == "session.update":
                session = {**event["session"], "model": "provider-model"}
                await socket.send(json.dumps({"type": "session.updated", "session": session}))
            if event["type"] in {"response.create", "input_audio_buffer.append"}:
                # Provider never sees billable input before its durable intent.
                rows = await db.query_raw(
                    "SELECT operation_id FROM deltallm_realtime_billing_intents WHERE snapshot->'attribution'->>'api_key'=$1 AND state='dispatched'",
                    token,
                )
                assert len(rows) == 1
                operations.append(rows[0]["operation_id"])
                if recovering and len(operations) == 2:
                    assert (await backend.get_health(ref))["recovery_required"] == "false"
            if event["type"] == (
                "input_audio_buffer.commit" if profile == "transcription" else "response.create"
            ):
                turn += 1
                terminal = (
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "item_id": f"item_{turn}",
                        "content_index": 0,
                        "transcript": "hello",
                        "usage": {"type": "duration", "seconds": 2.4},
                    }
                    if profile == "transcription"
                    else response_event(turn)
                )
                await socket.send(json.dumps(terminal))

    original = __import__(
        "src.realtime.routing", fromlist=["resolve_realtime_target"]
    ).resolve_realtime_target
    try:
        async with serve(provider, "127.0.0.1", 0) as upstream:
            port = upstream.sockets[0].getsockname()[1]
            monkeypatch.setattr(
                "src.realtime.routing.resolve_realtime_target",
                lambda *args, **kwargs: replace(
                    original(*args, **kwargs), url=f"ws://127.0.0.1:{port}/v1/realtime"
                ),
            )
            async with loopback_gateway(test_app) as base:
                await run_client(base, raw_key, profile, client_kind)
        async with asyncio.timeout(5):
            while True:
                rows = await db.query_raw(
                    "SELECT state FROM deltallm_realtime_billing_intents WHERE operation_id=ANY($1::text[])",
                    operations,
                )
                if len(rows) == 2 and all(row["state"] == "settled" for row in rows):
                    break
                await asyncio.sleep(0.02)
        assert len(set(operations)) == 2
        timings = await db.query_raw(
            "SELECT i.created_at::timestamptz(3) = s.start_time AT TIME ZONE 'UTC' AS matches, "
            "s.latency_ms FROM deltallm_realtime_billing_intents i "
            "JOIN deltallm_spendlog_events s ON s.id=i.event_id "
            "WHERE i.operation_id=ANY($1::text[])",
            operations,
        )
        assert len(timings) == 2
        assert all(row["matches"] and 0 <= row["latency_ms"] < 5000 for row in timings)
        total = Decimal("0.00048") if profile == "transcription" else Decimal("0.000018")
        key = await db.query_raw(
            "SELECT spend_exact::text AS spend FROM deltallm_verificationtoken WHERE token=$1",
            token,
        )
        assert Decimal(key[0]["spend"]) == total
        totals = await _scope_totals(db, identity)
        assert all(totals[scope] == total for scope in ("user", "team", "org", "model"))
        assert runtime.active_sessions == 0
    finally:
        await runtime.close()
        await spend.shutdown()


async def run_client(base, raw_key, profile, client_kind):
    if client_kind == "sdk":
        process = await asyncio.create_subprocess_exec(
            os.environ["DELTALLM_REALTIME_SDK_PYTHON"],
            str(Path(__file__).parent / "realtime/sdk_client.py"),
            "--base-url",
            base,
            "--profile",
            profile,
            env={**os.environ, "DELTALLM_SDK_TEST_KEY": raw_key},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), 15)
            assert process.returncode == 0, stderr.decode()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return
    query = "intent=transcription" if profile == "transcription" else "model=voice"
    async with connect(
        base.replace("http:", "ws:") + "/v1/realtime?" + query,
        additional_headers={"Authorization": "Bearer " + raw_key},
        proxy=None,
    ) as client:
        assert json.loads(await client.recv())["type"] == "session.created"
        assert json.loads(await client.recv())["type"] == "session.updated"
        for _ in range(2):
            if profile == "transcription":
                await client.send('{"type":"input_audio_buffer.append","audio":"AA=="}')
                await client.send('{"type":"input_audio_buffer.commit"}')
            else:
                await client.send('{"type":"response.create"}')
            terminal = json.loads(await asyncio.wait_for(client.recv(), 5))
            assert terminal["type"] in {
                "response.done",
                "conversation.item.input_audio_transcription.completed",
            }
