from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from src.billing.budget import BudgetExceeded
from src.services.limit_counter import LimitCounter, RateLimitCheck
from src.models.errors import RateLimitError, ServiceUnavailableError


class _AlwaysBudgetExceeded:
    async def check_budgets(self, **kwargs):
        del kwargs
        raise BudgetExceeded(entity_type="team", entity_id="t1", spend=20.0, max_budget=10.0)


class _SpendRecorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_spend(self, **kwargs):
        self.events.append({"status": "success", **kwargs})

    async def log_request_failure(self, **kwargs):
        error_type = kwargs.get("error_type")
        if error_type is None:
            exc = kwargs.get("exc")
            error_type = getattr(exc, "error_type", None) or (exc.__class__.__name__ if exc is not None else None)
        self.events.append({"status": "error", "cost": 0.0, "error_type": error_type, **kwargs})


class _RecordingAuditService:
    def __init__(self) -> None:
        self.events: list[tuple[object, list[object], bool]] = []

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        self.events.append((event, list(payloads or []), critical))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "kwargs"),
    [
        ("/v1/embeddings", {"json": {"model": "text-embedding-3-small", "input": "hello"}}),
        ("/v1/images/generations", {"json": {"model": "gpt-4o-mini", "prompt": "cat"}}),
        ("/v1/rerank", {"json": {"model": "gpt-4o-mini", "query": "q", "documents": ["a", "b"]}}),
        ("/v1/audio/speech", {"json": {"model": "gpt-4o-mini", "input": "hello", "voice": "alloy"}}),
    ],
)
async def test_budget_enforced_for_non_text_endpoints(client, test_app, path, kwargs):
    test_app.state.budget_service = _AlwaysBudgetExceeded()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post(path, headers=headers, **kwargs)
    assert response.status_code == 429
    payload = response.json()["error"]
    assert payload["type"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_budget_enforced_for_audio_transcriptions(client, test_app):
    test_app.state.budget_service = _AlwaysBudgetExceeded()
    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    files = {"file": ("audio.wav", b"abc", "audio/wav")}
    data = {"model": "gpt-4o-mini", "response_format": "json"}
    response = await client.post("/v1/audio/transcriptions", headers=headers, files=files, data=data)
    assert response.status_code == 429
    payload = response.json()["error"]
    assert payload["type"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_chat_fallback_uses_served_deployment_api_base_in_spend_log(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()

    registry = test_app.state.router.deployment_registry["gpt-4o-mini"]
    registry[0].deltallm_params["api_base"] = "https://primary.example/v1"
    registry[0].deltallm_params["api_key"] = "primary-key"
    fallback = type(registry[0])(
        deployment_id="gpt-4o-mini-fallback",
        model_name="gpt-4o-mini",
        deltallm_params={"model": "openai/gpt-4o-mini", "api_key": "fallback-key", "api_base": "https://fallback.example/v1"},
        model_info={},
    )
    registry.append(fallback)

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del request_context
        return test_app.state.router.deployment_registry[model_group][0]

    test_app.state.router.select_deployment = choose_primary

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del timeout
        if url.endswith("/chat/completions") and headers.get("Authorization") == "Bearer primary-key":
            return httpx.Response(503, json={"error": "primary down"}, request=httpx.Request("POST", url))
        if url.endswith("/chat/completions"):
            payload = {
                "id": "chatcmpl-fallback",
                "object": "chat.completion",
                "created": 1700000000,
                "model": json["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False}
    response = await client.post("/v1/chat/completions", headers=headers, json=body)
    assert response.status_code == 200

    await asyncio.sleep(0.05)
    assert test_app.state.spend_tracking_service.events
    last = test_app.state.spend_tracking_service.events[-1]
    assert (last.get("metadata") or {}).get("api_base") == "https://fallback.example/v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "body", "registry_key"),
    [
        (
            "/v1/chat/completions",
            {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False},
            "gpt-4o-mini",
        ),
        (
            "/v1/embeddings",
            {"model": "text-embedding-3-small", "input": "hello"},
            "text-embedding-3-small",
        ),
    ],
)
async def test_explicit_provider_keeps_spend_logging_intact(client, test_app, path, body, registry_key):
    test_app.state.spend_tracking_service = _SpendRecorder()

    deployment = test_app.state.router.deployment_registry[registry_key][0]
    deployment.deltallm_params["provider"] = "openrouter"
    deployment.deltallm_params["api_base"] = "https://openrouter.ai/api/v1"

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    response = await client.post(path, headers=headers, json=body)
    assert response.status_code == 200

    await asyncio.sleep(0.05)
    assert test_app.state.spend_tracking_service.events
    last = test_app.state.spend_tracking_service.events[-1]
    assert last.get("model") == body["model"]
    assert (last.get("metadata") or {}).get("api_base") == "https://openrouter.ai/api/v1"
    assert "cost" in last


@pytest.mark.asyncio
async def test_chat_failure_writes_error_request_log(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()

    async def failing_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(503, json={"error": "upstream unavailable"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = failing_post

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-chat-error"},
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False},
    )
    assert response.status_code == 503

    await asyncio.sleep(0.05)
    assert len(test_app.state.spend_tracking_service.events) == 1
    last = test_app.state.spend_tracking_service.events[-1]
    assert last["status"] == "error"
    assert last["call_type"] == "completion"
    assert last["cost"] == 0.0
    assert last["http_status_code"] == 503
    assert (last.get("metadata") or {}).get("route") == "/v1/chat/completions"


@pytest.mark.asyncio
async def test_embedding_failure_writes_error_request_log(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()

    async def failing_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(503, json={"error": "upstream unavailable"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = failing_post

    response = await client.post(
        "/v1/embeddings",
        headers={"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-embedding-error"},
        json={"model": "text-embedding-3-small", "input": "hello"},
    )
    assert response.status_code == 503

    await asyncio.sleep(0.05)
    assert len(test_app.state.spend_tracking_service.events) == 1
    last = test_app.state.spend_tracking_service.events[-1]
    assert last["status"] == "error"
    assert last["call_type"] == "embedding"
    assert last["cost"] == 0.0
    assert last["http_status_code"] == 503
    assert (last.get("metadata") or {}).get("route") == "/v1/embeddings"


@pytest.mark.asyncio
async def test_embedding_failover_failure_uses_last_attempted_deployment_metadata(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit

    registry = test_app.state.router.deployment_registry["text-embedding-3-small"]
    registry[0].deltallm_params["api_base"] = "https://primary.example/v1"
    registry[0].deltallm_params["api_key"] = "primary-key"
    fallback = type(registry[0])(
        deployment_id="text-embedding-3-small-fallback",
        model_name="text-embedding-3-small",
        deltallm_params={
            "model": "openai/text-embedding-3-small",
            "api_key": "fallback-key",
            "api_base": "https://fallback.example/v1",
        },
        model_info={},
    )
    registry.append(fallback)

    async def choose_primary(model_group, request_context):  # noqa: ANN001, ANN201
        del request_context
        return test_app.state.router.deployment_registry[model_group][0]

    test_app.state.router.select_deployment = choose_primary

    async def failing_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del json, timeout
        request = httpx.Request("POST", url)
        if headers.get("Authorization") == "Bearer primary-key":
            return httpx.Response(503, json={"error": "primary unavailable"}, request=request)
        return httpx.Response(502, json={"error": "fallback unavailable"}, request=request)

    test_app.state.http_client.post = failing_post

    response = await client.post(
        "/v1/embeddings",
        headers={"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-embedding-failover-failure"},
        json={"model": "text-embedding-3-small", "input": "hello"},
    )
    assert response.status_code == 503

    await asyncio.sleep(0.05)
    assert len(test_app.state.spend_tracking_service.events) == 1
    last = test_app.state.spend_tracking_service.events[-1]
    metadata = last.get("metadata") or {}
    assert last["status"] == "error"
    assert metadata.get("api_base") == "https://fallback.example/v1"
    assert metadata.get("provider") == "openai"
    assert metadata.get("deployment_model") == "openai/text-embedding-3-small"

    assert len(audit.events) == 1
    event, payloads, critical = audit.events[0]
    assert event.status == "error"
    assert event.metadata["api_base"] == "https://fallback.example/v1"
    assert event.metadata["deployment_model"] == "openai/text-embedding-3-small"
    assert critical is True
    assert payloads

    primary_health = await test_app.state.router_state_backend.get_health(registry[0].deployment_id)
    fallback_health = await test_app.state.router_state_backend.get_health(fallback.deployment_id)
    assert primary_health.get("last_error") == "Upstream embedding call failed with status 503"
    assert fallback_health.get("last_error") == "Upstream embedding call failed with status 502"


@pytest.mark.asyncio
async def test_denied_model_preflight_writes_request_log_and_audit_event(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit

    response = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}", "x-request-id": "req-chat-denied"},
        json={"model": "gpts-4o-mini", "messages": [{"role": "user", "content": "hello"}], "stream": False},
    )
    assert response.status_code == 403
    payload = response.json()["error"]
    assert payload["type"] == "permission_denied"

    await asyncio.sleep(0.05)
    assert len(test_app.state.spend_tracking_service.events) == 1
    last = test_app.state.spend_tracking_service.events[-1]
    assert last["status"] == "error"
    assert last["call_type"] == "completion"
    assert last["model"] == "gpts-4o-mini"
    assert last["http_status_code"] == 403
    assert last["error_type"] == "permission_denied"
    assert (last.get("metadata") or {}).get("failure_stage") == "preflight"

    assert len(audit.events) == 1
    event, payloads, critical = audit.events[0]
    assert event.action == "CHAT_COMPLETION_REQUEST"
    assert event.status == "error"
    assert event.resource_id == "gpts-4o-mini"
    assert event.error_type == "PermissionDeniedError"
    assert critical is True
    assert payloads == []


@pytest.mark.asyncio
async def test_invalid_json_writes_request_log_without_audit_event(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    audit = _RecordingAuditService()
    test_app.state.audit_service = audit

    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {test_app.state._test_key}",
            "Content-Type": "application/json",
            "x-request-id": "req-chat-json-invalid",
        },
        content='{"model":"gpt-4o-mini","messages":[{"role":"user","content":"hello"}',
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["type"] == "json_invalid"

    await asyncio.sleep(0.05)
    assert len(test_app.state.spend_tracking_service.events) == 1
    last = test_app.state.spend_tracking_service.events[-1]
    assert last["status"] == "error"
    assert last["call_type"] == "completion"
    assert last["model"] == "(unknown)"
    assert last["http_status_code"] == 422
    assert last["error_type"] == "request_validation_error"
    assert (last.get("metadata") or {}).get("failure_stage") == "request_validation"
    assert ((last.get("metadata") or {}).get("error") or {}).get("code") == "json_invalid"

    assert audit.events == []


@pytest.mark.asyncio
async def test_audio_transcription_spend_log_includes_billing_metadata(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_second": 0.25}

    async def post(url, headers=None, json=None, timeout=None, files=None, data=None):  # noqa: ANN001, ANN201
        del headers, json, timeout, files, data
        request = httpx.Request("POST", url)
        if url.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": "hello", "duration": 2.0}, request=request)
        return httpx.Response(404, json={"error": "not found"}, request=request)

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={"model": "gpt-4o-mini", "response_format": "json"},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )
    assert response.status_code == 200

    await asyncio.sleep(0.05)
    last = test_app.state.spend_tracking_service.events[-1]
    billing = (last.get("metadata") or {}).get("billing") or {}
    assert billing["billing_unit"] == "second"
    assert billing["cost"] == 0.5
    assert billing["usage_snapshot"]["duration_seconds"] == 2.0


@pytest.mark.asyncio
async def test_audio_transcription_forces_verbose_json_for_second_pricing_and_preserves_json_shape(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"input_cost_per_second": 0.111}
    deployment.deltallm_params["provider"] = "groq"
    deployment.deltallm_params["api_base"] = "https://api.groq.com/openai/v1"
    captured_formats: list[str] = []

    async def post(url, headers=None, json=None, timeout=None, files=None, data=None):  # noqa: ANN001, ANN201
        del headers, json, timeout, files
        request = httpx.Request("POST", url)
        if url.endswith("/audio/transcriptions"):
            captured_formats.append(str((data or {}).get("response_format")))
            return httpx.Response(
                200,
                json={
                    "text": "hello",
                    "duration": 2.0,
                    "segments": [{"start": 0.0, "end": 2.0, "text": "hello"}],
                },
                request=request,
            )
        return httpx.Response(404, json={"error": "not found"}, request=request)

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        data={"model": "gpt-4o-mini", "response_format": "json"},
        files={"file": ("sample.wav", b"RIFFDATA", "audio/wav")},
    )
    assert response.status_code == 200
    assert response.json() == {"text": "hello"}
    assert captured_formats == ["verbose_json"]

    await asyncio.sleep(0.05)
    last = test_app.state.spend_tracking_service.events[-1]
    billing = (last.get("metadata") or {}).get("billing") or {}
    assert billing["cost"] == 1.11
    assert billing["usage_snapshot"]["duration_seconds"] == 2.0
    assert billing["usage_snapshot"]["billable_duration_seconds"] == 10.0


@pytest.mark.asyncio
async def test_audio_speech_spend_log_marks_unpriced_without_matching_pricing_unit(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"output_cost_per_second": 0.25}

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, json, timeout
        if url.endswith("/audio/speech"):
            return httpx.Response(200, content=b"audio-bytes", request=httpx.Request("POST", url))
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world", "voice": "alloy"},
    )
    assert response.status_code == 200

    await asyncio.sleep(0.05)
    last = test_app.state.spend_tracking_service.events[-1]
    billing = (last.get("metadata") or {}).get("billing") or {}
    assert billing["cost"] == 0.0
    assert billing["unpriced_reason"] == "missing_tts_pricing_or_usage"


@pytest.mark.asyncio
async def test_audio_speech_forces_sse_for_token_pricing_and_preserves_audio_response(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "openai"
    deployment.model_info = {
        "input_cost_per_token": 1.0,
        "output_cost_per_audio_token": 0.5,
    }
    captured_stream_formats: list[str | None] = []
    audio_chunk = base64.b64encode(b"hello-audio").decode("ascii")

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        if url.endswith("/audio/speech"):
            captured_stream_formats.append(json.get("stream_format"))
            body = (
                "event: speech.audio.delta\n"
                f"data: {{\"type\":\"speech.audio.delta\",\"audio\":\"{audio_chunk}\"}}\n\n"
                "event: speech.audio.done\n"
                "data: {\"type\":\"speech.audio.done\",\"usage\":{\"input_tokens\":10,\"output_tokens\":4}}\n\n"
                "data: [DONE]\n\n"
            )
            return httpx.Response(
                200,
                content=body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world", "voice": "alloy", "response_format": "mp3"},
    )
    assert response.status_code == 200
    assert response.content == b"hello-audio"
    assert captured_stream_formats == ["sse"]

    await asyncio.sleep(0.05)
    last = test_app.state.spend_tracking_service.events[-1]
    billing = (last.get("metadata") or {}).get("billing") or {}
    assert billing["billing_unit"] == "token"
    assert billing["cost"] == 12.0
    assert billing["usage_snapshot"]["prompt_tokens"] == 10
    assert billing["usage_snapshot"]["output_audio_tokens"] == 4


@pytest.mark.asyncio
async def test_audio_speech_uses_gemini_native_endpoint_and_bills_from_usage_metadata(client, test_app):
    test_app.state.spend_tracking_service = _SpendRecorder()
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "gemini"
    deployment.deltallm_params["model"] = "gemini/gemini-2.5-flash-preview-tts"
    deployment.deltallm_params["api_base"] = "https://generativelanguage.googleapis.com/v1beta"
    deployment.deltallm_params["api_key"] = "gemini-key"
    deployment.model_info = {
        "input_cost_per_token": 1.0,
        "output_cost_per_audio_token": 0.5,
    }
    pcm_chunk = base64.b64encode(b"\x00\x00\x01\x00").decode("ascii")

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        if url.endswith("/models/gemini-2.5-flash-preview-tts:generateContent?key=gemini-key"):
            assert json["generationConfig"]["responseModalities"] == ["AUDIO"]
            assert json["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Kore"
            payload = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/L16;rate=24000",
                                        "data": pcm_chunk,
                                    }
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 10,
                    "candidatesTokenCount": 4,
                    "candidatesTokensDetails": [{"modality": "AUDIO", "tokenCount": 4}],
                },
            }
            return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world", "voice": "Kore", "response_format": "wav"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content.startswith(b"RIFF")

    await asyncio.sleep(0.05)
    last = test_app.state.spend_tracking_service.events[-1]
    billing = (last.get("metadata") or {}).get("billing") or {}
    assert billing["billing_unit"] == "token"
    assert billing["cost"] == 12.0
    assert billing["usage_snapshot"]["prompt_tokens"] == 10
    assert billing["usage_snapshot"]["output_audio_tokens"] == 4


@pytest.mark.asyncio
async def test_audio_speech_applies_default_params_for_gemini_native_requests(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "gemini"
    deployment.deltallm_params["model"] = "gemini/gemini-2.5-flash-preview-tts"
    deployment.deltallm_params["api_base"] = "https://generativelanguage.googleapis.com/v1beta"
    deployment.deltallm_params["api_key"] = "gemini-key"
    deployment.model_info = {"default_params": {"safetySettings": [{"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"}]}}
    pcm_chunk = base64.b64encode(b"\x00\x00\x01\x00").decode("ascii")

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, timeout
        if url.endswith("/models/gemini-2.5-flash-preview-tts:generateContent?key=gemini-key"):
            assert json["safetySettings"] == [{"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"}]
            payload = {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "audio/L16;rate=24000",
                                        "data": pcm_chunk,
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
            return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
        return httpx.Response(404, json={"error": "not found"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world", "voice": "Kore", "response_format": "wav"},
    )
    assert response.status_code == 200
    assert response.content.startswith(b"RIFF")


@pytest.mark.asyncio
async def test_audio_speech_rejects_unsupported_gemini_output_formats(client, test_app):
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.deltallm_params["provider"] = "gemini"
    deployment.deltallm_params["model"] = "gemini/gemini-2.5-flash-preview-tts"
    deployment.deltallm_params["api_base"] = "https://generativelanguage.googleapis.com/v1beta"
    deployment.deltallm_params["api_key"] = "gemini-key"

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello world", "voice": "Kore", "response_format": "mp3"},
    )
    assert response.status_code == 400
    assert "supports only 'wav' and 'pcm'" in response.text


@pytest.mark.asyncio
async def test_limit_counter_fail_open_uses_in_memory_fallback():
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_open")
    checks = [RateLimitCheck(scope="key_rpm", entity_id="k1", limit=1, amount=1)]
    await limiter.check_rate_limits_atomic(checks)
    with pytest.raises(RateLimitError):
        await limiter.check_rate_limits_atomic(checks)


@pytest.mark.asyncio
async def test_limit_counter_fail_closed_blocks_when_backend_unavailable():
    limiter = LimitCounter(redis_client=None, degraded_mode="fail_closed")
    checks = [RateLimitCheck(scope="key_rpm", entity_id="k1", limit=1, amount=1)]
    with pytest.raises(ServiceUnavailableError):
        await limiter.check_rate_limits_atomic(checks)
