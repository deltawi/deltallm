from __future__ import annotations

import httpx
import pytest

from src.db.named_credentials import NamedCredentialRecord


class _FakeNamedCredentialRepository:
    def __init__(self, records: list[NamedCredentialRecord]) -> None:
        self.records = {record.credential_id: record for record in records}

    async def get_by_id(self, credential_id: str) -> NamedCredentialRecord | None:
        return self.records.get(credential_id)


@pytest.mark.asyncio
async def test_elevenlabs_model_discovery_returns_tts_catalog_without_credentials(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "elevenlabs", "mode": "audio_speech"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []
    models = {item["id"]: item for item in payload["data"]}
    assert set(models) == {"eleven_v3", "eleven_multilingual_v2", "eleven_flash_v2_5"}
    assert models["eleven_flash_v2_5"]["known_metadata"]["input_cost_per_character"] == 0.00005


@pytest.mark.asyncio
async def test_elevenlabs_model_discovery_returns_stt_catalog_without_credentials(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "elevenlabs", "mode": "audio_transcription"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []
    models = {item["id"]: item for item in payload["data"]}
    assert set(models) == {"scribe_v2", "scribe_v1"}
    assert models["scribe_v2"]["known_metadata"]["input_cost_per_second"] == 0.0000611111111111


@pytest.mark.asyncio
async def test_elevenlabs_model_discovery_uses_xi_api_key_and_merges_live_catalog(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    captured: dict[str, object] = {}

    async def get(url: str, headers: dict[str, str] | None = None, timeout=None):  # noqa: ANN001, ANN201
        captured["url"] = url
        captured["headers"] = headers
        captured["timeout"] = timeout
        return httpx.Response(
            200,
            json=[
                {
                    "model_id": "eleven_multilingual_v2",
                    "name": "Eleven Multilingual v2",
                    "can_do_text_to_speech": True,
                },
                {
                    "model_id": "eleven_turbo_v2_5",
                    "name": "Eleven Turbo v2.5",
                    "can_do_text_to_speech": True,
                },
                {
                    "model_id": "eleven_music_v1",
                    "name": "Eleven Music v1",
                    "can_do_text_to_speech": False,
                },
            ],
        )

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "elevenlabs", "mode": "audio_speech", "api_key": "provider-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []
    assert captured["url"] == "https://api.elevenlabs.io/v1/models"
    assert captured["headers"] == {"xi-api-key": "provider-key"}
    assert "Authorization" not in captured["headers"]
    models = {item["id"]: item for item in payload["data"]}
    assert models["eleven_multilingual_v2"]["source"] == "catalog+provider_api"
    assert models["eleven_multilingual_v2"]["supported_modes"] == ["audio_speech"]
    assert models["eleven_multilingual_v2"]["known_metadata"]["input_cost_per_character"] == 0.0001
    assert models["eleven_turbo_v2_5"]["source"] == "provider_api"
    assert models["eleven_turbo_v2_5"]["known_metadata"] is None
    assert models["eleven_turbo_v2_5"]["supported_modes"] == ["audio_speech"]
    assert "eleven_music_v1" not in models


@pytest.mark.asyncio
async def test_elevenlabs_model_discovery_supports_named_credentials(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository(
        [
            NamedCredentialRecord(
                credential_id="cred-1",
                name="ElevenLabs prod",
                provider="elevenlabs",
                connection_config={
                    "api_key": "credential-key",
                    "api_base": "https://elevenlabs-proxy.example/v1",
                },
            )
        ]
    )
    captured: dict[str, object] = {}

    async def get(url: str, headers: dict[str, str] | None = None, timeout=None):  # noqa: ANN001, ANN201
        captured["url"] = url
        captured["headers"] = headers
        del timeout
        return httpx.Response(
            200,
            json={"models": [{"model_id": "eleven_v3", "name": "Eleven v3", "can_do_text_to_speech": True}]},
        )

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "elevenlabs", "mode": "audio_speech", "named_credential_id": "cred-1"},
    )

    assert response.status_code == 200
    assert response.json()["warnings"] == []
    assert captured["url"] == "https://elevenlabs-proxy.example/v1/models"
    assert captured["headers"] == {"xi-api-key": "credential-key"}


@pytest.mark.asyncio
async def test_elevenlabs_model_discovery_filters_live_results_to_supported_batch_stt(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def get(url: str, headers: dict[str, str] | None = None, timeout=None):  # noqa: ANN001, ANN201
        del url, headers, timeout
        return httpx.Response(
            200,
            json=[
                {
                    "model_id": "eleven_turbo_v2_5",
                    "name": "Eleven Turbo v2.5",
                    "can_do_text_to_speech": True,
                },
                {
                    "model_id": "scribe_v2",
                    "name": "Scribe v2",
                    "can_do_text_to_speech": False,
                },
                {
                    "model_id": "scribe_v2_realtime",
                    "name": "Scribe v2 Realtime",
                    "can_do_text_to_speech": False,
                },
                {
                    "model_id": "eleven_music_v1",
                    "name": "Eleven Music v1",
                },
            ],
        )

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "elevenlabs", "mode": "audio_transcription", "api_key": "provider-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []
    models = {item["id"]: item for item in payload["data"]}
    assert "eleven_turbo_v2_5" not in models
    assert "scribe_v2_realtime" not in models
    assert "eleven_music_v1" not in models
    assert models["scribe_v2"]["source"] == "catalog+provider_api"
    assert models["scribe_v2"]["supported_modes"] == ["audio_transcription"]
    assert models["scribe_v2"]["known_metadata"]["input_cost_per_second"] == 0.0000611111111111


@pytest.mark.asyncio
async def test_elevenlabs_model_discovery_keeps_catalog_on_live_failure(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def get(url: str, headers: dict[str, str] | None = None, timeout=None):  # noqa: ANN001, ANN201
        del url, headers, timeout
        return httpx.Response(503, json={"detail": "unavailable"})

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "elevenlabs", "mode": "audio_transcription", "api_key": "provider-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"]
    assert {item["id"] for item in payload["data"]} == {"scribe_v2", "scribe_v1"}


@pytest.mark.asyncio
async def test_elevenlabs_model_discovery_keeps_catalog_on_invalid_live_json(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def get(url: str, headers: dict[str, str] | None = None, timeout=None):  # noqa: ANN001, ANN201
        del url, headers, timeout
        return httpx.Response(200, content=b"not-json")

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "elevenlabs", "mode": "audio_speech", "api_key": "provider-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert any("invalid JSON" in warning for warning in payload["warnings"])
    assert {"eleven_v3", "eleven_multilingual_v2", "eleven_flash_v2_5"}.issubset(
        {item["id"] for item in payload["data"]}
    )
