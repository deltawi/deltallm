from __future__ import annotations

import httpx
import pytest

from src.config_runtime.secrets import SecretResolver
from src.db.named_credentials import NamedCredentialRecord


class _FakeNamedCredentialRepository:
    def __init__(self, records: list[NamedCredentialRecord]) -> None:
        self.records = {record.credential_id: record for record in records}

    async def get_by_id(self, credential_id: str) -> NamedCredentialRecord | None:
        return self.records.get(credential_id)


@pytest.mark.asyncio
async def test_provider_model_discovery_returns_catalog_without_credentials(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "openai", "mode": "embedding"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []
    models = {item["id"]: item for item in payload["data"]}
    assert "text-embedding-3-small" in models
    assert "gpt-4o" not in models
    assert models["text-embedding-3-small"]["known_metadata"]["input_cost_per_token"] == 0.00000002


@pytest.mark.asyncio
async def test_provider_model_discovery_preserves_catalog_suggestions_for_azure_alias(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "azure", "mode": "chat"},
    )

    assert response.status_code == 200
    payload = response.json()
    models = {item["id"] for item in payload["data"]}
    assert {"gpt-5.4", "gpt-4o"}.issubset(models)


@pytest.mark.asyncio
async def test_provider_model_discovery_catalog_matches_current_provider_models(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    expectations = {
        "openai": {
            "mode": "chat",
            "expected_ids": {"gpt-5.4", "gpt-5.4-mini", "gpt-4.1", "gpt-4o"},
        },
        "anthropic": {
            "mode": "chat",
            "expected_ids": {"claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"},
        },
        "groq": {
            "mode": "chat",
            "expected_ids": {"openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.3-70b-versatile"},
        },
        "gemini": {
            "mode": "chat",
            "expected_ids": {"gemini-3.1-pro-preview", "gemini-3-flash-preview", "gemini-2.5-flash"},
        },
    }

    for provider, config in expectations.items():
        response = await client.post(
            "/ui/api/provider-models/discover",
            headers={"Authorization": "Bearer mk-test"},
            json={"provider": provider, "mode": config["mode"]},
        )

        assert response.status_code == 200
        payload = response.json()
        returned_ids = {item["id"] for item in payload["data"]}
        assert config["expected_ids"].issubset(returned_ids)


@pytest.mark.asyncio
async def test_provider_model_discovery_merges_catalog_and_live_results(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def get(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0):  # noqa: ANN201
        del headers, timeout
        assert url == "https://api.openai.com/v1/models"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-4o", "name": "gpt-4o"},
                    {"id": "gpt-5-custom", "name": "gpt-5-custom"},
                ]
            },
        )

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "openai", "mode": "chat", "api_key": "provider-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []
    models = {item["id"]: item for item in payload["data"]}
    assert models["gpt-4o"]["source"] == "catalog+provider_api"
    assert models["gpt-4o"]["known_metadata"]["max_tokens"] == 128000
    assert models["gpt-5-custom"]["source"] == "provider_api"
    assert models["gpt-5-custom"]["known_metadata"] is None


@pytest.mark.asyncio
async def test_provider_model_discovery_supports_named_credentials(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository(
        [
            NamedCredentialRecord(
                credential_id="cred-1",
                name="OpenAI prod",
                provider="openai",
                connection_config={"api_key": "provider-key", "api_base": "https://api.openai.com/v1"},
            )
        ]
    )

    async def get(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0):  # noqa: ANN201
        assert url == "https://api.openai.com/v1/models"
        assert headers == {"Authorization": "Bearer provider-key"}
        del timeout
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-4o", "name": "gpt-4o"}]},
        )

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "openai", "mode": "chat", "named_credential_id": "cred-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []
    models = {item["id"]: item for item in payload["data"]}
    assert models["gpt-4o"]["source"] == "catalog+provider_api"


@pytest.mark.asyncio
async def test_provider_model_discovery_resolves_named_credential_env_refs(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
):
    setattr(test_app.state.settings, "master_key", "mk-test")
    monkeypatch.setenv("OPENAI_PROVIDER_KEY", "provider-key")
    test_app.state.dynamic_config_manager = type("DynamicConfig", (), {"secret_resolver": SecretResolver()})()
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository(
        [
            NamedCredentialRecord(
                credential_id="cred-1",
                name="OpenAI prod",
                provider="openai",
                connection_config={"api_key": "os.environ/OPENAI_PROVIDER_KEY", "api_base": "https://api.openai.com/v1"},
            )
        ]
    )

    async def get(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0):  # noqa: ANN201
        assert url == "https://api.openai.com/v1/models"
        assert headers == {"Authorization": "Bearer provider-key"}
        del timeout
        return httpx.Response(200, json={"data": [{"id": "gpt-4o", "name": "gpt-4o"}]})

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "openai", "mode": "chat", "named_credential_id": "cred-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []


@pytest.mark.asyncio
async def test_provider_model_discovery_returns_catalog_with_warning_on_live_failure(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def get(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0):  # noqa: ANN201
        del headers, timeout
        return httpx.Response(503, json={"error": "unavailable"})

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "openai", "mode": "chat", "api_key": "provider-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"]
    assert any(item["id"] == "gpt-4o" for item in payload["data"])


@pytest.mark.asyncio
async def test_provider_model_discovery_normalizes_gemini_model_ids(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def get(url: str, headers: dict[str, str] | None = None, timeout: float = 10.0):  # noqa: ANN201
        del headers, timeout
        assert url == "https://generativelanguage.googleapis.com/v1beta/models?key=provider-key"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash"},
                ]
            },
        )

    test_app.state.http_client.get = get

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "gemini", "mode": "chat", "api_key": "provider-key"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["warnings"] == []
    assert payload["data"][0]["id"] == "gemini-2.5-flash"
    assert payload["data"][0]["label"] == "Gemini 2.5 Flash"


@pytest.mark.asyncio
async def test_provider_model_discovery_returns_current_gemini_embedding_catalog(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/provider-models/discover",
        headers={"Authorization": "Bearer mk-test"},
        json={"provider": "gemini", "mode": "embedding"},
    )

    assert response.status_code == 200
    payload = response.json()
    models = {item["id"]: item for item in payload["data"]}
    assert "gemini-embedding-001" in models
    assert models["gemini-embedding-001"]["known_metadata"]["output_vector_size"] == 3072
