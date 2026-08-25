from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.billing.audio_usage import normalize_transcription_usage


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_spend(self, **kwargs):  # noqa: ANN003, ANN201
        self.events.append({"status": "success", **kwargs})

    async def log_request_failure(self, **kwargs):  # noqa: ANN003, ANN201
        self.events.append({"status": "error", **kwargs})


def _configure_elevenlabs_stt_deployment(
    test_app,  # noqa: ANN001
    *,
    default_params: dict | None = None,
    model_info: dict | None = None,
) -> None:
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "elevenlabs"
    deployment.deltallm_params["model"] = "elevenlabs/scribe_v2"
    deployment.deltallm_params["api_base"] = "https://api.elevenlabs.io/v1"
    deployment.deltallm_params["api_key"] = "elevenlabs-key"
    deployment.model_info = {
        "input_cost_per_second": 0.5,
        "default_params": dict(default_params or {}),
        **dict(model_info or {}),
        "mode": "audio_transcription",
    }


def test_normalize_transcription_usage_accepts_provider_billable_duration() -> None:
    usage = normalize_transcription_usage(
        response_payload={
            "_billing_duration_seconds": 3.0,
            "_billing_billable_duration_seconds": 6.0,
        },
        file_size_bytes=16,
        provider="elevenlabs",
    )

    assert usage["duration_seconds"] == 3.0
    assert usage["billable_duration_seconds"] == 6.0


def test_normalize_transcription_usage_ignores_public_billable_duration() -> None:
    usage = normalize_transcription_usage(
        response_payload={
            "duration": 3.0,
            "billable_duration_seconds": 999.0,
        },
        file_size_bytes=16,
        provider="elevenlabs",
    )

    assert usage["duration_seconds"] == 3.0
    assert "billable_duration_seconds" not in usage


