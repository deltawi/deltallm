from __future__ import annotations

import httpx
import pytest


@pytest.mark.asyncio
async def test_multimodal_endpoints_emit_route_decision_headers(client, test_app):
    async def post(url, headers=None, json=None, timeout=None, files=None, data=None):  # noqa: ANN001, ANN201
        del headers, timeout, files, data
        request = httpx.Request("POST", url)
        if url.endswith("/images/generations"):
            return httpx.Response(
                200,
                json={"created": 1700000000, "data": [{"url": "https://example.com/image.png"}], "model": json["model"]},
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
    image_usage = await test_app.state.router_state_backend.get_usage(image_response.headers["x-deltallm-route-deployment"])
    assert image_usage == {"rpm": 1, "tpm": 0, "image_pm": 1}
    test_app.state.redis.store.clear()

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
    speech_usage = await test_app.state.router_state_backend.get_usage(speech_response.headers["x-deltallm-route-deployment"])
    assert speech_usage["rpm"] == 1
    assert speech_usage.get("tpm", 0) == 0
    assert speech_usage["char_pm"] > 0
    test_app.state.redis.store.clear()

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
    rerank_usage = await test_app.state.router_state_backend.get_usage(rerank_response.headers["x-deltallm-route-deployment"])
    assert rerank_usage == {"rpm": 1, "tpm": 0, "rerank_units_pm": 2}
