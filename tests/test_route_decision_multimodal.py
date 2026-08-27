from __future__ import annotations

from dataclasses import replace

import httpx
import pytest

from src.router.router import Deployment


@pytest.mark.asyncio
async def test_azure_image_inner_error_selects_content_policy_fallback(client, test_app):
    registry_store = test_app.state.router.deployment_registry
    primary = registry_store["gpt-4o-mini"][0]
    primary.deltallm_params.update(
        {
            "provider": "azure_openai",
            "model": "azure/image-primary",
            "api_key": "primary-key",
        }
    )
    primary.model_info["mode"] = "image_generation"
    fallback = Deployment(
        deployment_id="azure-image-content-fallback",
        model_name="azure-image-content-fallback",
        deltallm_params={
            "provider": "azure_openai",
            "model": "azure/image-fallback",
            "api_key": "fallback-key",
        },
        model_info={"mode": "image_generation"},
    )
    registry_store.replace(
        {
            **registry_store.snapshot(),
            "azure-image-content-fallback": [fallback],
        }
    )
    manager = test_app.state.failover_manager
    manager.config = replace(
        manager.config,
        content_policy_fallbacks={"gpt-4o-mini": ["azure-image-content-fallback"]},
    )
    attempts: list[str | None] = []

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del json, timeout
        attempts.append(headers.get("Authorization"))
        if headers.get("Authorization") == "Bearer primary-key":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "provider-owned sensitive detail sk-upstream",
                        "inner_error": {"code": "ResponsibleAIPolicyViolation"},
                    }
                },
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={"created": 1700000000, "data": [{"url": "https://example.com/image.png"}]},
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/images/generations",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "prompt": "sunset"},
    )

    assert response.status_code == 200
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    assert attempts == ["Bearer primary-key", "Bearer fallback-key"]
    assert "sk-upstream" not in response.text
    primary_health = await test_app.state.router_state_backend.get_health(primary.deployment_id)
    assert int(primary_health.get("consecutive_failures", 0) or 0) == 0
    assert primary_health.get("last_error") is None


@pytest.mark.asyncio
async def test_multimodal_endpoints_emit_route_decision_headers(client, test_app):
    async def post(url, headers=None, json=None, timeout=None, files=None, data=None):  # noqa: ANN001, ANN201
        del headers, timeout, files, data
        request = httpx.Request("POST", url)
        if url.endswith("/images/generations"):
            return httpx.Response(
                200,
                json={
                    "created": 1700000000,
                    "data": [{"url": "https://example.com/image.png"}],
                    "model": json["model"],
                },
                request=request,
            )
        if url.endswith("/audio/speech"):
            return httpx.Response(200, content=b"audio-bytes", request=request)
        if url.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hello", "duration": 1.0}, request=request)
        if url.endswith("/rerank"):
            return httpx.Response(
                200,
                json={"results": [{"index": 0, "relevance_score": 0.91}], "model": json["model"]},
                request=request,
            )
        return httpx.Response(404, json={"error": "not found"}, request=request)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]

    deployment.model_info["mode"] = "image_generation"
    image_response = await client.post(
        "/v1/images/generations",
        headers=headers,
        json={"model": "gpt-4o-mini", "prompt": "sunset"},
    )
    assert image_response.status_code == 200
    assert image_response.headers.get("x-deltallm-route-group") == "gpt-4o-mini"
    assert image_response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert image_response.headers.get("x-deltallm-route-deployment")
    assert image_response.headers.get("x-deltallm-route-fallback-used") == "false"
    image_usage = await test_app.state.router_state_backend.get_usage(
        image_response.headers["x-deltallm-route-deployment"]
    )
    assert image_usage == {"rpm": 1, "tpm": 0, "image_pm": 1}
    test_app.state.redis.store.clear()

    deployment.model_info["mode"] = "audio_speech"
    speech_response = await client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"model": "gpt-4o-mini", "input": "hello world", "voice": "alloy"},
    )
    assert speech_response.status_code == 200
    assert speech_response.headers.get("x-deltallm-route-group") == "gpt-4o-mini"
    assert speech_response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert speech_response.headers.get("x-deltallm-route-deployment")
    assert speech_response.headers.get("x-deltallm-route-fallback-used") == "false"
    speech_usage = await test_app.state.router_state_backend.get_usage(
        speech_response.headers["x-deltallm-route-deployment"]
    )
    assert speech_usage["rpm"] == 1
    assert speech_usage.get("tpm", 0) == 0
    assert speech_usage["char_pm"] > 0
    test_app.state.redis.store.clear()

    deployment.model_info["mode"] = "audio_transcription"
    transcription_response = await client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        data={"model": "gpt-4o-mini", "response_format": "json"},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )
    assert transcription_response.status_code == 200
    assert transcription_response.headers.get("x-deltallm-route-group") == "gpt-4o-mini"
    assert transcription_response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert transcription_response.headers.get("x-deltallm-route-deployment")
    assert transcription_response.headers.get("x-deltallm-route-fallback-used") == "false"
    transcription_usage = await test_app.state.router_state_backend.get_usage(
        transcription_response.headers["x-deltallm-route-deployment"]
    )
    assert transcription_usage["rpm"] == 1
    assert transcription_usage.get("tpm", 0) == 0
    assert transcription_usage["audio_seconds_pm"] > 0
    test_app.state.redis.store.clear()

    deployment.model_info["mode"] = "rerank"
    deployment.deltallm_params["provider"] = "vllm"
    rerank_response = await client.post(
        "/v1/rerank",
        headers=headers,
        json={"model": "gpt-4o-mini", "query": "hello", "documents": ["hello world", "bye world"]},
    )
    assert rerank_response.status_code == 200
    assert rerank_response.headers.get("x-deltallm-route-group") == "gpt-4o-mini"
    assert rerank_response.headers.get("x-deltallm-route-strategy") == "simple-shuffle"
    assert rerank_response.headers.get("x-deltallm-route-deployment")
    assert rerank_response.headers.get("x-deltallm-route-fallback-used") == "false"
    rerank_usage = await test_app.state.router_state_backend.get_usage(
        rerank_response.headers["x-deltallm-route-deployment"]
    )
    assert rerank_usage == {"rpm": 1, "tpm": 0, "rerank_units_pm": 2}


