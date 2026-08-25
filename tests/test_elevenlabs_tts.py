from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.billing.audio_usage import normalize_speech_usage


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_spend(self, **kwargs):  # noqa: ANN003, ANN201
        self.events.append({"status": "success", **kwargs})

    async def log_request_failure(self, **kwargs):  # noqa: ANN003, ANN201
        self.events.append({"status": "error", **kwargs})


def _configure_elevenlabs_deployment(test_app, *, default_params: dict | None = None) -> None:  # noqa: ANN001
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "elevenlabs"
    deployment.deltallm_params["model"] = "elevenlabs/eleven_multilingual_v2"
    deployment.deltallm_params["api_base"] = "https://api.elevenlabs.io/v1"
    deployment.deltallm_params["api_key"] = "elevenlabs-key"
    deployment.model_info = {
        "input_cost_per_character": 0.01,
        "default_params": dict(default_params or {}),
        "mode": "audio_speech",
    }


def test_normalize_speech_usage_prefers_provider_character_count() -> None:
    usage = normalize_speech_usage(
        request_text="short",
        response_payload={"input_characters": 123},
        provider="elevenlabs",
    )

    assert usage["input_characters"] == 123


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "api_base", "upstream_model"),
    [
        ("groq", "https://api.groq.com/openai/v1", "openai/playai-tts"),
        ("vllm", "https://vllm.example/v1", "vllm/custom-tts"),
    ],
)
async def test_audio_speech_openai_compatible_tts_providers_stay_on_audio_speech_path(
    client,
    test_app,
    provider: str,
    api_base: str,
    upstream_model: str,
):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = provider
    deployment.deltallm_params["model"] = upstream_model
    deployment.deltallm_params["api_base"] = api_base
    deployment.deltallm_params["api_key"] = "provider-key"
    deployment.model_info["mode"] = "audio_speech"
    captured: dict[str, object] = {}

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del timeout
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(200, content=b"audio-bytes", request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "input": "hello world",
            "voice": "alloy",
            "response_format": "mp3",
        },
    )

    assert response.status_code == 200
    assert response.content == b"audio-bytes"
    assert captured["url"] == f"{api_base}/audio/speech"
    assert captured["headers"] == {
        "Authorization": "Bearer provider-key",
        "Content-Type": "application/json",
    }
    assert "xi-api-key" not in captured["headers"]
    assert captured["json"]["model"] == upstream_model
    assert captured["json"]["input"] == "hello world"
    assert captured["json"]["voice"] == "alloy"
    assert captured["json"]["response_format"] == "mp3"
    assert "stream_format" not in captured["json"]


@pytest.mark.asyncio
async def test_audio_speech_uses_elevenlabs_native_endpoint_defaults_and_billing(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    _configure_elevenlabs_deployment(
        test_app,
        default_params={
            "voice_id": "voice-default",
            "output_format": "opus_48000_128",
            "voice_settings": {"stability": 0.4},
            "language_code": "en",
            "enable_logging": False,
            "optimize_streaming_latency": 2,
            "available_voices": ["should-not-forward"],
        },
    )
    captured: dict[str, object] = {}

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del timeout
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return httpx.Response(
            200,
            content=b"eleven-audio",
            headers={"x-character-count": "123"},
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world"},
    )

    assert response.status_code == 200
    assert response.content == b"eleven-audio"
    assert response.headers["content-type"].startswith("audio/opus")

    parsed_url = urlparse(str(captured["url"]))
    assert parsed_url.path == "/v1/text-to-speech/voice-default"
    query = parse_qs(parsed_url.query)
    assert query["output_format"] == ["opus_48000_128"]
    assert query["enable_logging"] == ["false"]
    assert query["optimize_streaming_latency"] == ["2"]

    headers = captured["headers"]
    assert headers["xi-api-key"] == "elevenlabs-key"
    assert "Authorization" not in headers

    upstream_json = captured["json"]
    assert upstream_json == {
        "text": "hello world",
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.4},
        "language_code": "en",
    }

    await asyncio.sleep(0.05)
    last_spend = test_app.state.spend_tracking_service.events[-1]
    billing = (last_spend.get("metadata") or {}).get("billing") or {}
    assert billing["billing_unit"] == "character"
    assert billing["cost"] == 1.23
    assert billing["usage_snapshot"]["input_characters"] == 123

    route_deployment = response.headers["x-deltallm-route-deployment"]
    route_usage = await test_app.state.router_state_backend.get_usage(route_deployment)
    assert route_usage["char_pm"] == 123


@pytest.mark.asyncio
async def test_audio_speech_elevenlabs_explicit_voice_format_and_speed_override(client, test_app):
    _configure_elevenlabs_deployment(
        test_app,
        default_params={
            "voice_id": "voice-default",
            "voice_settings": {"stability": 0.7, "similarity_boost": 0.9},
        },
    )
    captured: dict[str, object] = {}

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        captured["url"] = url
        captured["json"] = json
        return httpx.Response(200, content=b"wav-audio", request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "input": "hello world",
            "voice": "voice/request id",
            "response_format": "wav",
            "speed": 1.2,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")

    parsed_url = urlparse(str(captured["url"]))
    assert parsed_url.path == "/v1/text-to-speech/voice%2Frequest%20id"
    assert parse_qs(parsed_url.query)["output_format"] == ["wav_44100"]
    assert captured["json"]["voice_settings"] == {
        "stability": 0.7,
        "similarity_boost": 0.9,
        "speed": 1.2,
    }


@pytest.mark.asyncio
async def test_audio_speech_elevenlabs_requires_voice_or_deployment_default(client, test_app):
    _configure_elevenlabs_deployment(test_app)
    called = False

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        nonlocal called
        del url, headers, json, timeout
        called = True
        return httpx.Response(200, content=b"audio")

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world"},
    )

    assert response.status_code == 400
    assert "requires a request voice or model_info.default_params.voice_id" in response.text
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize("response_format", ["aac", "flac"])
async def test_audio_speech_elevenlabs_rejects_unsupported_openai_formats(
    client,
    test_app,
    response_format: str,
):
    _configure_elevenlabs_deployment(test_app, default_params={"voice_id": "voice-default"})
    called = False

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        nonlocal called
        del url, headers, json, timeout
        called = True
        return httpx.Response(200, content=b"audio")

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={
            "model": "gpt-4o-mini",
            "input": "hello world",
            "response_format": response_format,
        },
    )

    assert response.status_code == 400
    assert "supports 'mp3', 'opus', 'wav', and 'pcm'" in response.text
    assert called is False


@pytest.mark.asyncio
async def test_audio_speech_elevenlabs_sanitizes_upstream_errors(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    _configure_elevenlabs_deployment(test_app, default_params={"voice_id": "voice-default"})

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(
            422,
            json={"detail": {"message": "bad voice"}},
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Provider rejected request"
    assert "bad voice" not in response.text

    await asyncio.sleep(0.05)
    last_event = test_app.state.spend_tracking_service.events[-1]
    assert last_event["status"] == "error"
    assert last_event["call_type"] == "audio_speech"


@pytest.mark.asyncio
async def test_audio_speech_elevenlabs_rejects_empty_success_audio(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    _configure_elevenlabs_deployment(test_app, default_params={"voice_id": "voice-default"})

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(200, content=b"", request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Provider returned an invalid response"
    await asyncio.sleep(0.05)
    assert not [
        event
        for event in test_app.state.spend_tracking_service.events
        if event["status"] == "success"
    ]
