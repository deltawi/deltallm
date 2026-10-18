"""The official OpenAI Python SDK against the real gateway routes.

Each test serves the in-process gateway on a loopback port, answers its provider
calls with a fake OpenAI upstream, and runs one check of tests/openai_sdk/sdk_client.py
in the isolated SDK environment named by DELTALLM_OPENAI_SDK_PYTHON. Batches use
real PostgreSQL storage and the real batch worker.
"""

import asyncio
import base64
import json
import os
import re
import struct
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
import pytest

from src.batch.repository import BatchRepository
from src.batch.storage import LocalBatchArtifactStorage
from src.batch.worker import BatchExecutorWorker, BatchWorkerConfig
from src.db.routing.callable_targets import CallableTargetBindingRecord
from src.router import FailoverManager, Router, build_deployment_registry
from src.router.runtime_generation import RoutingRuntimeGeneration, RoutingRuntimeRouterProvider
from src.services.access.callable_targets import build_callable_target_catalog
from tests import test_batch_db_integration as database_fixtures
from tests.router.selection.provider_fixtures import registry
from tests.test_stream_accounting_commit import loopback_gateway

pytestmark = pytest.mark.postgres

SDK_CLIENT = Path(__file__).parent / "openai_sdk" / "sdk_client.py"
UPSTREAM = "https://upstream.test/v1"
EMBEDDING = [0.25, -0.5, 0.75]
STRUCTURED = {"city": "Paris", "population": 2100000}
USAGE = {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
WAV = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + bytes(24) + b"data" + bytes(4)


def _completion(body: dict) -> dict:
    message: dict = {"role": "assistant", "content": "Hello from upstream"}
    finish = "stop"
    if body.get("tools"):
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "Paris"}'},
                }
            ],
        }
        finish = "tool_calls"
    elif body.get("response_format", {}).get("type") in {"json_object", "json_schema"}:
        message["content"] = json.dumps(STRUCTURED)
    return {
        "id": "chatcmpl-upstream",
        "object": "chat.completion",
        "created": 1700000000,
        "model": body["model"],
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": USAGE,
    }


def _completion_stream(body: dict) -> bytes:
    def chunk(delta: dict | None, finish: str | None = None, **extra) -> str:  # noqa: ANN003
        choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
        payload = {
            "id": "chatcmpl-upstream",
            "object": "chat.completion.chunk",
            "created": 1700000000,
            "model": body["model"],
            "choices": choices,
            **extra,
        }
        return "data: " + json.dumps(payload) + "\n\n"

    events = [chunk({"role": "assistant", "content": ""})]
    events += [chunk({"content": part}) for part in ("Hello ", "from ", "upstream")]
    events.append(chunk({}, "stop"))
    if body.get("stream_options", {}).get("include_usage"):
        events.append(chunk(None, usage=USAGE))
    events.append("data: [DONE]\n\n")
    return "".join(events).encode()


def _embeddings(body: dict) -> dict:
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    vector: object = EMBEDDING
    if body.get("encoding_format") == "base64":
        vector = base64.b64encode(struct.pack("<3f", *EMBEDDING)).decode()
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index, _ in enumerate(inputs)
        ],
        "model": body["model"],
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }


def _transcription(request: httpx.Request) -> httpx.Response:
    field = re.search(rb'name="response_format"\r\n\r\n(\w+)', request.content)
    fmt = field.group(1).decode() if field else "json"
    if fmt == "text":
        return httpx.Response(200, text="hello world\n")
    if fmt == "verbose_json":
        return httpx.Response(
            200,
            json={
                "task": "transcribe",
                "language": "english",
                "duration": 1.0,
                "text": "hello world",
                "segments": [],
            },
        )
    return httpx.Response(
        200, json={"text": "hello world", "usage": {"type": "duration", "seconds": 1}}
    )


async def fake_openai(request: httpx.Request) -> httpx.Response:
    path = request.url.path.removeprefix("/v1")
    if path == "/audio/transcriptions":
        return _transcription(request)
    body = json.loads(request.content)
    if path == "/chat/completions":
        if body.get("stream"):
            return httpx.Response(
                200,
                content=_completion_stream(body),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=_completion(body))
    if path == "/embeddings":
        return httpx.Response(200, json=_embeddings(body))
    if path == "/audio/speech":
        audio = WAV if body.get("response_format") == "wav" else b"ID3-fake-mp3-audio"
        return httpx.Response(200, content=audio)
    return httpx.Response(404, json={"error": {"message": f"unexpected {path}"}})


def _deployment(name: str, mode: str) -> dict:
    return {
        "deployment_id": name,
        "deltallm_params": {
            "model": f"openai/{name}",
            "api_key": "provider-key",
            "api_base": UPSTREAM,
        },
        "model_info": {
            "mode": mode,
            "chat_capabilities": {
                "tools": True,
                "json_object": True,
                "json_schema": True,
                "streaming": True,
            },
        },
    }


