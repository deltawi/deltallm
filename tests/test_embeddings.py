from __future__ import annotations

import asyncio

import httpx
import pytest

from src.callbacks import CallbackManager, CustomLogger


class RecordingCallback(CustomLogger):
    def __init__(self):
        self.success = 0
        self.failure = 0

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        del kwargs, response_obj, start_time, end_time
        self.success += 1

    async def async_log_failure_event(self, kwargs, exception, start_time, end_time):
        del kwargs, exception, start_time, end_time
        self.failure += 1


@pytest.mark.asyncio
async def test_embeddings_runs_success_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="success")
    test_app.state.callback_manager = manager

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    response = await client.post("/v1/embeddings", headers=headers, json=body)
    assert response.status_code == 200
    assert response.headers.get("x-deltallm-route-group") == "text-embedding-3-small"
    assert response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert response.headers.get("x-deltallm-route-deployment")
    assert response.headers.get("x-deltallm-route-fallback-used") == "false"
    deployment_id = str(response.headers["x-deltallm-route-deployment"])
    await asyncio.sleep(0.05)
    assert recorder.success == 1
    usage = await test_app.state.router_state_backend.get_usage(deployment_id)
    assert usage == {"rpm": 1, "tpm": 2}


@pytest.mark.asyncio
async def test_embeddings_runs_failure_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="failure")
    test_app.state.callback_manager = manager

    async def failing_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(500, json={"error": "boom"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = failing_post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    response = await client.post("/v1/embeddings", headers=headers, json=body)
    assert response.status_code == 503
    await asyncio.sleep(0.05)
    assert recorder.failure == 1


@pytest.mark.asyncio
async def test_embeddings_upstream_rate_limit_returns_429(client, test_app):
    deployment = test_app.state.router.deployment_registry["text-embedding-3-small"][0]

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(
            429,
            json={"error": {"message": "provider quota exhausted"}},
            headers={"Retry-After": "11"},
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    response = await client.post("/v1/embeddings", headers=headers, json=body)

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "11"
    assert response.json()["error"]["type"] == "rate_limit_error"
    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 2
    assert health.get("last_error") == "provider quota exhausted"
    assert not await test_app.state.router_state_backend.is_cooled_down(deployment.deployment_id)


@pytest.mark.asyncio
async def test_embeddings_upstream_service_unavailable_updates_passive_health(client, test_app):
    deployment = test_app.state.router.deployment_registry["text-embedding-3-small"][0]

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(503, json={"error": {"message": "provider unavailable"}}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    response = await client.post("/v1/embeddings", headers=headers, json=body)

    assert response.status_code == 503
    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 2
    assert health.get("last_error") == "Upstream embedding call failed with status 503"
    assert not await test_app.state.router_state_backend.is_cooled_down(deployment.deployment_id)


@pytest.mark.asyncio
async def test_embeddings_upstream_service_unavailable_retries_when_route_policy_allows(client, test_app):
    deployment = test_app.state.router.deployment_registry["text-embedding-3-small"][0]
    attempts = {"count": 0}

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group
        request_context["route_policy"] = {"retry_max_attempts": 1}
        return deployment

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(
                503,
                json={"error": {"message": "provider unavailable"}},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
            request=httpx.Request("POST", url),
        )

    test_app.state.router.select_deployment = choose_primary
    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    response = await client.post("/v1/embeddings", headers=headers, json=body)

    assert response.status_code == 200
    assert attempts["count"] == 2
    assert response.headers.get("x-deltallm-route-deployment") == deployment.deployment_id
    assert response.headers.get("x-deltallm-route-fallback-used") == "false"
    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0


@pytest.mark.asyncio
async def test_embeddings_upstream_timeout_retries_when_route_policy_targets_timeout(client, test_app):
    deployment = test_app.state.router.deployment_registry["text-embedding-3-small"][0]
    attempts = {"count": 0}

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group
        request_context["route_policy"] = {
            "retry_max_attempts": 1,
            "retryable_error_classes": ["timeout"],
        }
        return deployment

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.ReadTimeout("upstream timed out", request=httpx.Request("POST", url))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
            request=httpx.Request("POST", url),
        )

    test_app.state.router.select_deployment = choose_primary
    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    response = await client.post("/v1/embeddings", headers=headers, json=body)

    assert response.status_code == 200
    assert attempts["count"] == 2
    assert response.headers.get("x-deltallm-route-deployment") == deployment.deployment_id
    assert response.headers.get("x-deltallm-route-fallback-used") == "false"
    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0


@pytest.mark.asyncio
async def test_embeddings_upstream_bad_request_does_not_mark_deployment_unhealthy(client, test_app):
    registry = test_app.state.router.deployment_registry["text-embedding-3-small"]
    deployment = registry[0]
    deployment.deltallm_params["api_key"] = "provider-key"
    registry.append(
        type(deployment)(
            deployment_id="text-embedding-3-small-fallback",
            model_name="text-embedding-3-small",
            deltallm_params={"model": "openai/text-embedding-3-small", "api_key": "provider-key-fallback"},
            model_info={},
        )
    )

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group, request_context
        return deployment

    test_app.state.router.select_deployment = choose_primary
    calls = {"count": 0}
    attempted_auths: list[str | None] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del timeout
        request = httpx.Request("POST", url)
        attempted_auths.append(headers.get("Authorization"))
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(400, json={"error": {"message": "bad embedding input"}}, request=request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": json["model"],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
            request=request,
        )

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    failure = await client.post("/v1/embeddings", headers=headers, json=body)
    assert failure.status_code == 400
    assert attempted_auths == ["Bearer provider-key"]

    health = await test_app.state.router_state_backend.get_health(deployment.deployment_id)
    assert health.get("healthy", "true") != "false"
    assert int(health.get("consecutive_failures", 0) or 0) == 0
    assert health.get("last_error") is None
    assert not await test_app.state.router_state_backend.is_cooled_down(deployment.deployment_id)

    success = await client.post("/v1/embeddings", headers=headers, json=body)
    assert success.status_code == 200
    assert attempted_auths == ["Bearer provider-key", "Bearer provider-key"]


@pytest.mark.asyncio
async def test_embeddings_use_custom_auth_headers_for_openai_compatible_provider(client, test_app):
    deployment = test_app.state.router.deployment_registry["text-embedding-3-small"][0]
    deployment.deltallm_params["provider"] = "vllm"
    deployment.deltallm_params["api_base"] = "https://vllm.example/v1"
    deployment.deltallm_params["api_key"] = "provider-key"
    deployment.deltallm_params["auth_header_name"] = "X-Provider-Auth"
    deployment.deltallm_params["auth_header_format"] = "Token {api_key}"

    captured: dict[str, object] = {}

    async def fake_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["json"] = dict(json)
        del timeout
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": json["model"],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = fake_post

    response = await client.post(
        "/v1/embeddings",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "text-embedding-3-small", "input": "hello"},
    )

    assert response.status_code == 200
    assert captured["url"] == "https://vllm.example/v1/embeddings"
    assert captured["headers"] == {
        "X-Provider-Auth": "Token provider-key",
        "Content-Type": "application/json",
    }
