from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from src.db.repositories import KeyRecord
from src.guardrails.middleware import GuardrailMiddleware
from src.guardrails.registry import GuardrailRegistry
from src.main import create_app
from src.providers.openai import OpenAIAdapter
from src.router import (
    CooldownManager,
    FallbackConfig,
    FailoverManager,
    HealthEndpointHandler,
    PassiveHealthTracker,
    RedisStateBackend,
    Router,
    RouterConfig,
    RoutingStrategy,
    build_deployment_registry,
)
from src.services.key_service import KeyService
from src.services.limit_counter import LimitCounter


class NoopBudgetService:
    async def check_budgets(self, **kwargs):
        return None


class NoopSpendTrackingService:
    async def log_spend(self, **kwargs):
        return None


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int | str] = {}
        self.hash_store: dict[str, dict[str, str]] = {}
        self.zset_store: dict[str, list[tuple[int, str]]] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value

    async def set(self, key: str, value: str):
        self.store[key] = value

    async def incr(self, key: str):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return int(self.store[key])

    async def incrby(self, key: str, amount: int):
        self.store[key] = int(self.store.get(key, 0)) + amount
        return int(self.store[key])

    async def decr(self, key: str):
        self.store[key] = int(self.store.get(key, 0)) - 1
        return int(self.store[key])

    async def expire(self, key: str, ttl: int):
        return True

    async def pexpire(self, key: str, ttl: int):
        return True

    async def mget(self, keys):
        return [self.store.get(key) for key in keys]

    async def delete(self, *keys: str):
        for key in keys:
            self.store.pop(key, None)
            self.hash_store.pop(key, None)
            self.zset_store.pop(key, None)

    async def exists(self, key: str):
        return 1 if key in self.store else 0

    async def hset(self, key: str, mapping: dict[str, str]):
        self.hash_store.setdefault(key, {}).update(mapping)

    async def hgetall(self, key: str):
        return self.hash_store.get(key, {})

    async def zadd(self, key: str, mapping: dict[str, int]):
        items = self.zset_store.setdefault(key, [])
        for member, score in mapping.items():
            items.append((int(score), member))

    async def zremrangebyscore(self, key: str, min_score: int, max_score: int):
        values = self.zset_store.get(key, [])
        self.zset_store[key] = [(s, m) for s, m in values if not (int(min_score) <= s <= int(max_score))]

    async def zrangebyscore(self, key: str, min_score: int, max_score: str):
        del max_score
        values = self.zset_store.get(key, [])
        filtered = [member for score, member in values if score >= int(min_score)]
        return filtered

    def pipeline(self):
        return FakePipeline(self)

    async def ping(self):
        return True

    async def eval(self, script: str, numkeys: int, *args):
        del script
        keys = [str(item) for item in args[:numkeys]]
        argv = [str(item) for item in args[numkeys:]]
        n = len(keys)
        amounts = [int(argv[i]) for i in range(n)]
        limits = [int(argv[n + i]) for i in range(n)]

        for idx, key in enumerate(keys):
            current = int(self.store.get(key, 0))
            if current + amounts[idx] > limits[idx]:
                return [0, idx + 1]

        for idx, key in enumerate(keys):
            self.store[key] = int(self.store.get(key, 0)) + amounts[idx]

        return [1, 0]


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[tuple[str, tuple, dict]] = []

    def zadd(self, *args, **kwargs):
        self.ops.append(("zadd", args, kwargs))
        return self

    def zremrangebyscore(self, *args, **kwargs):
        self.ops.append(("zremrangebyscore", args, kwargs))
        return self

    def pexpire(self, *args, **kwargs):
        self.ops.append(("pexpire", args, kwargs))
        return self

    def incr(self, *args, **kwargs):
        self.ops.append(("incr", args, kwargs))
        return self

    def incrby(self, *args, **kwargs):
        self.ops.append(("incrby", args, kwargs))
        return self

    def expire(self, *args, **kwargs):
        self.ops.append(("expire", args, kwargs))
        return self

    def delete(self, *args, **kwargs):
        self.ops.append(("delete", args, kwargs))
        return self

    def hset(self, *args, **kwargs):
        self.ops.append(("hset", args, kwargs))
        return self

    async def execute(self):
        results = []
        for name, args, kwargs in self.ops:
            result = await getattr(self.redis, name)(*args, **kwargs)
            results.append(result)
        self.ops.clear()
        return results


