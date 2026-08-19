from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from src.cache import (
    CacheKeyBuilder,
    InMemoryBackend,
    NoopCacheMetrics,
    StreamWriteContext,
    StreamingCacheHandler,
)
from src.cache.backends.base import CacheBackend, CacheEntry
from src.callbacks import CallbackManager, CustomLogger
from src.db.repositories import KeyRecord
from src.router import build_deployment_registry


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_spend(self, **kwargs):
        self.events.append({"status": "success", **kwargs})

    async def log_request_failure(self, **kwargs):
        self.events.append({"status": "error", "cost": 0.0, **kwargs})


class _TierPricingService:
    def __init__(self, pricing: dict[str, float]) -> None:
        self.pricing = pricing
        self.mode = "enforce"

    def get_pricing_policy(self, organization_id: str, callable_key: str, *, mode: str = "sync"):
        if organization_id != "org-default" or callable_key != "gpt-4o-mini" or mode != "sync":
            return None
        return SimpleNamespace(
            mode="sync",
            pricing=self.pricing,
            source=SimpleNamespace(
                assignment_id="assignment-cache",
                tier_key="enterprise",
                tier_version_id="version-cache",
                tier_version_number=1,
                model_policy_id="policy-cache",
            ),
        )

    def get_snapshot(self):
        return SimpleNamespace(org_tier_keys={"org-default": ("enterprise",)})


class _DenyingTierAccessService:
    mode = "enforce"
    missing_service_mode = "fail_open"
    snapshot_stale = False

    def resolve_org_allowed_callable_keys(self, organization_id: str):
        assert organization_id == "org-default"
        return frozenset()


class _PromptRewriteCallback(CustomLogger):
    def __init__(self) -> None:
        self.calls = 0

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):  # noqa: ANN001
        del user_api_key_dict, cache
        assert call_type == "completion"
        self.calls += 1
        return {
            **data,
            "messages": [
                {"role": "system", "content": "resolved policy prompt"},
                *list(data.get("messages") or []),
            ],
        }


class _InvalidPreCallCallback(CustomLogger):
    def __init__(self, *, call_type: str) -> None:
        self.call_type = call_type
        self.calls = 0

    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):  # noqa: ANN001
        del user_api_key_dict, cache
        assert call_type == self.call_type
        self.calls += 1
        invalid_field = "messages" if call_type == "completion" else "input"
        return {**data, invalid_field: None}


def _enable_cache(test_app):
    backend = InMemoryBackend(max_size=100)
    test_app.state.cache_backend = backend
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="test-cache")
    test_app.state.cache_metrics = NoopCacheMetrics()
    test_app.state.streaming_cache_handler = StreamingCacheHandler(backend)


def _enable_stream_cache(
    test_app,
    *,
    backend: CacheBackend | None = None,
    max_buffer_bytes: int = 262_144,
    max_fragments: int = 2_048,
) -> CacheBackend:
    cache_backend = backend or InMemoryBackend(max_size=100)
    test_app.state.cache_backend = cache_backend
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="test-cache")
    test_app.state.cache_metrics = NoopCacheMetrics()
    test_app.state.streaming_cache_handler = StreamingCacheHandler(
        cache_backend,
        max_buffer_bytes=max_buffer_bytes,
        max_fragments=max_fragments,
    )
    return cache_backend


