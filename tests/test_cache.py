from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from src.db.repositories import KeyRecord
from src.cache import CacheKeyBuilder, InMemoryBackend, NoopCacheMetrics, StreamingCacheHandler


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_spend(self, **kwargs):
        self.events.append(kwargs)


def _enable_cache(test_app):
    backend = InMemoryBackend(max_size=100)
    test_app.state.cache_backend = backend
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="test-cache")
    test_app.state.cache_metrics = NoopCacheMetrics()
    test_app.state.streaming_cache_handler = StreamingCacheHandler(backend)


@pytest.mark.asyncio
async def test_chat_cache_hit(client, test_app):
    _enable_cache(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "cache me"}],
        "stream": False,
    }

    r1 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r2 = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers["x-deltallm-cache-hit"] == "false"
    assert r2.headers["x-deltallm-cache-hit"] == "true"
    assert test_app.state.http_client.post_calls == 1


@pytest.mark.asyncio
async def test_embeddings_cache_hit(client, test_app):
    _enable_cache(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    r1 = await client.post("/v1/embeddings", headers=headers, json=body)
    r2 = await client.post("/v1/embeddings", headers=headers, json=body)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers["x-deltallm-cache-hit"] == "false"
    assert r2.headers["x-deltallm-cache-hit"] == "true"
    assert test_app.state.http_client.post_calls == 1


@pytest.mark.asyncio
async def test_cache_control_no_store(client, test_app):
    _enable_cache(test_app)
    headers = {
        "Authorization": f"Bearer {test_app.state._test_key}",
        "Cache-Control": "no-store",
    }
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "dont store"}],
        "stream": False,
    }

    r1 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r2 = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json=body,
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers["x-deltallm-cache-hit"] == "false"
    assert r2.headers["x-deltallm-cache-hit"] == "false"
    assert test_app.state.http_client.post_calls == 2


@pytest.mark.asyncio
async def test_streaming_cache_hit(client, test_app):
    _enable_cache(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "stream this"}],
        "stream": True,
    }

    r1 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r2 = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers["x-deltallm-cache-hit"] == "false"
    assert r2.headers["x-deltallm-cache-hit"] == "true"
    assert "data: [DONE]" in r2.text
    assert test_app.state.http_client.stream_calls == 1


@pytest.mark.asyncio
async def test_streaming_cache_miss_populates_cache_entry(client, test_app):
    _enable_cache(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "cache stream write"}],
        "stream": True,
    }

    backend = test_app.state.cache_backend
    assert len(backend._cache) == 0

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers["x-deltallm-cache-hit"] == "false"

    assert len(backend._cache) == 1
    stored = next(iter(backend._cache.values()))
    assert stored.response.get("object") == "chat.completion"


@pytest.mark.asyncio
async def test_cache_hit_still_requires_auth(client, test_app):
    _enable_cache(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "protected cache"}],
        "stream": False,
    }
    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert warm.status_code == 200

    unauthorized = await client.post("/v1/chat/completions", json=body)
    assert unauthorized.status_code == 401
    assert test_app.state.http_client.post_calls == 1


@pytest.mark.asyncio
async def test_cache_partitioned_by_api_key_scope(client, test_app):
    _enable_cache(test_app)
    headers1 = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "scope test"}],
        "stream": False,
    }

    key2 = "sk-test-2"
    token_hash2 = hashlib.sha256(f"{test_app.state.key_service.salt}:{key2}".encode("utf-8")).hexdigest()
    test_app.state._test_repo.records[token_hash2] = KeyRecord(
        token=token_hash2,
        models=["gpt-4o-mini"],
        rpm_limit=10,
        tpm_limit=10000,
        max_parallel_requests=5,
        expires=datetime.now(tz=UTC) + timedelta(hours=1),
    )
    headers2 = {"Authorization": f"Bearer {key2}"}

    r1 = await client.post("/v1/chat/completions", headers=headers1, json=body)
    r2 = await client.post("/v1/chat/completions", headers=headers1, json=body)
    r3 = await client.post("/v1/chat/completions", headers=headers2, json=body)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert r2.headers["x-deltallm-cache-hit"] == "true"
    assert r3.headers["x-deltallm-cache-hit"] == "false"
    assert test_app.state.http_client.post_calls == 2


@pytest.mark.asyncio
async def test_cache_hit_records_cache_pricing_signal(client, test_app):
    _enable_cache(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "cache priced"}],
        "stream": False,
    }

    r1 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r2 = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    await asyncio.sleep(0.05)
    assert any(bool(evt.get("cache_hit")) for evt in recorder.events)
