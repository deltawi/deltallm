from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.api.admin.endpoints.models import _rebuild_runtime_registry
from src.config import AppConfig
from src.billing.budgets.budget import BudgetEnforcementService
from src.services.access.creator_model_access import refresh_creator_model_access_for_app
from src.services.models.model_deployments import load_model_registry
from tests.db import external_auth_fixtures as fixtures
from tests.db.test_external_customer_assets import clear_assets, install_customer
from tests.db.test_external_customer_keys import own_key
from tests.db.test_external_auth_exchange import proof, services

pytestmark = pytest.mark.postgres
external_database = fixtures.external_database


class SpendRecorder:
    def __init__(self):
        self.events = []
        self.complete = asyncio.Event()

    async def log_spend(self, **record):
        self.events.append(record)
        self.complete.set()

    async def log_request_failure(self, **record):
        pass


async def create_assets(client):
    credential = await client.post(
        "/ui/api/named-credentials",
        json={
            "name": "Customer private provider",
            "provider": "openai",
            "connection_config": {
                "api_key": "disposable-provider-secret",
                "api_base": "https://api.openai.com/v1",
            },
        },
    )
    assert credential.status_code == 200, credential.text
    namespace = "mod-test-" + uuid4().hex[:8]
    name = namespace + "/private-model"
    model = await client.post(
        "/ui/api/models",
        json={
            "model_name": name,
            "api_model_id": name,
            "api_namespace": namespace,
            "api_model_slug": "private-model",
            "named_credential_id": credential.json()["credential_id"],
            "deltallm_params": {"provider": "openai", "model": "openai/gpt-4o-mini"},
        },
    )
    assert model.status_code == 200, model.text
    return (
        name,
        credential.json()["access"]["managed_asset_id"],
        model.json()["access"]["managed_asset_id"],
    )


async def publish_models(app):
    registry, _ = await load_model_registry(
        app.state.model_deployment_repository,
        AppConfig(),
        app.state.settings,
        source_mode="db_only",
        named_credential_repository=app.state.named_credential_repository,
        allow_db_error_fallback=False,
    )
    app.state.model_registry = registry
    _rebuild_runtime_registry(app)
    await refresh_creator_model_access_for_app(app)


async def assert_budget_after_renewal(fixture, app, inference, body, own):
    app.state.budget_service = BudgetEnforcementService(fixture.db, query_mode="combined")
    await fixture.db.execute_raw(
        "UPDATE deltallm_teamtable SET max_budget=0 WHERE team_id=$1", fixture.team_id
    )
    before = app.state.http_client.post_calls
    exchange, _, _ = services(fixture)
    await exchange.exchange(proof(fixture), "budget-renewal")
    response = await inference.post(
        "/v1/chat/completions", json=body, headers={"Authorization": "Bearer " + own}
    )
    assert response.status_code == 429, response.text
    assert response.json()["error"]["type"] == "budget_exceeded"
    assert app.state.http_client.post_calls == before
    rows = await fixture.db.query_raw(
        "SELECT max_budget FROM deltallm_teamtable WHERE team_id=$1", fixture.team_id
    )
    assert rows[0]["max_budget"] == 0


async def test_normal_bearer_inference_keeps_customer_ownership_and_sharing(
    external_database, test_app, client
):
    fixture = external_database
    await install_customer(fixture, test_app, client)
    peer = "peer-" + uuid4().hex
    await fixture.db.execute_raw(
        "INSERT INTO deltallm_platformaccount (account_id,email,updated_at) VALUES ($1,$1 || '@example.com',NOW())",
        peer,
    )
    recorder = SpendRecorder()
    test_app.state.spend_tracking_service = recorder
    try:
        name, credential_asset, model_asset = await create_assets(client)
        await publish_models(test_app)
        own = "sk-" + uuid4().hex
        foreign = "sk-" + uuid4().hex
        await own_key(fixture, own, test_app.state.key_service)
        foreign_hash = await own_key(fixture, foreign, test_app.state.key_service)
        await fixture.db.execute_raw(
            "UPDATE deltallm_verificationtoken SET owner_account_id=$2 WHERE token=$1",
            foreign_hash,
            peer,
        )
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://gateway.test"
        ) as inference:
            body = {"model": name, "messages": [{"role": "user", "content": "Test private access"}]}
            accepted = await inference.post(
                "/v1/chat/completions", json=body, headers={"Authorization": "Bearer " + own}
            )
            assert accepted.status_code == 200, accepted.text
            await asyncio.wait_for(recorder.complete.wait(), 1)
            event = recorder.events[0]
            assert event["user_id"] == fixture.account_id and event["team_id"] == fixture.team_id
            assert event["organization_id"] == fixture.organization_id and event["model"] == name
            assert "disposable-provider-secret" not in accepted.text
            before = test_app.state.http_client.post_calls
            denied = await inference.post(
                "/v1/chat/completions", json=body, headers={"Authorization": "Bearer " + foreign}
            )
            assert denied.status_code == 403, denied.text
            assert test_app.state.http_client.post_calls == before
            for asset in (credential_asset, model_asset):
                shared = await client.put(
                    "/ui/api/assets/" + asset + "/access",
                    json={
                        "access": {
                            "expected_policy_version": 1,
                            "visibility": "team",
                            "subject_id": fixture.team_id,
                            "access_role": "reader",
                        }
                    },
                )
                assert shared.status_code == 200, shared.text
            await publish_models(test_app)
            shared = await inference.post(
                "/v1/chat/completions", json=body, headers={"Authorization": "Bearer " + foreign}
            )
            assert shared.status_code == 200, shared.text
            await assert_budget_after_renewal(fixture, test_app, inference, body, own)
    finally:
        await clear_assets(fixture)
        await fixture.db.execute_raw(
            "DELETE FROM deltallm_verificationtoken WHERE owner_account_id=$1", peer
        )
        await fixture.db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id=$1", peer
        )
