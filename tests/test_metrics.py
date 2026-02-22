from __future__ import annotations

from src.cache import CacheKeyBuilder, InMemoryBackend, PrometheusCacheMetrics


async def test_metrics_endpoint_exposes_request_and_usage_metrics(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200

    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    text = metrics.text
    assert "litellm_requests_total" in text
    assert "litellm_input_tokens_total" in text
    assert "litellm_output_tokens_total" in text
    assert "litellm_spend_total" in text
    assert "litellm_request_total_latency_seconds" in text
    assert "litellm_llm_api_latency_seconds" in text


async def test_metrics_endpoint_exposes_cache_hit_and_miss(client, test_app):
    test_app.state.cache_backend = InMemoryBackend(max_size=32)
    test_app.state.cache_key_builder = CacheKeyBuilder(custom_salt="test-salt")
    test_app.state.cache_metrics = PrometheusCacheMetrics(cache_type="memory")

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "cache me"}]}

    r1 = await client.post("/v1/chat/completions", headers=headers, json=body)
    r2 = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.headers.get("x-litellm-cache-hit") == "true"

    metrics = await client.get("/metrics")
    text = metrics.text
    assert "litellm_cache_hit_total" in text
    assert "litellm_cache_miss_total" in text


async def test_metrics_endpoint_exposes_deployment_gauges(client):
    health = await client.get("/health/deployments")
    assert health.status_code == 200

    metrics = await client.get("/metrics")
    text = metrics.text
    assert "litellm_deployment_state" in text
    assert "litellm_deployment_active_requests" in text
    assert "litellm_deployment_cooldown" in text
