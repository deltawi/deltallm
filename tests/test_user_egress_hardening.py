from __future__ import annotations

from types import SimpleNamespace

import httpcore2
import httpx
import pytest

from src.client_address import resolve_client_address
from src.config import GeneralSettings
from src.config_runtime.secrets import SecretResolver, contains_secret_reference
from src.db.catalog.model_deployments import ModelDeploymentRecord
from src.db.catalog.named_credentials import NamedCredentialRecord
from src.outbound.network_policy import OutboundNetworkPolicy
from src.outbound.user_egress import (
    GuardedNetworkBackend,
    UserEgressDenied,
    UserEgressGuard,
    restricted_egress,
)
from src.providers.http_transport import UpstreamHTTPTransport
from src.services.access.managed_asset_access import is_creator_callable_key
from src.services.models.model_deployments import build_model_registry_from_records
from src.services.models.named_credentials import resolve_named_credential_record
from tests.test_named_credentials_api import (  # noqa: F401  (autouse fixture)
    _FakeNamedCredentialRepository,
    _IdentityService,
    _managed_asset_access_repository,
    _user_context,
)

_ADDRESSES = {
    "api.example.com": ("93.184.216.34",),
    "internal.example.com": ("10.0.0.7",),
    "metadata.example.com": ("169.254.169.254",),
}


async def _fake_resolver(hostname: str, port: int) -> tuple[str, ...]:
    del port
    return _ADDRESSES[hostname]


def _guard() -> UserEgressGuard:
    return UserEgressGuard(OutboundNetworkPolicy(resolver=_fake_resolver))


class _CredentialsById(_FakeNamedCredentialRepository):
    async def list_by_ids(self, credential_ids: list[str]) -> dict[str, NamedCredentialRecord]:
        return {key: self.records[key] for key in credential_ids if key in self.records}


class _RecordingBackend:
    def __init__(self) -> None:
        self.hosts: list[str] = []

    async def connect_tcp(self, host: str, port: int, **_kwargs: object) -> str:
        self.hosts.append(host)
        return f"stream:{host}:{port}"

    async def connect_unix_socket(self, path: str, **_kwargs: object) -> str:
        return path

    async def sleep(self, seconds: float) -> None:
        del seconds


# Secret references -------------------------------------------------------


def test_secret_reference_detection_covers_env_and_cloud_prefixes() -> None:
    assert contains_secret_reference({"api_key": "os.environ/DELTALLM_MASTER_KEY"})
    assert contains_secret_reference({"nested": ["aws.secretsmanager/arn:aws:x"]})
    assert contains_secret_reference(" gcp.secretmanager/projects/p/secrets/s")
    assert contains_secret_reference("azure.keyvault/https://v.vault.azure.net/secrets/s")
    assert not contains_secret_reference({"api_key": "sk-real", "api_base": "https://x"})


def test_only_platform_credentials_resolve_secret_references(monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_TEST_SECRET", "resolved-secret")
    config = {"api_key": "os.environ/AUDIT_TEST_SECRET", "api_base": "https://api.example.com"}

    platform = resolve_named_credential_record(
        NamedCredentialRecord(
            credential_id="c1", name="p", provider="openai", connection_config=config
        ),
        secret_resolver=SecretResolver(),
    )
    creator = resolve_named_credential_record(
        NamedCredentialRecord(
            credential_id="c2",
            name="c",
            provider="openai",
            connection_config=config,
            name_scope="account:acct-1",
        ),
        secret_resolver=SecretResolver(),
    )

    assert platform is not None and platform.connection_config["api_key"] == "resolved-secret"
    assert creator is not None
    assert "api_key" not in creator.connection_config
    assert creator.connection_config["api_base"] == "https://api.example.com"


@pytest.mark.asyncio
async def test_user_cannot_store_secret_reference_and_discovery_never_sends_it(
    client, test_app, monkeypatch
):
    monkeypatch.setenv("DELTALLM_MASTER_KEY", "sk-master-value")
    repository = _FakeNamedCredentialRepository()
    test_app.state.named_credential_repository = repository
    test_app.state.platform_identity_service = _IdentityService(_user_context("acct-plain"))
    cookies = {"deltallm_session": "asset-session"}

    response = await client.post(
        "/ui/api/named-credentials",
        cookies=cookies,
        json={
            "name": "env ref",
            "provider": "openai",
            "connection_config": {
                "api_key": "os.environ/DELTALLM_MASTER_KEY",
                "api_base": "https://api.example.com/v1",
            },
        },
    )
    assert response.status_code == 400
    assert repository.records == {}

    # A row stored before this check is never resolved for a creator credential.
    repository.records["legacy"] = NamedCredentialRecord(
        credential_id="legacy",
        name="legacy",
        provider="openai",
        connection_config={
            "api_key": "os.environ/DELTALLM_MASTER_KEY",
            "api_base": "https://api.example.com/v1",
        },
        name_scope="account:acct-plain",
        managed_asset_id="asset-legacy",
    )
    from src.services.access.managed_asset_access import (
        AssetAccessPolicy,
        AssetKind,
        GovernanceSource,
        ManagedAsset,
    )

    test_app.state.managed_asset_access_repository.policies["asset-legacy"] = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="asset-legacy",
            asset_kind=AssetKind.NAMED_CREDENTIAL,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="acct-plain",
        )
    )
    test_app.state.dynamic_config_manager = SimpleNamespace(
        secret_resolver=SecretResolver(), get_master_key=lambda: None
    )
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"data": []})

    test_app.state.control_http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    discovered = await client.post(
        "/ui/api/provider-models/discover",
        cookies=cookies,
        json={"provider": "openai", "named_credential_id": "legacy"},
    )
    assert discovered.status_code == 200
    assert all("sk-master-value" not in str(value) for value in seen)


