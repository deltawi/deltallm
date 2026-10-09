import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_output_tpm_admin import OutputAdminDB
from tests.test_output_tpm_policy_admin import _KeyDB
from tests.test_ui_tiers_api import _FakeTierRepository, _headers


class ModelTeamDB(OutputAdminDB):
    async def execute_raw(self, query, *params):
        if "SET model_output_tpm_limit = $1" in query:
            self.teams[params[1]]["model_output_tpm_limit"] = (
                json.loads(params[0]) if params[0] else None
            )
            return 1
        return await super().execute_raw(query, *params)


class ModelKeyDB(_KeyDB):
    async def execute_raw(self, query, *params):
        if "SET model_output_tpm_limit = $1" in query:
            self.keys[params[1]]["model_output_tpm_limit"] = (
                json.loads(params[0]) if params[0] else None
            )
            return 1
        return await super().execute_raw(query, *params)


@pytest.mark.parametrize("scope", ["team", "key"])
async def test_model_limit_update_omit_clear_and_transaction_rollback(client, test_app, scope):
    db = ModelTeamDB() if scope == "team" else ModelKeyDB()
    if scope == "team":
        db.teams["team-1"] = {"team_id": "team-1", "team_alias": "Test", "organization_id": "org-1"}
    db.organizations = {
        "org-1": {
            "organization_id": "org-1",
            "organization_name": "Test",
            "lifecycle_state": "active",
        }
    }
    test_app.state.prisma_manager = SimpleNamespace(client=db)
    test_app.state.key_service.repository.prisma = db
    test_app.state.settings.master_key = "mk-test"
    test_app.state.limit_counter.degraded_mode = "fail_closed"
    test_app.state.app_config.general_settings.enable_jwt_auth = False
    test_app.state.app_config.general_settings.custom_auth = None
    invalidate = AsyncMock(return_value=0)
    if scope == "team":
        test_app.state.key_service.invalidate_keys_for_team = invalidate
    else:
        test_app.state.key_service.invalidate_key_cache_by_hash = invalidate
    identity = "team-1" if scope == "team" else "key-hash-1"
    path = f"/ui/api/{'teams' if scope == 'team' else 'keys'}/{identity}"
    headers = {"Authorization": "Bearer mk-test"}
    set_limit = await client.put(path, headers=headers, json={"model_output_tpm_limit": {"m": 10}})
    assert set_limit.status_code == 200, set_limit.text
    assert set_limit.json()["model_output_tpm_limit"] == {"m": 10}
    assert db.invalidations[0]["scope_id"] == identity
    invalidate.assert_awaited_once()
    omitted = await client.put(path, headers=headers, json={})
    assert omitted.json()["model_output_tpm_limit"] == {"m": 10}
    db.fail_enqueue = True
    failed = await client.put(path, headers=headers, json={"model_output_tpm_limit": None})
    assert failed.status_code == 503
    rows = db.teams if scope == "team" else db.keys
    assert rows[identity]["model_output_tpm_limit"] == {"m": 10}
    db.fail_enqueue = False
    cleared = await client.put(path, headers=headers, json={"model_output_tpm_limit": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["model_output_tpm_limit"] is None


async def test_tier_output_bulk_contract_and_clear(client, test_app):
    from src.db.tiers import TierModelPolicyRecord

    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version()
    repository.model_policies["version-1"] = [
        TierModelPolicyRecord("p", "version-1", "m", rpm_limit=100)
    ]
    from src.config import AppConfig

    test_app.state.app_config.general_settings = AppConfig().general_settings
    test_app.state.tier_repository = repository
    test_app.state.limit_counter.degraded_mode = "fail_closed"
    path = "/ui/api/tiers/tier-1/versions/version-1/model-policies/bulk-limits"
    for revision, value in [(0, 10), (1, None)]:
        response = await client.post(
            path,
            headers=_headers(test_app),
            json={"expected_revision": revision, "all_filtered": True, "output_tpm_limit": value},
        )
        assert response.status_code == 200, response.text
        assert response.json()["configuration_revision"] == revision + 1
        assert repository.model_policies["version-1"][0].output_tpm_limit == value
        assert repository.model_policies["version-1"][0].rpm_limit == 100
    invalid = await client.post(
        path,
        headers=_headers(test_app),
        json={"expected_revision": 2, "all_filtered": True, "output_tpm_limit": True},
    )
    assert invalid.status_code == 422


@pytest.mark.parametrize("operation", ["activation", "enable"])
@pytest.mark.parametrize("source", ["config", "environment"])
async def test_tier_output_publication_rejects_fail_open_snapshot_mode(
    client, test_app, monkeypatch, operation, source
):
    from src.config import AppConfig

    repository = _FakeTierRepository()
    repository.seed_tier()
    tier = repository.tiers["tier-1"]
    version = repository.seed_version()
    tier.enabled = False
    tier.active_version_id = version.tier_version_id
    if operation == "enable":
        version.status = "active"
    test_app.state.tier_repository = repository
    test_app.state.prisma_manager = SimpleNamespace(
        client=SimpleNamespace(query_raw=AsyncMock(return_value=[{"output_enabled": True}]))
    )
    settings = AppConfig().general_settings
    if source == "config":
        settings.tier_policy_mode = "enforce"
        settings.tier_policy_missing_service_mode = "fail_open"
    else:
        monkeypatch.setattr(test_app.state.settings, "tier_policy_mode", "enforce", raising=False)
        monkeypatch.setattr(
            test_app.state.settings, "tier_policy_missing_service_mode", "fail_open", raising=False
        )
    test_app.state.app_config.general_settings = settings
    test_app.state.limit_counter.degraded_mode = "fail_closed"
    if operation == "enable":
        response = await client.patch(
            "/ui/api/tiers/tier-1", headers=_headers(test_app), json={"enabled": True}
        )
    else:
        response = await client.post(
            "/ui/api/tiers/tier-1/versions/version-1/activate",
            headers=_headers(test_app),
            json={"expected_revision": 0, "expected_active_version_id": None},
        )
    assert response.status_code == 400, response.text
    assert "tier_policy_missing_service_mode=fail_closed" in response.json()["detail"]
    assert not tier.enabled
    assert version.status == ("active" if operation == "enable" else "draft")


@pytest.mark.parametrize("endpoint", ["settings", "routing"])
async def test_output_config_denial_returns_client_error_without_persistence(
    client, test_app, endpoint
):
    from copy import deepcopy

    from src.config_runtime.dynamic import DynamicConfigManager
    from tests.test_output_policy_configuration import OutputConfigDB, FakeRedis

    db = OutputConfigDB(
        {
            "general_settings": {
                "tier_policy_mode": "enforce",
                "tier_policy_missing_service_mode": "fail_closed",
                "redis_degraded_mode": "fail_closed",
            }
        }
    )
    before = deepcopy(db.config_value)
    manager = DynamicConfigManager(
        db, FakeRedis() if endpoint == "settings" else None, {}, poll_interval_seconds=0
    )
    await manager.initialize()
    test_app.state.dynamic_config_manager = manager
    test_app.state.app_config = manager.get_app_config()
    payload = (
        {"general_settings": {"enable_jwt_auth": True}}
        if endpoint == "settings"
        else {"config": {"timeout": 61}}
    )
    try:
        response = await client.put(f"/ui/api/{endpoint}", headers=_headers(test_app), json=payload)
        assert response.status_code == 400, response.text
        assert ("stored API-key" if endpoint == "settings" else "Redis") in response.json()[
            "detail"
        ]
        assert db.config_value == before
        assert db.updated_by is None
        assert db.output_reads == 1
        assert manager.get_config_generation() == 1
    finally:
        await manager.close()


@pytest.mark.parametrize(
    "field,environment,candidate",
    [
        ("tier_policy_mode", "enforce", "disabled"),
        ("tier_policy_missing_service_mode", "fail_closed", "fail_open"),
    ],
)
async def test_tier_mode_settings_require_restart_before_persistence(
    client, test_app, field, environment, candidate
):
    from src.config import Settings
    from src.config_runtime.dynamic import DynamicConfigManager
    from tests.test_output_policy_configuration import OutputConfigDB

    db = OutputConfigDB({})
    manager = DynamicConfigManager(
        db, None, {}, poll_interval_seconds=0, runtime_settings=Settings(**{field: environment})
    )
    await manager.initialize()
    test_app.state.dynamic_config_manager = manager
    test_app.state.app_config = manager.get_app_config()
    try:
        response = await client.put(
            "/ui/api/settings",
            headers=_headers(test_app),
            json={"general_settings": {field: candidate}},
        )
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "restart_required"
        assert field in response.json()["detail"]["message"]
        assert db.config_value == {}
        assert db.updated_by is None
        assert db.output_reads == 0
        assert manager.get_config_generation() == 1
    finally:
        await manager.close()
