from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.auth.external_client import ExternalClientResolver
from src.auth.external_config import ExternalAuthSettings
from src.db.logical_models import LogicalModelRepository
from src.db.managed_assets import ManagedAssetAccessRepository
from src.db.named_credentials import NamedCredentialRepository
from src.db.repositories import KeyRepository, ModelDeploymentRepository
from src.services.creator_model_access import CreatorModelAccessService
from src.services.key_service import KeyService
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_auth_exchange import enable, proof, services

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


async def install_customer(fixture, app, client):
    await enable(fixture)
    exchange, sessions, _ = services(fixture)
    exchange.identities.sessions.external = sessions
    response = await exchange.exchange(proof(fixture), "customer-assets")
    app.state.platform_identity_service = exchange.identities
    app.state.external_auth_runtime = SimpleNamespace(
        client_resolver=ExternalClientResolver(
            ExternalAuthSettings(allowed_origins=("https://console.example.com",))
        )
    )
    app.state.prisma_manager = SimpleNamespace(client=fixture.db)
    app.state.named_credential_repository = NamedCredentialRepository(fixture.db)
    app.state.managed_asset_access_repository = ManagedAssetAccessRepository(fixture.db)
    app.state.logical_model_repository = LogicalModelRepository(fixture.db)
    app.state.model_deployment_repository = ModelDeploymentRepository(fixture.db)
    app.state.creator_model_access_service = CreatorModelAccessService(
        app.state.managed_asset_access_repository, app.state.logical_model_repository
    )
    app.state.key_service = KeyService(
        KeyRepository(fixture.db),
        app.state.redis,
        salt="test-external-salt",
        auth_cache_ttl_seconds=60,
    )
    client.cookies.set("deltallm_session", response.session_token)
    client.headers["Origin"] = "https://console.example.com"
    return exchange


async def clear_assets(fixture):
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_modeldeployment WHERE model_id IN (SELECT model_id FROM deltallm_model WHERE managed_asset_id IN (SELECT asset_id FROM deltallm_managedasset WHERE owner_account_id = $1))",
        fixture.account_id,
    )
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_model WHERE managed_asset_id IN (SELECT asset_id FROM deltallm_managedasset WHERE owner_account_id = $1)",
        fixture.account_id,
    )
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_namedcredential WHERE created_by_account_id = $1", fixture.account_id
    )
    await fixture.db.execute_raw(
        "DELETE FROM deltallm_managedasset WHERE owner_account_id = $1", fixture.account_id
    )


async def test_customer_creates_private_assets_and_keeps_normal_api_key_identity(
    external_database, test_app, client
):
    fixture = external_database
    await install_customer(fixture, test_app, client)
    credential_id = str(uuid4())
    namespace = "mod-test-" + uuid4().hex[:8]
    model_name = namespace + "/private-model"
    try:
        credential = await client.post(
            "/ui/api/named-credentials",
            json={
                "credential_id": credential_id,
                "name": "Private provider",
                "provider": "openai",
                "connection_config": {
                    "api_key": "provider-secret",
                    "api_base": "https://api.openai.com/v1",
                },
            },
        )
        assert credential.status_code == 200, credential.text
        assert "provider-secret" not in credential.text
        model = await client.post(
            "/ui/api/models",
            json={
                "model_name": model_name,
                "api_model_id": model_name,
                "api_namespace": namespace,
                "api_model_slug": "private-model",
                "named_credential_id": credential_id,
                "deltallm_params": {"provider": "openai", "model": "openai/gpt-4o-mini"},
            },
        )
        assert model.status_code == 200, model.text
        assert "provider-secret" not in model.text
        owned = await fixture.db.query_raw(
            "SELECT owner_account_id, created_by_account_id FROM deltallm_managedasset WHERE owner_account_id = $1",
            fixture.account_id,
        )
        assert len(owned) == 2
        assert all(row["created_by_account_id"] == fixture.account_id for row in owned)
        payload = {"key_name": "Own key", "team_id": fixture.team_id}
        disabled = await client.post("/ui/api/keys", json=payload)
        assert disabled.status_code == 403, disabled.text
        await fixture.db.execute_raw(
            "UPDATE deltallm_teamtable SET self_service_keys_enabled = true WHERE team_id = $1",
            fixture.team_id,
        )
        created = await client.post(
            "/ui/api/keys",
            json={**payload, "user_id": "foreign-runtime", "owner_account_id": "foreign-account"},
        )
        assert created.status_code == 200, created.text
        raw_key = created.json()["raw_key"]
        auth = await test_app.state.key_service.validate_key(raw_key)
        assert auth.owner_account_id == fixture.account_id and auth.user_id == fixture.account_id
        assert auth.team_id == fixture.team_id and auth.organization_id == fixture.organization_id
        snapshot = await test_app.state.creator_model_access_service.reload()
        assert model_name in snapshot.visible_models(auth)
        foreign = auth.model_copy(
            update={
                "owner_account_id": "other-customer",
                "team_id": "foreign-team",
                "organization_id": "foreign-org",
            }
        )
        assert model_name not in snapshot.visible_models(foreign)
        no_bearer = await client.post(
            "/v1/chat/completions", json={"model": "gpt-4o-mini", "messages": []}
        )
        assert no_bearer.status_code == 401
        token_hash = test_app.state.key_service.hash_key(raw_key)
        for path in [f"/ui/api/keys/{token_hash}/regenerate", "/ui/api/teams"]:
            denied = await client.post(
                path,
                json={"team_alias": "Unauthorized", "organization_id": fixture.organization_id},
            )
            assert denied.status_code in (403, 405), denied.text
    finally:
        await clear_assets(fixture)


async def test_customer_asset_audiences_are_fixed_to_registered_workspace(
    external_database, test_app, client
):
    fixture = external_database
    await install_customer(fixture, test_app, client)
    try:
        created = await client.post(
            "/ui/api/named-credentials",
            json={
                "name": "Audience provider",
                "provider": "openai",
                "connection_config": {"api_key": "secret"},
            },
        )
        assert created.status_code == 200, created.text
        asset_id = created.json()["access"]["managed_asset_id"]
        for visibility, subject_id in [
            ("public", None),
            ("team", "foreign-team"),
            ("organization", "foreign-org"),
        ]:
            denied = await client.put(
                f"/ui/api/assets/{asset_id}/access",
                json={
                    "access": {
                        "expected_policy_version": 1,
                        "visibility": visibility,
                        "subject_id": subject_id,
                        "access_role": "reader",
                    }
                },
            )
            assert denied.status_code == 403, denied.text
        accepted = await client.put(
            f"/ui/api/assets/{asset_id}/access",
            json={
                "access": {
                    "expected_policy_version": 1,
                    "visibility": "team",
                    "subject_id": fixture.team_id,
                    "access_role": "reader",
                }
            },
        )
        assert accepted.status_code == 200, accepted.text
        options = await client.get(
            "/ui/api/assets/audience-options", params={"subject_type": "team"}
        )
        assert options.status_code == 200, options.text
        assert all(item["id"] == fixture.team_id for item in options.json()["data"])
        absent = await client.get("/ui/api/assets/foreign-asset/access")
        assert absent.status_code == 404
    finally:
        await clear_assets(fixture)