@pytest.mark.asyncio
async def test_audio_transcription_elevenlabs_native_multipart_defaults_and_billing(
    client,
    test_app,
):
    test_app.state.spend_tracking_service = _SpendRecorder()
    _configure_elevenlabs_stt_deployment(
        test_app,
        default_params={
            "language_code": "en",
            "tag_audio_events": True,
            "num_speakers": 2,
            "timestamps_granularity": "word",
            "diarize": False,
            "diarization_threshold": 0.55,
            "file_format": "pcm_s16le_16",
            "seed": 7,
            "use_multi_channel": False,
            "no_verbatim": True,
            "enable_logging": False,
            "additional_formats": [{"format": "srt"}],
            "webhook": True,
            "entity_detection": True,
            "keyterms": ["should", "not", "forward"],
        },
    )
    captured: dict[str, object] = {}

    async def post(  # noqa: ANN201
        url: str,
        headers: dict[str, str],
        files: dict,
        data: dict,
        timeout: httpx.Timeout,
    ):
        del timeout
        captured["url"] = url
        captured["headers"] = headers
        captured["files"] = files
        captured["data"] = data
        return httpx.Response(
            200,
            json={
                "language_code": "en",
                "language_probability": 0.98,
                "text": "Hello world.",
                "words": [
                    {
                        "text": "Hello",
                        "start": 0.0,
                        "end": 0.4,
                        "type": "word",
                        "speaker_id": "speaker_0",
                    },
                    {
                        "text": "world.",
                        "start": 0.5,
                        "end": 1.2,
                        "type": "word",
                        "speaker_id": "speaker_0",
                    },
                ],
                "transcription_id": "tr_123",
            },
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={
            "model": "gpt-4o-mini",
            "language": "fr",
            "prompt": "do not forward",
            "response_format": "verbose_json",
            "temperature": "0.2",
        },
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Hello world."
    assert body["language"] == "en"
    assert body["language_probability"] == 0.98
    assert body["duration"] == 1.2
    assert body["segments"] == [
        {
            "start": 0.0,
            "end": 1.2,
            "text": "Hello world.",
            "speaker": "speaker_0",
        }
    ]
    assert "_billing_payload" not in body
    assert "_billing_duration_seconds" not in body

    parsed_url = urlparse(str(captured["url"]))
    assert parsed_url.path == "/v1/speech-to-text"
    assert parse_qs(parsed_url.query) == {"enable_logging": ["false"]}

    headers = captured["headers"]
    assert headers == {"xi-api-key": "elevenlabs-key"}

    upstream_files = captured["files"]
    assert upstream_files["file"] == ("sample.wav", b"RIFFDATA", "audio/wav")

    upstream_data = captured["data"]
    assert upstream_data == {
        "model_id": "scribe_v2",
        "language_code": "fr",
        "temperature": "0.2",
        "tag_audio_events": "true",
        "num_speakers": "2",
        "timestamps_granularity": "word",
        "diarize": "false",
        "diarization_threshold": "0.55",
        "file_format": "pcm_s16le_16",
        "seed": "7",
        "use_multi_channel": "false",
        "no_verbatim": "true",
    }
    for field in (
        "model",
        "language",
        "prompt",
        "response_format",
        "additional_formats",
        "webhook",
        "entity_detection",
        "keyterms",
    ):
        assert field not in upstream_data

    assert "x-deltallm-route-deployment" in response.headers

    await asyncio.sleep(0.05)
    last_spend = test_app.state.spend_tracking_service.events[-1]
    billing = (last_spend.get("metadata") or {}).get("billing") or {}
    assert billing["billing_unit"] == "second"
    assert billing["cost"] == 0.6
    assert billing["usage_snapshot"]["duration_seconds"] == 1.2

    route_deployment = response.headers["x-deltallm-route-deployment"]
    route_usage = await test_app.state.router_state_backend.get_usage(route_deployment)
    assert route_usage["audio_seconds_pm"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response_format", "expected_text"),
    [
        ("json", "Hello world."),
        ("text", "Hello world."),
        ("srt", "1\n00:00:00,000 --> 00:00:01,200\nHello world."),
        ("vtt", "WEBVTT\n\n00:00:00.000 --> 00:00:01.200\nHello world."),
    ],
)
async def test_audio_transcription_elevenlabs_response_formats(
    client,
    test_app,
    response_format: str,
    expected_text: str,
):
    _configure_elevenlabs_stt_deployment(test_app)

    async def post(
        url: str, headers: dict[str, str], files: dict, data: dict, timeout: httpx.Timeout
    ):  # noqa: ANN001, ANN201
        del headers, files, data, timeout
        return httpx.Response(
            200,
            json={
                "text": "Hello world.",
                "words": [
                    {"text": "Hello", "start": 0.0, "end": 0.4, "type": "word"},
                    {"text": "world.", "start": 0.5, "end": 1.2, "type": "word"},
                ],
            },
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={"model": "gpt-4o-mini", "response_format": response_format},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"text": expected_text}


@pytest.mark.asyncio
async def test_audio_transcription_elevenlabs_multichannel_uses_billable_channel_duration(
    client,
    test_app,
):
    test_app.state.spend_tracking_service = _SpendRecorder()
    _configure_elevenlabs_stt_deployment(
        test_app,
        default_params={"use_multi_channel": True},
        model_info={"input_cost_per_second": 0.2},
    )

    async def post(
        url: str, headers: dict[str, str], files: dict, data: dict, timeout: httpx.Timeout
    ):  # noqa: ANN001, ANN201
        del headers, files, data, timeout
        return httpx.Response(
            200,
            json={
                "duration_seconds": 3.0,
                "transcription_id": "tr_multi",
                "transcripts": [
                    {
                        "text": "Left channel.",
                        "language_code": "en",
                        "words": [{"text": "Left channel.", "start": 0.0, "end": 3.0}],
                    },
                    {
                        "text": "Right channel.",
                        "language_code": "en",
                        "words": [{"text": "Right channel.", "start": 0.0, "end": 3.0}],
                    },
                ],
            },
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={"model": "gpt-4o-mini", "response_format": "json"},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "Left channel.\nRight channel."}

    await asyncio.sleep(0.05)
    last_spend = test_app.state.spend_tracking_service.events[-1]
    billing = (last_spend.get("metadata") or {}).get("billing") or {}
    assert billing["cost"] == 1.2
    assert billing["usage_snapshot"]["duration_seconds"] == 3.0
    assert billing["usage_snapshot"]["billable_duration_seconds"] == 6.0

    route_deployment = response.headers["x-deltallm-route-deployment"]
    route_usage = await test_app.state.router_state_backend.get_usage(route_deployment)
    assert route_usage["audio_seconds_pm"] == 6


@pytest.mark.asyncio
async def test_audio_transcription_elevenlabs_sanitizes_upstream_errors(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    _configure_elevenlabs_stt_deployment(test_app)

    async def post(
        url: str, headers: dict[str, str], files: dict, data: dict, timeout: httpx.Timeout
    ):  # noqa: ANN001, ANN201
        del headers, files, data, timeout
        return httpx.Response(
            422,
            json={"detail": {"message": "bad audio"}},
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={"model": "gpt-4o-mini"},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Provider rejected request"
    assert "bad audio" not in response.text

    await asyncio.sleep(0.05)
    last_event = test_app.state.spend_tracking_service.events[-1]
    assert last_event["status"] == "error"
    assert last_event["call_type"] == "audio_transcription"


@pytest.mark.asyncio
async def test_audio_transcription_elevenlabs_invalid_schema_returns_sanitized_error(
    client,
    test_app,
):
    test_app.state.spend_tracking_service = _SpendRecorder()
    _configure_elevenlabs_stt_deployment(test_app)

    async def post(
        url: str, headers: dict[str, str], files: dict, data: dict, timeout: httpx.Timeout
    ):  # noqa: ANN001, ANN201
        del headers, files, data, timeout
        return httpx.Response(
            200,
            json={"secret": "sk-upstream"},
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={"model": "gpt-4o-mini"},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Provider returned an invalid response"
    assert "sk-upstream" not in response.text

    await asyncio.sleep(0.05)
    last_event = test_app.state.spend_tracking_service.events[-1]
    assert last_event["status"] == "error"
    assert last_event["call_type"] == "audio_transcription"
    assert last_event["http_status_code"] == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "api_base", "upstream_model"),
    [
        ("groq", "https://api.groq.com/openai/v1", "whisper-large-v3"),
        ("vllm", "https://vllm.example/v1", "vllm/custom-stt"),
    ],
)
async def test_audio_transcription_openai_compatible_providers_keep_existing_path(
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
    deployment.model_info["mode"] = "audio_transcription"
    captured: dict[str, object] = {}

    async def post(
        url: str, headers: dict[str, str], files: dict, data: dict, timeout: httpx.Timeout
    ):  # noqa: ANN001, ANN201
        del timeout
        captured["url"] = url
        captured["headers"] = headers
        captured["files"] = files
        captured["data"] = data
        return httpx.Response(200, json={"text": "hello"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={"model": "gpt-4o-mini", "language": "en", "response_format": "json"},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "hello"}
    assert captured["url"] == f"{api_base}/audio/transcriptions"
    assert captured["headers"] == {"Authorization": "Bearer provider-key"}
    assert "xi-api-key" not in captured["headers"]
    assert captured["files"]["file"] == ("sample.wav", b"RIFFDATA", "audio/wav")
    assert captured["data"]["model"] == upstream_model
    assert captured["data"]["language"] == "en"
    assert captured["data"]["response_format"] == "json"
    assert captured["data"]["temperature"] == "0.0"