@pytest.mark.asyncio
async def test_multimodal_endpoints_use_custom_auth_headers_for_openai_compatible_providers(
    client, test_app
):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "vllm"
    deployment.deltallm_params["api_base"] = "https://vllm.example/api/v1"
    deployment.deltallm_params["api_key"] = "provider-key"
    deployment.deltallm_params["auth_header_name"] = "X-Provider-Auth"
    deployment.deltallm_params["auth_header_format"] = "Token {api_key}"

    captured: dict[str, dict[str, str]] = {}

    async def post(url, headers=None, json=None, timeout=None, files=None, data=None):  # noqa: ANN001, ANN201
        captured[url] = dict(headers or {})
        del timeout, files, data
        request = httpx.Request("POST", url)
        if url.endswith("/images/generations"):
            return httpx.Response(
                200,
                json={
                    "created": 1700000000,
                    "data": [{"url": "https://example.com/image.png"}],
                    "model": json["model"],
                },
                request=request,
            )
        if url.endswith("/audio/speech"):
            return httpx.Response(200, content=b"audio-bytes", request=request)
        if url.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hello", "duration": 1.0}, request=request)
        if url.endswith("/rerank"):
            return httpx.Response(
                200,
                json={"results": [{"index": 0, "relevance_score": 0.91}], "model": json["model"]},
                request=request,
            )
        return httpx.Response(404, json={"error": "not found"}, request=request)

    test_app.state.http_client.post = post
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}

    deployment.model_info["mode"] = "image_generation"
    image_response = await client.post(
        "/v1/images/generations",
        headers=headers,
        json={"model": "gpt-4o-mini", "prompt": "sunset"},
    )
    assert image_response.status_code == 200
    test_app.state.redis.store.clear()

    deployment.model_info["mode"] = "audio_speech"
    speech_response = await client.post(
        "/v1/audio/speech",
        headers=headers,
        json={"model": "gpt-4o-mini", "input": "hello world", "voice": "alloy"},
    )
    assert speech_response.status_code == 200
    test_app.state.redis.store.clear()

    deployment.model_info["mode"] = "audio_transcription"
    transcription_response = await client.post(
        "/v1/audio/transcriptions",
        headers=headers,
        data={"model": "gpt-4o-mini", "response_format": "json"},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )
    assert transcription_response.status_code == 200
    test_app.state.redis.store.clear()

    deployment.model_info["mode"] = "rerank"
    rerank_response = await client.post(
        "/v1/rerank",
        headers=headers,
        json={"model": "gpt-4o-mini", "query": "hello", "documents": ["hello world", "bye world"]},
    )
    assert rerank_response.status_code == 200

    assert captured["https://vllm.example/api/v1/images/generations"] == {
        "X-Provider-Auth": "Token provider-key",
        "Content-Type": "application/json",
    }
    assert captured["https://vllm.example/api/v1/audio/speech"] == {
        "X-Provider-Auth": "Token provider-key",
        "Content-Type": "application/json",
    }
    assert captured["https://vllm.example/api/v1/audio/transcriptions"] == {
        "X-Provider-Auth": "Token provider-key",
    }
    assert captured["https://vllm.example/api/v1/rerank"] == {
        "X-Provider-Auth": "Token provider-key",
        "Content-Type": "application/json",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "path", "request_kwargs", "invalid_payload", "valid_payload", "provider"),
    [
        (
            "image_generation",
            "/v1/images/generations",
            {"json": {"model": "gpt-4o-mini", "prompt": "sunset"}},
            {"secret": "sk-upstream"},
            {"created": 1700000000, "data": [{"url": "https://example.com/image.png"}]},
            "openai",
        ),
        (
            "rerank",
            "/v1/rerank",
            {
                "json": {
                    "model": "gpt-4o-mini",
                    "query": "hello",
                    "documents": ["hello world"],
                }
            },
            {"results": []},
            {"results": [{"index": 0, "relevance_score": 0.91}]},
            "vllm",
        ),
        (
            "rerank",
            "/v1/rerank",
            {
                "json": {
                    "model": "gpt-4o-mini",
                    "query": "hello",
                    "documents": ["hello world"],
                }
            },
            {"results": [{"index": 99, "relevance_score": 0.91}]},
            {"results": [{"index": 0, "relevance_score": 0.91}]},
            "vllm",
        ),
        (
            "audio_speech",
            "/v1/audio/speech",
            {
                "json": {
                    "model": "gpt-4o-mini",
                    "input": "hello world",
                    "voice": "alloy",
                }
            },
            {},
            {},
            "openai",
        ),
        (
            "audio_transcription",
            "/v1/audio/transcriptions",
            {
                "data": {"model": "gpt-4o-mini", "response_format": "json"},
                "files": {"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
            },
            {"secret": "sk-upstream"},
            {"text": "hello", "duration": 1.0},
            "openai",
        ),
    ],
)
async def test_multimodal_malformed_object_success_uses_general_fallback(
    client,
    test_app,
    mode: str,
    path: str,
    request_kwargs: dict,
    invalid_payload: dict,
    valid_payload: dict,
    provider: str,
):
    registry_store = test_app.state.router.deployment_registry
    primary = registry_store["gpt-4o-mini"][0]
    primary.deltallm_params.update({"api_key": "primary-key", "provider": provider})
    primary.model_info["mode"] = mode
    fallback = Deployment(
        deployment_id=f"{mode}-malformed-fallback",
        model_name="gpt-4o-mini",
        deltallm_params={
            "provider": provider,
            "model": f"{provider}/provider-model",
            "api_key": "fallback-key",
        },
        model_info={"mode": mode},
    )
    registry_store.replace(
        {
            **registry_store.snapshot(),
            "gpt-4o-mini": [primary, fallback],
        }
    )

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del model_group, request_context
        return primary

    attempts: list[str | None] = []

    async def post(url, headers=None, json=None, timeout=None, files=None, data=None):  # noqa: ANN001, ANN201
        del json, timeout, files, data
        attempts.append((headers or {}).get("Authorization"))
        request = httpx.Request("POST", url)
        if attempts[-1] == "Bearer primary-key":
            if mode == "audio_speech":
                return httpx.Response(200, content=b"", request=request)
            return httpx.Response(200, json=invalid_payload, request=request)
        if mode == "audio_speech":
            return httpx.Response(200, content=b"audio-bytes", request=request)
        return httpx.Response(200, json=valid_payload, request=request)

    test_app.state.router.select_deployment = choose_primary
    test_app.state.http_client.post = post
    response = await client.post(
        path,
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        **request_kwargs,
    )

    assert response.status_code == 200
    assert response.headers["x-deltallm-route-deployment"] == fallback.deployment_id
    assert response.headers["x-deltallm-route-fallback-used"] == "true"
    assert attempts == ["Bearer primary-key", "Bearer fallback-key"]
    assert "sk-upstream" not in response.text
    primary_health = await test_app.state.router_state_backend.get_health(primary.deployment_id)
    assert primary_health["last_error"] == "Provider returned an invalid response"
    primary_usage = await test_app.state.router_state_backend.get_usage(primary.deployment_id)
    fallback_usage = await test_app.state.router_state_backend.get_usage(fallback.deployment_id)
    assert int(primary_usage.get("rpm", 0) or 0) == 0
    assert fallback_usage["rpm"] == 1
