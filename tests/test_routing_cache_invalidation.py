from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import httpx
import pytest

from src.router import FallbackConfig
from src.services.callable_targets import build_callable_target_catalog
from tests.test_routing_cache_identity import _enable_cache, _group, _publish


def _request_body(endpoint, custom):
    body = {"model": "gpt-4o-mini"}
    if endpoint == "/v1/responses":
        body["input"] = "routing identity regression"
    elif endpoint == "/v1/completions":
        body["prompt"] = "routing identity regression"
    else:
        body["messages"] = [{"role": "user", "content": "routing identity regression"}]
    if custom:
        body["metadata"] = {"cache_key": "same caller key"}
    return body


def _label_provider_answers(test_app, monkeypatch, *, primary_error=None):
    original = test_app.state.http_client.post

    async def post(url, headers, json, timeout):
        if primary_error is not None and json["model"] == "gpt-4o-mini":
            status, code = primary_error
            test_app.state.http_client.post_calls += 1
            return httpx.Response(
                status,
                json={"error": {"message": "Primary unavailable", "code": code}},
                request=httpx.Request("POST", url),
            )
        response = await original(url, headers, json, timeout)
        result = response.json()
        result["choices"][0]["message"]["content"] = "answered by " + json["model"]
        return httpx.Response(200, json=result)

    monkeypatch.setattr(test_app.state.http_client, "post", post)


@pytest.mark.parametrize("endpoint", ["/v1/chat/completions", "/v1/responses", "/v1/completions"])
@pytest.mark.parametrize("custom", [False, True])
async def test_deployment_retarget_invalidates_cache_and_rollback_reuses_it(
    client, test_app, monkeypatch, endpoint, custom
):
    _enable_cache(test_app)
    _label_provider_answers(test_app, monkeypatch)
    body = _request_body(endpoint, custom)
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    fingerprints = []
    for model, expected in [
        ("gpt-4o-mini", "false"),
        ("gpt-4o-mini", "true"),
        ("new-answer-model", "false"),
        ("gpt-4o-mini", "true"),
    ]:
        registry = deepcopy(test_app.state.model_registry)
        registry["gpt-4o-mini"][0]["deltallm_params"]["model"] = "openai/" + model
        test_app.state.model_registry = registry
        generation = _publish(test_app, [_group()])
        fingerprints.append(generation.routing_fingerprints["gpt-4o-mini"])
        response = await client.post(endpoint, headers=headers, json=body)
        assert response.status_code == 200, response.text
        assert response.headers["x-deltallm-cache-hit"] == expected
        assert "answered by " + model in response.text
    assert fingerprints[0] == fingerprints[1] == fingerprints[3] != fingerprints[2]
    assert test_app.state.http_client.post_calls == 2


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize(
    ("kind", "status", "code"),
    [
        ("fallbacks", 503, "server_error"),
        ("context_window_fallbacks", 400, "context_length_exceeded"),
        ("content_policy_fallbacks", 400, "content_filter"),
    ],
)
async def test_reachable_fallback_member_change_invalidates_primary_cache(
    client, test_app, monkeypatch, custom, kind, status, code
):
    _enable_cache(test_app)
    _label_provider_answers(test_app, monkeypatch, primary_error=(status, code))
    fallback = "text-embedding-3-small"
    registry = deepcopy(test_app.state.model_registry)
    registry[fallback] = [
        {
            "deltallm_params": {"model": "openai/" + model, "api_key": "test-provider-key"},
            "model_info": {"mode": "chat"},
        }
        for model in ("old-fallback", "new-fallback")
    ]
    test_app.state.model_registry = registry
    store = test_app.state.routing_runtime_generation_store
    store.replace(
        replace(
            store.require_snapshot(),
            callable_target_catalog=build_callable_target_catalog(registry),
        )
    )
    config = FallbackConfig(**{kind: {"gpt-4o-mini": [fallback]}})
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    fingerprints = []
    for index, expected in [(0, "false"), (0, "true"), (1, "false"), (0, "true")]:
        group = {
            "key": fallback,
            "mode": "chat",
            "policy_semantics_version": 3,
            "members": [{"deployment_id": f"{fallback}-{index}"}],
        }
        generation = _publish(test_app, [_group(), group], failover_config=config)
        fingerprints.append(generation.routing_fingerprints["gpt-4o-mini"])
        response = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json=_request_body("/v1/chat/completions", custom),
        )
        assert response.status_code == 200, response.text
        assert response.headers["x-deltallm-cache-hit"] == expected
        assert "answered by " + ("old-fallback" if index == 0 else "new-fallback") in response.text
    assert fingerprints[0] == fingerprints[1] == fingerprints[3] != fingerprints[2]
    assert test_app.state.http_client.post_calls == 4


@pytest.mark.parametrize("stream", [False, True])
async def test_stream_and_embedding_deployment_changes_invalidate_cache(client, test_app, stream):
    _enable_cache(test_app)
    model = "gpt-4o-mini" if stream else "text-embedding-3-small"
    endpoint = "/v1/chat/completions" if stream else "/v1/embeddings"
    body = {"model": model, "metadata": {"cache_key": "same"}}
    if stream:
        body.update(stream=True, messages=[{"role": "user", "content": "stream identity"}])
    else:
        body["input"] = "embedding identity"
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    for upstream, expected in [(model, "false"), ("replacement", "false"), (model, "true")]:
        registry = deepcopy(test_app.state.model_registry)
        registry[model][0]["deltallm_params"]["model"] = "openai/" + upstream
        test_app.state.model_registry = registry
        _publish(test_app, [])
        response = await client.post(endpoint, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["x-deltallm-cache-hit"] == expected
        if stream:
            assert "[DONE]" in response.text
    assert (
        test_app.state.http_client.stream_calls if stream else test_app.state.http_client.post_calls
    ) == 2


async def test_admin_registry_rebuild_recomputes_deployment_cache_identity(
    client, test_app, monkeypatch
):
    from src.api.admin.endpoints.models import _rebuild_runtime_registry

    _enable_cache(test_app)
    _label_provider_answers(test_app, monkeypatch)
    _publish(test_app, [])
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = _request_body("/v1/chat/completions", False)
    first = await client.post("/v1/chat/completions", json=body, headers=headers)
    assert first.status_code == 200 and first.headers["x-deltallm-cache-hit"] == "false"
    registry = deepcopy(test_app.state.model_registry)
    registry["gpt-4o-mini"][0]["deltallm_params"]["model"] = "openai/new-admin-model"
    test_app.state.model_registry = registry
    _rebuild_runtime_registry(test_app)
    second = await client.post("/v1/chat/completions", json=body, headers=headers)
    assert second.status_code == 200 and second.headers["x-deltallm-cache-hit"] == "false"
    assert "answered by new-admin-model" in second.text
    assert test_app.state.http_client.post_calls == 2
