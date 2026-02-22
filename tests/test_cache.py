from __future__ import annotations

import pytest

from src.cache import CacheKeyBuilder, InMemoryBackend, NoopCacheMetrics, StreamingCacheHandler


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
    assert r1.headers["x-litellm-cache-hit"] == "false"
    assert r2.headers["x-litellm-cache-hit"] == "true"
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
    assert r1.headers["x-litellm-cache-hit"] == "false"
    assert r2.headers["x-litellm-cache-hit"] == "true"
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
    assert r1.headers["x-litellm-cache-hit"] == "false"
    assert r2.headers["x-litellm-cache-hit"] == "false"
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
    assert r1.headers["x-litellm-cache-hit"] == "false"
    assert r2.headers["x-litellm-cache-hit"] == "true"
    assert "data: [DONE]" in r2.text
    assert test_app.state.http_client.stream_calls == 1
