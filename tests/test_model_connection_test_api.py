from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from src.api.admin.endpoints import models as model_endpoints
from src.config_runtime.secrets import SecretResolver
from src.db.catalog.named_credentials import NamedCredentialRecord
from src.outbound.network_policy import OutboundNetworkPolicy
from src.outbound.user_egress import UserEgressGuard
from src.services.access.managed_asset_access import (
    AssetAccessPolicy,
    AssetKind,
    GovernanceSource,
    ManagedAsset,
)
from tests.test_named_credentials_api import (  # noqa: F401  (autouse fixture)
    _FakeNamedCredentialRepository,
    _IdentityService,
    _managed_asset_access_repository,
    _user_context,
)

URL = "/ui/api/provider-models/test-connection"
ADMIN = {"Authorization": "Bearer mk-test"}


def _models_server(
    calls: list[httpx.Request], *, status_code: int = 200, ids: tuple[str, ...] = ("llama-3",)
):
    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/models"):
            # vLLM discovery also reads /openapi.json; a server without it is fine.
            return httpx.Response(404)
        calls.append(request)
        if status_code != 200:
            return httpx.Response(status_code, json={"error": {"message": "nope"}})
        return httpx.Response(200, json={"data": [{"id": model_id} for model_id in ids]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _inline(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "vllm",
        "mode": "chat",
        "model": "llama-3",
        "api_key": "vllm-key",
        "api_base": "https://vllm.example/v1",
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_connection_test_passes_and_finds_the_model(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    calls: list[httpx.Request] = []
    test_app.state.control_http_client = _models_server(calls)

    response = await client.post(URL, headers=ADMIN, json=_inline())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "passed"
    assert body["model_check"] == "found"
    assert "llama-3 is in its model list" in body["message"]
    assert calls
    assert all(str(call.url).startswith("https://vllm.example/v1/") for call in calls)
    assert all(call.headers["authorization"] == "Bearer vllm-key" for call in calls)


@pytest.mark.asyncio
async def test_connection_test_warns_when_the_model_is_not_listed(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.control_http_client = _models_server([], ids=("other-model",))

    response = await client.post(URL, headers=ADMIN, json=_inline(model="llama-3-typo"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "warning"
    assert body["model_check"] == "not_found"
    assert "llama-3-typo is not in its model list" in body["message"]


@pytest.mark.asyncio
async def test_connection_test_finds_a_model_the_catalog_also_knows(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.control_http_client = _models_server([], ids=("gpt-6-astra",))

    response = await client.post(
        URL,
        headers=ADMIN,
        json=_inline(
            provider="openai",
            model="gpt-6-astra",
            api_key="openai-key",
            api_base="https://api.openai.com/v1",
        ),
    )

    assert response.status_code == 200
    assert response.json()["model_check"] == "found"


@pytest.mark.asyncio
async def test_connection_test_reports_a_rejected_key(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.control_http_client = _models_server([], status_code=401)

    response = await client.post(URL, headers=ADMIN, json=_inline())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["status_code"] == 401
    assert "Check the API key" in body["message"]


@pytest.mark.asyncio
async def test_connection_test_reports_a_missing_key_without_calling_out(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    calls: list[httpx.Request] = []
    test_app.state.control_http_client = _models_server(calls)

    response = await client.post(URL, headers=ADMIN, json=_inline(api_key=None))

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "API key is missing" in response.json()["message"]
    assert calls == []


@pytest.mark.asyncio
async def test_connection_test_rejects_an_unknown_outbound_proxy(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.upstream_proxy_names = frozenset({"corp"})

    response = await client.post(URL, headers=ADMIN, json=_inline(outbound_proxy="other"))

    assert response.status_code == 400
    assert "not configured" in str(response.json())


@pytest.mark.asyncio
async def test_connection_test_uses_the_saved_key_when_editing(client, test_app, monkeypatch):
    setattr(test_app.state.settings, "master_key", "mk-test")
    calls: list[httpx.Request] = []
    test_app.state.control_http_client = _models_server(calls)
    saved = SimpleNamespace(
        deployment_id="dep-1",
        named_credential_id=None,
        deltallm_params={
            "provider": "vllm",
            "model": "llama-3",
            "api_key": "saved-key",
            "api_base": "https://vllm.example/v1",
        },
    )
    monkeypatch.setattr(
        model_endpoints,
        "_find_runtime_deployment",
        lambda app, deployment_id: saved if deployment_id == "dep-1" else None,
    )

    response = await client.post(
        URL,
        headers=ADMIN,
        json={"provider": "vllm", "mode": "chat", "model": "llama-3", "deployment_id": "dep-1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "passed"
    assert calls and all(call.headers["authorization"] == "Bearer saved-key" for call in calls)

    missing = await client.post(
        URL,
        headers=ADMIN,
        json={"provider": "vllm", "model": "llama-3", "deployment_id": "dep-missing"},
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_user_cannot_test_inline_settings(client, test_app):
    test_app.state.platform_identity_service = _IdentityService(_user_context("acct-plain"))

    response = await client.post(URL, cookies={"deltallm_session": "asset-session"}, json=_inline())

    assert response.status_code == 400
    assert "inline connection settings" in str(response.json())


async def _fake_resolver(hostname: str, port: int) -> tuple[str, ...]:
    del port
    return {"internal.example.com": ("10.0.0.7",)}[hostname]


@pytest.mark.asyncio
async def test_user_credential_cannot_reach_a_private_address(client, test_app):
    test_app.state.platform_identity_service = _IdentityService(_user_context("acct-plain"))
    test_app.state.user_egress_guard = UserEgressGuard(
        OutboundNetworkPolicy(resolver=_fake_resolver)
    )
    repository = _FakeNamedCredentialRepository()
    repository.records["mine"] = NamedCredentialRecord(
        credential_id="mine",
        name="mine",
        provider="vllm",
        connection_config={"api_key": "k", "api_base": "https://internal.example.com/v1"},
        name_scope="account:acct-plain",
        managed_asset_id="asset-mine",
    )
    test_app.state.named_credential_repository = repository
    test_app.state.managed_asset_access_repository.policies["asset-mine"] = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="asset-mine",
            asset_kind=AssetKind.NAMED_CREDENTIAL,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="acct-plain",
        )
    )
    test_app.state.dynamic_config_manager = SimpleNamespace(
        secret_resolver=SecretResolver(), get_master_key=lambda: None
    )
    calls: list[httpx.Request] = []
    test_app.state.control_http_client = _models_server(calls)

    response = await client.post(
        URL,
        cookies={"deltallm_session": "asset-session"},
        json={"provider": "vllm", "model": "llama-3", "named_credential_id": "mine"},
    )

    assert response.status_code == 400
    assert "not allowed for user-owned connections" in str(response.json())
    assert calls == []