async def _install_models(app, upstream: httpx.AsyncClient) -> None:
    models = {
        "gpt-4o-mini": "chat",
        "text-embedding-3-small": "embedding",
        "tts-1": "audio_speech",
        "whisper-1": "audio_transcription",
    }
    state = app.state
    record = next(iter(state._test_repo.records.values()))
    record.models = list(models)
    # One check sends several requests, and batch polling more; the default
    # fixture limits are sized for single-request tests.
    record.rpm_limit, record.tpm_limit, record.max_parallel_requests = 10_000, 10_000_000, 20
    grants = state.callable_target_grant_service
    grants.repository.bindings = [
        CallableTargetBindingRecord(f"sdk-{name}", name, "organization", "org-default", True)
        for name in models
    ]
    await grants.reload()
    state.model_registry = {name: [_deployment(name, mode)] for name, mode in models.items()}
    state.http_client = state.control_http_client = upstream
    state.provider_error_mapper_registry = registry(upstream)
    state.openai_adapter = state.provider_error_mapper_registry.openai

    old = state.routing_runtime_generation_store.require_snapshot()
    deployments = build_deployment_registry(state.model_registry)
    router = Router(old.strategy, old.router.state, old.router_config, deployments)
    failover = FailoverManager(
        config=old.failover_config,
        candidate_planner=router,
        state_backend=old.router.state,
        cooldown_manager=old.cooldown_manager,
    )
    state.router, state.failover_manager = router, failover
    state.routing_runtime_generation_store.replace(
        RoutingRuntimeGeneration.create(
            revision=old.revision + 1,
            app_config=old.app_config,
            model_registry=state.model_registry,
            route_groups=[],
            callable_target_catalog=build_callable_target_catalog(state.model_registry),
            authorization_snapshot=grants.snapshot(),
            deployment_registry=router.deployment_registry,
            strategy=old.strategy,
            router_config=old.router_config,
            failover_config=old.failover_config,
            salt_key=old.salt_key,
            router=router,
            failover_manager=failover,
            cooldown_manager=old.cooldown_manager,
        )
    )


@pytest.fixture
def sdk_python():
    python = os.getenv("DELTALLM_OPENAI_SDK_PYTHON")
    if not python:
        if os.getenv("CI"):
            pytest.fail("DELTALLM_OPENAI_SDK_PYTHON is required in CI")
        pytest.skip("DELTALLM_OPENAI_SDK_PYTHON is required for official SDK tests")
    return python


@pytest.fixture
async def batch_db(monkeypatch):
    # Seed and clean the test key's team and organization like the batch fixtures' own.
    monkeypatch.setattr(
        database_fixtures,
        "_BATCH_TEST_TEAM_ORGANIZATIONS",
        (*database_fixtures._BATCH_TEST_TEAM_ORGANIZATIONS, ("team-default", "org-default")),
    )
    monkeypatch.setattr(
        database_fixtures,
        "_BATCH_TEST_ORGANIZATION_IDS",
        (*database_fixtures._BATCH_TEST_ORGANIZATION_IDS, "org-default"),
    )
    fixture = database_fixtures.batch_db.__wrapped__()
    try:
        yield await anext(fixture)
    finally:
        await fixture.aclose()


@asynccontextmanager
async def batch_worker(app, db, artifacts: Path, *, running: bool):
    repository = BatchRepository(db)
    storage = LocalBatchArtifactStorage(str(artifacts))
    service = database_fixtures._build_cutover_batch_service(repository=repository, storage=storage)
    for owner in (service, service.create_session_service):
        owner.callable_target_grant_service = app.state.callable_target_grant_service
        owner.model_group_resolver = RoutingRuntimeRouterProvider(
            app.state.routing_runtime_generation_store
        )
    app.state.batch_service, app.state.batch_repository = service, repository
    worker = BatchExecutorWorker(
        app=app,
        repository=repository,
        storage=storage,
        config=BatchWorkerConfig(worker_id="openai-sdk", worker_concurrency=2, max_attempts=1),
    )

    async def drive() -> None:
        while True:
            if not await worker.process_once():
                await asyncio.sleep(0.05)

    task = asyncio.create_task(drive()) if running else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def run_sdk_check(python: str, base_url: str, key: str, check: str) -> dict:
    process = await asyncio.create_subprocess_exec(
        python,
        "-I",
        str(SDK_CLIENT),
        "--base-url",
        base_url,
        "--check",
        check,
        env={"PATH": os.environ.get("PATH", ""), "DELTALLM_SDK_TEST_KEY": key, "NO_PROXY": "*"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 60)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, stderr.decode()
    return json.loads(stdout.decode().splitlines()[-1])


@pytest.mark.parametrize(
    "check",
    [
        "models",
        "chat",
        "chat_stream",
        "chat_tools",
        "chat_structured",
        "embeddings",
        "audio_speech",
        "audio_transcription",
        "errors",
    ],
)
async def test_official_sdk_parses_gateway_answers(test_app, sdk_python, check):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)) as upstream:
        await _install_models(test_app, upstream)
        async with loopback_gateway(test_app) as base_url:
            result = await run_sdk_check(sdk_python, base_url, test_app.state._test_key, check)
    assert result["check"] == check


@pytest.mark.parametrize("check", ["batch", "batch_cancel"])
async def test_official_sdk_runs_files_and_batches(test_app, sdk_python, batch_db, tmp_path, check):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_openai)) as upstream:
        await _install_models(test_app, upstream)
        async with (
            batch_worker(test_app, batch_db, tmp_path / "artifacts", running=check == "batch"),
            loopback_gateway(test_app) as base_url,
        ):
            result = await run_sdk_check(sdk_python, base_url, test_app.state._test_key, check)
    assert result["check"] == check