class _StreamContext:
    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self.status_code = status_code
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FailingCacheBackend(CacheBackend):
    def __init__(self) -> None:
        self.set_calls = 0

    async def get(self, key: str) -> CacheEntry | None:
        del key
        return None

    async def set(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        del key, entry, ttl
        self.set_calls += 1
        raise RuntimeError("backend unavailable")

    async def delete(self, key: str) -> None:
        del key

    async def clear(self) -> None:
        return None


def _refresh_runtime_registry(test_app) -> None:
    rebuilt = build_deployment_registry(test_app.state.model_registry)
    test_app.state.router.deployment_registry.clear()
    test_app.state.router.deployment_registry.update(rebuilt)
    test_app.state.router_health_handler.registry.clear()
    test_app.state.router_health_handler.registry.update(rebuilt)


def _configure_groq_openai_compatible_chat_model(test_app) -> None:
    test_app.state.model_registry["gpt-4o-mini"] = [
        {
            "deltallm_params": {
                "provider": "groq",
                "model": "openai/gpt-oss-20b",
                "api_key": "provider-key",
                "api_base": "https://api.groq.com/openai/v1",
            }
        }
    ]
    _refresh_runtime_registry(test_app)


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
async def test_chat_cache_prepares_transformed_payload_once_per_request(client, test_app):
    _enable_cache(test_app)
    callback = _PromptRewriteCallback()
    manager = CallbackManager()
    manager.register_callback(callback, callback_type="success")
    test_app.state.callback_manager = manager
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "cache transformed"}],
        "stream": False,
    }

    first = await client.post("/v1/chat/completions", headers=headers, json=body)
    second = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers["x-deltallm-cache-hit"] == "true"
    assert callback.calls == 2
    assert test_app.state.http_client.post_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "body", "call_type"),
    [
        (
            "/v1/chat/completions",
            {
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": "invalidate me"}],
                "stream": False,
            },
            "completion",
        ),
        (
            "/v1/embeddings",
            {"model": "text-embedding-3-small", "input": "invalidate me"},
            "embedding",
        ),
    ],
)
async def test_cache_does_not_repeat_hooks_after_transformed_validation_failure(
    client,
    test_app,
    endpoint,
    body,
    call_type,
):
    _enable_cache(test_app)
    callback = _InvalidPreCallCallback(call_type=call_type)
    manager = CallbackManager()
    manager.register_callback(callback, callback_type="success")
    test_app.state.callback_manager = manager
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}

    response = await client.post(endpoint, headers=headers, json=body)

    assert response.status_code == 400
    assert response.json()["error"] == {
        "message": "Request data was invalid after pre-call policy processing",
        "type": "invalid_request_error",
        "param": None,
        "code": None,
    }
    assert callback.calls == 1
    assert test_app.state.http_client.post_calls == 0