# Shared credential target -------------------------------------------------


@pytest.mark.asyncio
async def test_editor_cannot_move_shared_credential_but_owner_can(client, test_app):
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository()
    identity = _IdentityService(_user_context("acct-owner", team_ids=("team-1",)))
    test_app.state.platform_identity_service = identity
    cookies = {"deltallm_session": "asset-session"}
    created = await client.post(
        "/ui/api/named-credentials",
        cookies=cookies,
        json={
            "name": "Team OpenAI",
            "provider": "openai",
            "connection_config": {"api_key": "sk-owner", "api_base": "https://api.example.com/v1"},
            "access": {"visibility": "team", "subject_id": "team-1", "access_role": "editor"},
        },
    )
    assert created.status_code == 200
    credential_id = created.json()["credential_id"]

    identity.context = _user_context("acct-editor", team_ids=("team-1",))
    moved = await client.put(
        f"/ui/api/named-credentials/{credential_id}",
        cookies=cookies,
        json={
            "name": "Team OpenAI",
            "provider": "openai",
            "connection_config": {"api_base": "https://attacker.example/v1"},
        },
    )
    assert moved.status_code == 403
    assert "api_base" in moved.json()["detail"]

    rotated = await client.put(
        f"/ui/api/named-credentials/{credential_id}",
        cookies=cookies,
        json={"name": "Team OpenAI", "provider": "openai", "connection_config": {"api_key": "sk-new"}},
    )
    assert rotated.status_code == 200

    identity.context = _user_context("acct-owner", team_ids=("team-1",))
    owner_move = await client.put(
        f"/ui/api/named-credentials/{credential_id}",
        cookies=cookies,
        json={
            "name": "Team OpenAI",
            "provider": "openai",
            "connection_config": {"api_base": "https://api.example.com/v2"},
        },
    )
    assert owner_move.status_code == 200


@pytest.mark.asyncio
async def test_bedrock_region_must_be_a_region(client, test_app):
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository()
    test_app.state.platform_identity_service = _IdentityService(_user_context("acct-owner"))
    response = await client.post(
        "/ui/api/named-credentials",
        cookies={"deltallm_session": "asset-session"},
        json={
            "name": "Bedrock",
            "provider": "bedrock",
            "connection_config": {
                "region": "x.attacker.example#",
                "aws_access_key_id": "AKIA",
                "aws_secret_access_key": "secret",
            },
        },
    )
    assert response.status_code == 400


# Client address -----------------------------------------------------------


@pytest.mark.parametrize(
    ("peer", "forwarded", "expected"),
    [
        ("203.0.113.9", "1.2.3.4", "203.0.113.9"),  # untrusted peer: header ignored
        ("10.0.0.2", "1.2.3.4, 198.51.100.7", "198.51.100.7"),  # spoofed left entry ignored
        ("10.0.0.2", "198.51.100.7, 10.0.0.3", "198.51.100.7"),
        ("127.0.0.1", "10.0.0.5", "10.0.0.5"),  # internal caller behind a local proxy
        ("10.0.0.2", "not-an-ip", "10.0.0.2"),
        ("10.0.0.2", None, "10.0.0.2"),
    ],
)
def test_client_address_reads_forwarding_only_from_trusted_proxies(peer, forwarded, expected):
    assert resolve_client_address(peer, forwarded) == expected


def test_client_address_honors_configured_proxy_networks():
    assert (
        resolve_client_address("10.0.0.2", "198.51.100.7", cidrs=("192.0.2.0/24",)) == "10.0.0.2"
    )


