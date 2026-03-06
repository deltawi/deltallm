from __future__ import annotations

from src.db.prompt_registry import PromptResolvedRecord
from src.cache import CacheKeyBuilder, InMemoryBackend, PrometheusCacheMetrics
from src.services.prompt_registry import PromptRegistryService


class _PromptMetricsRepository:
    async def resolve_prompt(self, *, template_key: str, label: str | None = None, version: int | None = None):  # noqa: ANN201
        del label, version
        return PromptResolvedRecord(
            prompt_template_id="tmpl-1",
            template_key=template_key,
            prompt_version_id="ver-1",
            version=1,
            status="published",
            label="production",
            template_body={"text": "Support user {{name}}"},
            variables_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            model_hints=None,
        )

    async def resolve_binding(self, *, scope_type: str, scope_id: str):  # noqa: ANN201
        del scope_type, scope_id
        return None

    async def create_render_log(self, **kwargs):  # noqa: ANN003, ANN201
        del kwargs
        return None


async def test_metrics_endpoint_exposes_request_and_usage_metrics(client, test_app):
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]}

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200

    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    text = metrics.text
    assert "deltallm_requests_total" in text
    assert "deltallm_input_tokens_total" in text
    assert "deltallm_output_tokens_total" in text
    assert "deltallm_spend_total" in text
    assert "deltallm_request_total_latency_seconds" in text
    assert "deltallm_llm_api_latency_seconds" in text


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
    assert r2.headers.get("x-deltallm-cache-hit") == "true"

    metrics = await client.get("/metrics")
    text = metrics.text
    assert "deltallm_cache_hit_total" in text
    assert "deltallm_cache_miss_total" in text


async def test_metrics_endpoint_exposes_deployment_gauges(client):
    health = await client.get("/health/deployments")
    assert health.status_code == 200

    metrics = await client.get("/metrics")
    text = metrics.text
    assert "deltallm_deployment_state" in text
    assert "deltallm_deployment_active_requests" in text
    assert "deltallm_deployment_cooldown" in text


async def test_metrics_endpoint_exposes_prompt_registry_metrics(client, test_app):
    test_app.state.prompt_registry_service = PromptRegistryService(repository=_PromptMetricsRepository())

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "metadata": {"prompt_ref": {"key": "support.prompt", "label": "production", "variables": {"name": "Mehdi"}}},
    }

    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200

    metrics = await client.get("/metrics")
    assert metrics.status_code == 200
    text = metrics.text
    assert "deltallm_prompt_cache_lookups_total" in text
    assert "deltallm_prompt_resolutions_total" in text
    assert "deltallm_prompt_resolution_latency_seconds" in text