@pytest.mark.asyncio
async def test_chat_cache_hit_rechecks_current_tier_model_access(client, test_app):
    _enable_cache(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "access can change"}],
        "stream": False,
    }

    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert warm.status_code == 200
    assert test_app.state.http_client.post_calls == 1

    test_app.state.tier_policy_service = _DenyingTierAccessService()
    denied = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert denied.status_code == 403
    assert denied.json()["error"]["type"] == "permission_denied"
    assert "not allowed" in denied.json()["error"]["message"]
    assert "x-deltallm-cache-hit" not in denied.headers
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
async def test_embedding_cache_hit_uses_input_only_cached_pricing_without_live_route_selection(
    client, test_app
):
    _enable_cache(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.model_registry["text-embedding-3-small"][0]["model_info"] = {
        "mode": "embedding",
        "input_cost_per_token": 0.5,
    }
    _refresh_runtime_registry(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    warm = await client.post("/v1/embeddings", headers=headers, json=body)
    assert warm.status_code == 200

    async def fail_select_deployment(*args, **kwargs):  # noqa: ANN001, ANN201
        del args, kwargs
        raise AssertionError("cache hit pricing must not select a live route")

    test_app.state.router.select_deployment = fail_select_deployment
    hit = await client.post("/v1/embeddings", headers=headers, json=body)

    assert hit.status_code == 200
    assert hit.headers["x-deltallm-cache-hit"] == "true"
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["cache_hit"] is True
    assert recorder.events[-1]["cost"] == 1.0
    assert recorder.events[-1]["metadata"]["provider_cost_avoided"] == 1.0


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
async def test_streaming_cache_hit_bills_from_estimated_usage_without_provider_cost(
    client, test_app
):
    _enable_stream_cache(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.model_registry["gpt-4o-mini"][0]["model_info"] = {
        "mode": "chat",
        "input_cost_per_token": 1.0,
        "output_cost_per_token": 2.0,
    }
    _refresh_runtime_registry(test_app)

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, json, timeout
        return _StreamContext(
            lines=[
                'data: {"id":"chatcmpl-est-cache","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-est-cache","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"done"},"finish_reason":null}]}',
                "data: [DONE]",
            ]
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert warm.status_code == 200
    stored = next(iter(test_app.state.cache_backend._cache.values()))
    assert stored.response["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 1,
        "total_tokens": 4,
    }

    hit = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert hit.status_code == 200
    assert hit.headers["x-deltallm-cache-hit"] == "true"
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["cache_hit"] is True
    assert recorder.events[-1]["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 1,
        "total_tokens": 4,
        "prompt_tokens_cached": 3,
    }
    assert recorder.events[-1]["cost"] == 5.0
    assert recorder.events[-1]["metadata"]["provider_cost"] == 0.0
    assert recorder.events[-1]["metadata"]["provider_cost_avoided"] == 5.0
    assert recorder.events[-1]["metadata"]["cache_cost_basis"] == "avoided_provider_cost"


@pytest.mark.asyncio
async def test_streaming_cache_hit_forwards_usage_when_client_requested_usage(client, test_app):
    _enable_stream_cache(test_app)

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, json, timeout
        return _StreamContext(
            lines=[
                'data: {"id":"chatcmpl-cache-usage","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"done"},"finish_reason":null}]}',
                "data: [DONE]",
            ]
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    hit = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert warm.status_code == 200
    assert hit.status_code == 200
    assert hit.headers["x-deltallm-cache-hit"] == "true"
    assert '"usage"' in hit.text
    assert '"choices":[]' in hit.text
    assert '"total_tokens":4' in hit.text


@pytest.mark.asyncio
async def test_cache_hit_uses_persisted_provider_metadata_when_runtime_deployment_missing(
    client, test_app
):
    _enable_cache(test_app)
    _configure_groq_openai_compatible_chat_model(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "cache provider"}],
        "stream": False,
    }

    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert warm.status_code == 200

    cached_entry = next(iter(test_app.state.cache_backend._cache.values()))
    assert cached_entry.provider == "groq"
    assert cached_entry.deployment_model == "openai/gpt-oss-20b"
    cached_entry.deployment_id = None

    hit = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert hit.status_code == 200
    assert hit.headers["x-deltallm-cache-hit"] == "true"

    await asyncio.sleep(0.05)
    assert recorder.events[-1]["cache_hit"] is True
    assert recorder.events[-1]["metadata"]["provider"] == "groq"
    assert recorder.events[-1]["metadata"]["deployment_model"] == "openai/gpt-oss-20b"


@pytest.mark.asyncio
async def test_streaming_cache_hit_uses_persisted_provider_metadata_when_runtime_deployment_missing(
    client, test_app
):
    _enable_stream_cache(test_app)
    _configure_groq_openai_compatible_chat_model(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "stream provider"}],
        "stream": True,
    }

    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert warm.status_code == 200

    cached_entry = next(iter(test_app.state.cache_backend._cache.values()))
    assert cached_entry.provider == "groq"
    assert cached_entry.deployment_model == "openai/gpt-oss-20b"
    cached_entry.deployment_id = None

    hit = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert hit.status_code == 200
    assert hit.headers["x-deltallm-cache-hit"] == "true"

    await asyncio.sleep(0.05)
    assert recorder.events[-1]["cache_hit"] is True
    assert recorder.events[-1]["metadata"]["provider"] == "groq"
    assert recorder.events[-1]["metadata"]["deployment_model"] == "openai/gpt-oss-20b"


@pytest.mark.asyncio
async def test_streaming_cache_handler_skips_store_when_buffer_limit_exceeded():
    backend = InMemoryBackend(max_size=10)
    handler = StreamingCacheHandler(backend, max_buffer_bytes=5, max_fragments=10)
    stream_id = "stream-overflow"

    handler.start_stream(stream_id)
    handler.add_chunk_from_line(
        stream_id,
        'data: {"id":"chatcmpl-overflow","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"hello"},"finish_reason":null}]}',
    )
    handler.add_chunk_from_line(
        stream_id,
        'data: {"id":"chatcmpl-overflow","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"!"},"finish_reason":"stop"}]}',
    )
    await handler.finalize_and_store(
        stream_id,
        StreamWriteContext(cache_key="cache-key", ttl=60, model="gpt-4o-mini"),
    )

    assert len(backend._cache) == 0
    assert handler.disabled_streams_total == 1
    assert handler.active_stream_count == 0


@pytest.mark.asyncio
async def test_streaming_cache_skips_store_after_invalid_chunk_without_breaking_stream(
    client, test_app
):
    _enable_stream_cache(test_app)
    calls = {"count": 0}

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, json, timeout
        calls["count"] += 1
        return _StreamContext(
            lines=[
                'data: {"id":"chatcmpl-invalid","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-invalid"',
                "data: [DONE]",
            ]
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    first = await client.post("/v1/chat/completions", headers=headers, json=body)
    second = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert "data: [DONE]" in first.text
    assert "data: [DONE]" in second.text
    assert calls["count"] == 2
    assert len(test_app.state.cache_backend._cache) == 0
    assert test_app.state.streaming_cache_handler.disabled_streams_total == 2
    assert test_app.state.streaming_cache_handler.active_stream_count == 0


@pytest.mark.asyncio
async def test_streaming_cache_malformed_usage_does_not_break_stream(client, test_app):
    _enable_stream_cache(test_app)

    def stream(method: str, url: str, headers: dict[str, str], json: dict[str, Any], timeout: int):  # noqa: ANN001
        del method, url, headers, json, timeout
        return _StreamContext(
            lines=[
                'data: {"id":"chatcmpl-bad-usage","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"done"},"finish_reason":null}]}',
                'data: {"id":"chatcmpl-bad-usage","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":"bad","completion_tokens":0,"total_tokens":0}}',
                "data: [DONE]",
            ]
        )

    test_app.state.http_client.stream = stream
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert test_app.state.streaming_cache_handler.active_stream_count == 0
    stored = next(iter(test_app.state.cache_backend._cache.values()))
    assert stored.response["usage"] == {
        "prompt_tokens": 3,
        "completion_tokens": 1,
        "total_tokens": 4,
    }


@pytest.mark.asyncio
async def test_streaming_cache_write_failure_does_not_fail_stream_response(client, test_app):
    failing_backend = _FailingCacheBackend()
    _enable_stream_cache(test_app, backend=failing_backend)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    assert failing_backend.set_calls == 1
    assert test_app.state.streaming_cache_handler.write_failures_total == 1
    assert test_app.state.streaming_cache_handler.active_stream_count == 0


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
    token_hash2 = hashlib.sha256(
        f"{test_app.state.key_service.salt}:{key2}".encode("utf-8")
    ).hexdigest()
    # Create key2 under org-default so it has model access via callable-target grants
    test_app.state._test_repo.records[token_hash2] = KeyRecord(
        token=token_hash2,
        organization_id="org-default",
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


@pytest.mark.asyncio
async def test_cache_hit_uses_deployment_cache_hit_pricing(client, test_app):
    _enable_cache(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.model_registry["gpt-4o-mini"][0]["model_info"] = {
        "mode": "chat",
        "input_cost_per_token": 1.0,
        "output_cost_per_token": 2.0,
        "input_cost_per_token_cache_hit": 0.25,
    }
    _refresh_runtime_registry(test_app)
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
    assert len(recorder.events) == 2
    assert recorder.events[0]["cost"] == 3.0
    assert recorder.events[1]["cache_hit"] is True
    assert recorder.events[1]["usage"]["prompt_tokens_cached"] == 1
    assert recorder.events[1]["cost"] == 2.25
    assert recorder.events[1]["metadata"]["provider_cost_avoided"] == 3.0
    assert recorder.events[1]["metadata"]["provider_cost_avoided_basis"] == "provider_miss_pricing"


@pytest.mark.asyncio
async def test_cache_hit_uses_partial_cached_pricing_without_live_route_selection(client, test_app):
    _enable_cache(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.model_registry["gpt-4o-mini"][0]["model_info"] = {
        "mode": "chat",
        "input_cost_per_token": 1.0,
        "output_cost_per_token": 2.0,
        "input_cost_per_token_cache_hit": 0.25,
    }
    _refresh_runtime_registry(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "cache priced"}],
        "stream": False,
    }

    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert warm.status_code == 200

    cached_entry = next(iter(test_app.state.cache_backend._cache.values()))
    cached_entry.pricing = {"input_cost_per_token_cache_hit": 0.25}

    async def fail_select_deployment(*args, **kwargs):  # noqa: ANN001, ANN201
        del args, kwargs
        raise AssertionError("cache hit pricing must not select a live route")

    test_app.state.router.select_deployment = fail_select_deployment
    hit = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert hit.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["cache_hit"] is True
    assert recorder.events[-1]["usage"]["prompt_tokens_cached"] == 1
    assert recorder.events[-1]["cost"] == 0.2500006
    assert recorder.events[-1]["metadata"]["provider_cost"] == 0.0
    assert recorder.events[-1]["metadata"]["provider_cost_avoided"] == 0.2500006
    assert (
        recorder.events[-1]["metadata"]["provider_cost_avoided_basis"]
        == "cache_hit_pricing_fallback"
    )
    assert recorder.events[-1]["metadata"]["cache_cost_basis"] == "avoided_provider_cost"
    assert recorder.events[-1]["metadata"]["billing_status"] == "priced"
    assert recorder.events[-1]["metadata"]["effective_pricing_sources"] == [
        "default",
        "deployment",
    ]


@pytest.mark.asyncio
async def test_cache_hit_missing_pricing_metadata_does_not_select_live_route(client, test_app):
    _enable_cache(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.model_registry["gpt-4o-mini"][0]["model_info"] = {
        "mode": "chat",
        "input_cost_per_token": 1.0,
        "output_cost_per_token": 2.0,
        "input_cost_per_token_cache_hit": 0.25,
    }
    _refresh_runtime_registry(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "cache priced"}],
        "stream": False,
    }

    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert warm.status_code == 200

    cached_entry = next(iter(test_app.state.cache_backend._cache.values()))
    cached_entry.pricing = None
    cached_entry.deployment_id = None

    async def fail_select_deployment(*args, **kwargs):  # noqa: ANN001, ANN201
        del args, kwargs
        raise AssertionError("cache hit pricing must not select a live route")

    test_app.state.router.select_deployment = fail_select_deployment
    hit = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert hit.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.events[-1]["cache_hit"] is True
    assert recorder.events[-1]["cost"] < 0.001


@pytest.mark.asyncio
async def test_cache_hit_re_resolves_current_tier_pricing(client, test_app):
    _enable_cache(test_app)
    recorder = _SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    test_app.state.model_registry["gpt-4o-mini"][0]["model_info"] = {
        "mode": "chat",
        "input_cost_per_token": 1.0,
        "output_cost_per_token": 2.0,
        "input_cost_per_token_cache_hit": 0.25,
    }
    _refresh_runtime_registry(test_app)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "tier cache priced"}],
        "stream": False,
    }

    warm = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert warm.status_code == 200
    test_app.state.tier_policy_service = _TierPricingService(
        {
            "input_cost_per_token": 4.0,
            "output_cost_per_token": 10.0,
            "input_cost_per_token_cache_hit": 0.5,
        }
    )

    hit = await client.post("/v1/chat/completions", headers=headers, json=body)

    assert hit.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.events[0]["cost"] == 3.0
    assert recorder.events[-1]["cache_hit"] is True
    assert recorder.events[-1]["cost"] == 10.5
    assert recorder.events[-1]["metadata"]["pricing_source"] == "tier"
    assert recorder.events[-1]["metadata"]["customer_tier_key"] == "enterprise"
    assert recorder.events[-1]["metadata"]["tier_model_policy_id"] == "policy-cache"
    assert recorder.events[-1]["metadata"]["provider_cost"] == 0.0
    assert recorder.events[-1]["metadata"]["provider_cost_avoided"] == 3.0
    assert recorder.events[-1]["metadata"]["provider_cost_avoided_basis"] == "provider_miss_pricing"
    assert recorder.events[-1]["metadata"]["cache_cost_basis"] == "avoided_provider_cost"