def test_trusted_proxy_cidrs_reject_trust_all():
    with pytest.raises(ValueError):
        GeneralSettings(trusted_proxy_cidrs=["0.0.0.0/0"])
    assert GeneralSettings(trusted_proxy_cidrs=["10.1.2.3/8"]).trusted_proxy_cidrs == [
        "10.0.0.0/8"
    ]


# Egress guard -------------------------------------------------------------


@pytest.mark.asyncio
async def test_guard_checks_saved_urls():
    guard = _guard()
    await guard.check_url("https://api.example.com/v1")
    for url in (
        "https://internal.example.com/v1",
        "https://metadata.example.com/latest",
        "http://api.example.com/v1",
        "https://api.example.com:8443/v1",
        "https://127.0.0.1/v1",
    ):
        with pytest.raises(UserEgressDenied):
            await guard.check_url(url)


@pytest.mark.asyncio
async def test_guarded_backend_pins_restricted_connections_only():
    guard = _guard()
    guard.replace_origins(
        "models",
        restricted_urls=["https://api.example.com/v1", "https://internal.example.com/v1"],
        trusted_urls=["https://internal.example.com/v1"],
    )
    inner = _RecordingBackend()
    backend = GuardedNetworkBackend(inner, guard, connect_error=httpcore2.ConnectError)

    await backend.connect_tcp("api.example.com", 443)
    # Used by a platform deployment as well, so it stays trusted.
    await backend.connect_tcp("internal.example.com", 443)
    assert inner.hosts == ["93.184.216.34", "internal.example.com"]

    with restricted_egress():
        with pytest.raises(httpcore2.ConnectError):
            await backend.connect_tcp("internal.example.com", 443)
        with pytest.raises(httpcore2.ConnectError):
            await backend.connect_tcp("metadata.example.com", 443)


@pytest.mark.asyncio
async def test_upstream_transport_rejects_restricted_request_before_sending():
    guard = _guard()
    transport = UpstreamHTTPTransport(limits=httpx.Limits(), egress_guard=guard)
    async with httpx.AsyncClient(transport=transport) as client:
        with restricted_egress():
            with pytest.raises(httpx.ConnectError):
                await client.post("http://internal.example.com:8080/v1/chat", json={})


@pytest.mark.asyncio
async def test_upstream_transport_checks_calls_routed_to_a_named_proxy():
    guard = _guard()
    proxied = UpstreamHTTPTransport(
        limits=httpx.Limits(), proxy="http://proxy.example:3128", egress_guard=guard
    )
    transport = UpstreamHTTPTransport(
        limits=httpx.Limits(),
        named_proxies={"corp": proxied},
        owns_named=True,
        egress_guard=guard,
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with restricted_egress():
            with pytest.raises(httpx.ConnectError, match="user egress denied"):
                await client.post(
                    "https://internal.example.com/v1/chat",
                    json={},
                    extensions={"deltallm_outbound_proxy": "corp"},
                )


@pytest.mark.asyncio
async def test_registry_excludes_creator_deployments_with_disallowed_urls():
    guard = _guard()
    repository = _CredentialsById()
    for credential_id, api_base, name_scope in (
        ("ok", "https://api.example.com/v1", "account:acct-1"),
        ("bad", "http://10.0.0.7:8000/v1", "account:acct-1"),
        # A platform credential shared with a creator keeps its admin-chosen URL.
        ("shared", "http://ollama.internal:11434/v1", "platform"),
    ):
        repository.records[credential_id] = NamedCredentialRecord(
            credential_id=credential_id,
            name=credential_id,
            provider="openai",
            connection_config={"api_key": "sk-x", "api_base": api_base},
            name_scope=name_scope,
        )

    def record(deployment_id: str, credential_id: str) -> ModelDeploymentRecord:
        return ModelDeploymentRecord(
            deployment_id=deployment_id,
            model_name=f"m-{deployment_id}",
            deltallm_params={"provider": "openai", "model": "gpt-x"},
            named_credential_id=credential_id,
            governance_source="creator",
            credential_binding_state="active",
        )

    registry = await build_model_registry_from_records(
        [record("d-ok", "ok"), record("d-bad", "bad"), record("d-shared", "shared")],
        SimpleNamespace(openai_api_key=None, openai_base_url=None),
        repository,
        egress_guard=guard,
    )

    assert set(registry) == {"m-d-ok", "m-d-shared"}
    assert guard.is_restricted("api.example.com", 443)
    assert not guard.is_restricted("ollama.internal", 11434)


def test_creator_callable_key_detection():
    assert is_creator_callable_key("creator-" + "a" * 64 + "-docs")
    assert not is_creator_callable_key("docs")
    assert not is_creator_callable_key("creator-docs")
