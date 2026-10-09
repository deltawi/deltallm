from types import SimpleNamespace
from unittest.mock import AsyncMock
from contextlib import asynccontextmanager
from copy import deepcopy

import pytest
from fastapi import HTTPException

from src.api.admin.output_policy import output_policy_change
from src.config import AppConfig
from tests.test_ui_rate_limits import FakeAdminDB


class OutputAdminDB(FakeAdminDB):
    def __init__(self):
        super().__init__()
        self.invalidations = []
        self.fail_enqueue = False

    @asynccontextmanager
    async def tx(self):
        before = deepcopy((self.organizations, self.teams, self.invalidations))
        try:
            yield self
        except BaseException:
            self.organizations, self.teams, self.invalidations = before
            raise

    async def query_raw(self, query, *params):
        if "INSERT INTO deltallm_cacheinvalidationoutbox" in query:
            if self.fail_enqueue:
                raise RuntimeError("Outbox unavailable")
            row = dict(
                zip(
                    (
                        "invalidation_id",
                        "scope_type",
                        "scope_id",
                        "reason",
                        "metadata",
                        "max_attempts",
                        "next_attempt_at",
                    ),
                    params,
                    strict=True,
                )
            )
            row["status"] = "pending"
            for pending in self.invalidations:
                if all(
                    pending[key] == row[key]
                    for key in ("scope_type", "scope_id", "reason", "status")
                ):
                    return [pending]
            self.invalidations.append(row)
            return [row]
        return await super().query_raw(query, *params)

    async def execute_raw(self, query, *params):
        if "SET output_tpm_limit = $1" in query:
            rows = self.organizations if "deltallm_organizationtable" in query else self.teams
            rows[params[1]]["output_tpm_limit"] = params[0]
            return 1
        if "INSERT INTO deltallm_organizationtable" in query:
            existing = self.organizations.get(params[0])
            previous_output = existing.get("output_tpm_limit") if existing else None
            result = await super().execute_raw(query, *params)
            self.organizations[params[0]]["output_tpm_limit"] = previous_output
            return result
        return await super().execute_raw(query, *params)


@pytest.mark.parametrize("scope", ["key", "user", "team", "organization"])
@pytest.mark.parametrize("value", [False, True, "12", 0, -1, 1.5, 2**31])
def test_admin_rejects_invalid_output_policy_before_persistence(scope, value):
    with pytest.raises(HTTPException) as exc:
        output_policy_change(SimpleNamespace(), {"output_tpm_limit": value}, scope=scope)
    assert exc.value.status_code == 400


def test_shared_auth_and_redis_requirements_allow_policy_clearing():
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                limit_counter=SimpleNamespace(redis=object(), degraded_mode="fail_closed"),
                app_config=AppConfig(general_settings={"enable_jwt_auth": True}),
            )
        )
    )
    assert output_policy_change(request, {"output_tpm_limit": 10}, scope="key").value == 10
    with pytest.raises(HTTPException):
        output_policy_change(request, {"output_tpm_limit": 10}, scope="team")
    request.app.state.limit_counter.redis = None
    assert output_policy_change(request, {"output_tpm_limit": None}, scope="team").value is None
    assert not output_policy_change(request, {}, scope="team").present


async def test_shared_admin_create_read_omit_clear_and_invalidate(client, test_app):
    db = OutputAdminDB()
    test_app.state.prisma_manager = SimpleNamespace(client=db)
    test_app.state.key_service.repository.prisma = db
    test_app.state.settings.master_key = "mk-test"
    test_app.state.limit_counter.degraded_mode = "fail_closed"
    test_app.state.app_config.general_settings.enable_jwt_auth = False
    test_app.state.app_config.general_settings.custom_auth = None
    invalidate_org = test_app.state.key_service.invalidate_keys_for_org = AsyncMock(return_value=0)
    invalidate_team = test_app.state.key_service.invalidate_keys_for_team = AsyncMock(
        return_value=0
    )
    headers = {"Authorization": "Bearer mk-test"}
    for path, body, identity, invalidation in (
        ("organizations", {"organization_id": "org-output"}, "org-output", invalidate_org),
        (
            "teams",
            {"team_id": "team-output", "organization_id": "org-output"},
            "team-output",
            invalidate_team,
        ),
    ):
        created = await client.post(
            f"/ui/api/{path}", headers=headers, json={**body, "output_tpm_limit": 100}
        )
        assert created.status_code == 200, created.text
        assert created.json()["output_tpm_limit"] == 100
        omitted = await client.put(f"/ui/api/{path}/{identity}", headers=headers, json={})
        assert omitted.status_code == 200, omitted.text
        assert omitted.json()["output_tpm_limit"] == 100
        read = await client.get(f"/ui/api/{path}/{identity}", headers=headers)
        assert read.json()["output_tpm_limit"] == 100
        cleared = await client.put(
            f"/ui/api/{path}/{identity}", headers=headers, json={"output_tpm_limit": None}
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["output_tpm_limit"] is None
        assert invalidation.await_count >= 1
    assert [(row["scope_type"], row["scope_id"]) for row in db.invalidations] == [
        ("organization", "org-output"),
        ("team", "team-output"),
    ]


@pytest.mark.parametrize(
    "scope,path,identity,method",
    [
        ("organization", "organizations", "org-output", "invalidate_keys_for_org"),
        ("team", "teams", "team-output", "invalidate_keys_for_team"),
    ],
)
async def test_output_policy_update_has_durable_invalidation_on_failure(
    client,
    test_app,
    scope,
    path,
    identity,
    method,
    caplog,
):
    db = OutputAdminDB()
    test_app.state.prisma_manager = SimpleNamespace(client=db)
    test_app.state.key_service.repository.prisma = db
    test_app.state.settings.master_key = "mk-test"
    test_app.state.limit_counter.degraded_mode = "fail_closed"
    test_app.state.app_config.general_settings.enable_jwt_auth = False
    test_app.state.app_config.general_settings.custom_auth = None
    headers = {"Authorization": "Bearer mk-test"}
    org = await client.post(
        "/ui/api/organizations", headers=headers, json={"organization_id": "org-output"}
    )
    assert org.status_code == 200, org.text
    if scope == "team":
        team = await client.post(
            "/ui/api/teams",
            headers=headers,
            json={"team_id": identity, "organization_id": "org-output"},
        )
        assert team.status_code == 200, team.text
    invalidate = AsyncMock(side_effect=RuntimeError("Token discovery failed"))
    setattr(test_app.state.key_service, method, invalidate)
    response = await client.put(
        f"/ui/api/{path}/{identity}", headers=headers, json={"output_tpm_limit": 100}
    )
    assert response.status_code == 200, response.text
    assert response.json()["output_tpm_limit"] == 100
    invalidate.assert_awaited_once()
    assert len(db.invalidations) == 1
    assert db.invalidations[0]["scope_type"] == scope
    assert db.invalidations[0]["scope_id"] == identity
    assert db.invalidations[0]["status"] == "pending"
    assert "output_tpm_policy_invalidation_queued" in caplog.text

    db.fail_enqueue = True
    failed = await client.put(
        f"/ui/api/{path}/{identity}", headers=headers, json={"output_tpm_limit": 50}
    )
    assert failed.status_code == 503, failed.text
    assert "could not be scheduled" in failed.text
    rows = db.organizations if scope == "organization" else db.teams
    assert rows[identity]["output_tpm_limit"] == 100
    assert len(db.invalidations) == 1
    invalidate.assert_awaited_once()