class InMemoryKeyRepository:
    def __init__(self, records: dict[str, KeyRecord]) -> None:
        self.records = records
        self.calls = 0

    async def get_by_token(self, token_hash: str) -> KeyRecord | None:
        self.calls += 1
        return self.records.get(token_hash)


class MockHTTPStreamResponse:
    def __init__(self) -> None:
        self.status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}'
        yield 'data: [DONE]'


class MockHTTPClient:
    def __init__(self) -> None:
        self.post_calls = 0
        self.stream_calls = 0

    async def post(self, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):
        self.post_calls += 1
        if url.endswith("/chat/completions"):
            payload = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return httpx.Response(200, json=payload)

        if url.endswith("/embeddings"):
            payload = {
                "object": "list",
                "data": [{"object": "embedding", "embedding": [0.1, 0.2], "index": 0}],
                "model": json["model"],
                "usage": {"prompt_tokens": 2, "completion_tokens": 0, "total_tokens": 2},
            }
            return httpx.Response(200, json=payload)

        return httpx.Response(404, json={"error": "not found"})

    def stream(self, method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):
        self.stream_calls += 1
        return MockHTTPStreamResponse()


@pytest.fixture
async def test_app() -> FastAPI:
    app = create_app()
    redis = FakeRedis()
    salt = "test-salt"
    raw_key = "sk-test"
    token_hash = hashlib.sha256(f"{salt}:{raw_key}".encode("utf-8")).hexdigest()

    record = KeyRecord(
        token=token_hash,
        models=["gpt-4o-mini", "text-embedding-3-small"],
        rpm_limit=2,
        tpm_limit=10000,
        max_parallel_requests=5,
        expires=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    repo = InMemoryKeyRepository(records={token_hash: record})
    mock_http = MockHTTPClient()

    app.state.redis = redis
    app.state.settings = type("Settings", (), {"openai_base_url": "https://api.openai.com/v1"})()
    app.state.key_service = KeyService(repository=repo, redis_client=redis, salt=salt)
    app.state.limit_counter = LimitCounter(redis_client=redis)
    app.state.model_registry = {
        "gpt-4o-mini": [{"litellm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"}}],
        "text-embedding-3-small": [
            {"litellm_params": {"model": "openai/text-embedding-3-small", "api_key": "provider-key"}}
        ],
    }
    app.state.http_client = mock_http
    app.state.openai_adapter = OpenAIAdapter(mock_http)  # type: ignore[arg-type]
    app.state.app_config = type("Cfg", (), {"router_settings": type("RouterCfg", (), {"num_retries": 0})()})()

    state_backend = RedisStateBackend(redis)
    deployment_registry = build_deployment_registry(app.state.model_registry)
    router = Router(
        strategy=RoutingStrategy.SIMPLE_SHUFFLE,
        state_backend=state_backend,
        config=RouterConfig(),
        deployment_registry=deployment_registry,
    )
    cooldown_manager = CooldownManager(state_backend=state_backend)
    failover_manager = FailoverManager(
        config=FallbackConfig(),
        deployment_registry=deployment_registry,
        state_backend=state_backend,
        cooldown_manager=cooldown_manager,
    )

    app.state.router_state_backend = state_backend
    app.state.router = router
    app.state.cooldown_manager = cooldown_manager
    app.state.failover_manager = failover_manager
    app.state.passive_health_tracker = PassiveHealthTracker(state_backend=state_backend)
    app.state.router_health_handler = HealthEndpointHandler(
        deployment_registry=deployment_registry,
        state_backend=state_backend,
    )
    app.state.guardrail_registry = GuardrailRegistry()
    app.state.guardrail_middleware = GuardrailMiddleware(registry=app.state.guardrail_registry, cache_backend=redis)
    app.state.budget_service = NoopBudgetService()
    app.state.spend_tracking_service = NoopSpendTrackingService()

    app.state._test_key = raw_key
    app.state._test_repo = repo
    return app


@pytest.fixture
async def client(test_app: FastAPI):
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
