from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncio
import pytest

from src.api.admin.output_policy import invalidate_output_policy_now
from tests.test_output_tpm_admin import OutputAdminDB
from tests.test_ui_scope_asset_visibility import _FakeScopeDB
from tests.test_ui_self_service_keys import _FakeKeyDB


class _OutputMutationDB:
    @asynccontextmanager
    async def tx(self):
        before = deepcopy(self.__dict__)
        try:
            yield self
        except BaseException:
            self.__dict__.update(before)
            raise

    async def query_raw(self, query, *params):
        if "INSERT INTO deltallm_cacheinvalidationoutbox" in query:
            return await OutputAdminDB.query_raw(self, query, *params)
        return await super().query_raw(query, *params)

    async def execute_raw(self, query, *params):
        if "SET output_tpm_limit = $1" in query:
            rows = self.users if "deltallm_usertable" in query else self.keys
            rows[params[1]]["output_tpm_limit"] = params[0]
            return 1
        return await super().execute_raw(query, *params)


class _UserDB(_OutputMutationDB, _FakeScopeDB):
    def __init__(self):
        super().__init__()
        self.invalidations = []
        self.fail_enqueue = False


class _KeyDB(_OutputMutationDB, _FakeKeyDB):
    def __init__(self):
        super().__init__(
            {"key-hash-1": {"token": "key-hash-1", "key_name": "key", "team_id": "team-1"}},
            teams={"team-1": {"team_id": "team-1", "organization_id": "org-1"}},
        )
        self.invalidations = []
        self.fail_enqueue = False

    async def query_raw(self, query, *params):
        if "FROM deltallm_verificationtoken" in query and "WHERE token = $1" in query:
            row = self.keys.get(params[0])
            return [dict(row)] if row else []
        return await super().query_raw(query, *params)

    async def execute_raw(self, query, *params):
        if "UPDATE deltallm_verificationtoken" in query and "SET key_name = $1" in query:
            self.keys[params[12]].update(
                dict(
                    zip(
                        (
                            "key_name",
                            "user_id",
                            "team_id",
                            "owner_account_id",
                            "owner_service_account_id",
                            "max_budget",
                            "rpm_limit",
                            "tpm_limit",
                            "rph_limit",
                            "rpd_limit",
                            "tpd_limit",
                            "expires",
                        ),
                        params[:12],
                        strict=True,
                    )
                )
            )
            return 1
        return await super().execute_raw(query, *params)


@pytest.mark.parametrize("value", [100, None], ids=["set", "clear"])
@pytest.mark.parametrize("scope", ["user", "key", "organization"])
async def test_policy_mutation_queues_recovery_and_rolls_back_if_enqueue_fails(
    client, test_app, scope, value, caplog
):
    db = {"user": _UserDB, "key": _KeyDB, "organization": OutputAdminDB}[scope]()
    identity = {"user": "user-1", "key": "key-hash-1", "organization": "org-1"}[scope]
    rows = {
        "user": getattr(db, "users", {}),
        "key": getattr(db, "keys", {}),
        "organization": getattr(db, "organizations", {}),
    }[scope]
    test_app.state.prisma_manager = SimpleNamespace(client=db)
    test_app.state.key_service.repository.prisma = db
    test_app.state.settings.master_key = "mk-test"
    test_app.state.limit_counter.degraded_mode = "fail_closed"
    test_app.state.app_config.general_settings.enable_jwt_auth = False
    test_app.state.app_config.general_settings.custom_auth = None
    headers = {"Authorization": "Bearer mk-test"}
    if scope == "organization":
        created = await client.post(
            "/ui/api/organizations", headers=headers, json={"organization_id": identity}
        )
        assert created.status_code == 200, created.text
    rows[identity]["output_tpm_limit"] = 33
    rows[identity]["rpm_limit"] = 40
    method = {
        "user": "invalidate_keys_for_user",
        "key": "invalidate_key_cache_by_hash",
        "organization": "invalidate_keys_for_org",
    }[scope]
    invalidation = AsyncMock(return_value=0)
    setattr(test_app.state.key_service, method, invalidation)

    async def update(payload):
        if scope == "organization":
            return await client.post(
                "/ui/api/organizations",
                headers=headers,
                json={"organization_id": identity, **payload},
            )
        path = "users" if scope == "user" else "keys"
        return await client.put(f"/ui/api/{path}/{identity}", headers=headers, json=payload)

    omitted = await update({})
    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["output_tpm_limit"] == 33
    assert not db.invalidations
    invalidation.reset_mock()
    invalidation.side_effect = RuntimeError("Redis invalidation failed")
    if value is None:
        test_app.state.key_service.redis = None
        test_app.state.limit_counter.redis = None
    response = await update({"output_tpm_limit": value})
    assert response.status_code == 200, response.text
    assert response.json()["output_tpm_limit"] == value
    assert len(db.invalidations) == 1

    pending = db.invalidations[0]
    assert (pending["scope_type"], pending["scope_id"], pending["status"]) == (
        "key_hash" if scope == "key" else scope,
        identity,
        "pending",
    )
    assert "output_tpm_policy_invalidation_queued" in caplog.text
    if value is not None:
        invalidation.assert_awaited_once_with(identity)
    else:
        invalidation.assert_not_awaited()

    db.fail_enqueue = True
    failed = await update({"output_tpm_limit": value, "rpm_limit": 500})
    assert failed.status_code == 503, failed.text
    assert "could not be scheduled" in failed.text
    saved_rows = {
        "user": getattr(db, "users", {}),
        "key": getattr(db, "keys", {}),
        "organization": getattr(db, "organizations", {}),
    }[scope]
    assert saved_rows[identity]["output_tpm_limit"] == value
    assert saved_rows[identity]["rpm_limit"] == response.json()["rpm_limit"]
    assert len(db.invalidations) == 1


@pytest.mark.parametrize("scope", ["key", "user", "team", "organization"])
async def test_immediate_policy_invalidation_is_bounded_and_cancels_work(scope, monkeypatch):
    from src.api.admin import output_policy

    timeout = asyncio.timeout
    limits = []

    def short_timeout(delay):
        limits.append(delay)
        return timeout(0.01)

    monkeypatch.setattr(output_policy.asyncio, "timeout", short_timeout)
    cancelled = asyncio.Event()

    async def stalled(_identity):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    service = SimpleNamespace(
        require_cache_invalidation_backend=lambda **kwargs: None,
        invalidate_key_cache_by_hash=stalled,
        invalidate_keys_for_user=stalled,
        invalidate_keys_for_team=stalled,
        invalidate_keys_for_org=stalled,
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(key_service=service)))
    await invalidate_output_policy_now(request, scope=scope, identity="bounded")
    assert limits == [0.5]
    assert cancelled.is_set()
