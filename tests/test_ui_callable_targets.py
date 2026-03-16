from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.db.callable_targets import CallableTargetBindingRecord
from src.db.callable_target_policies import CallableTargetScopePolicyRecord
from src.db.route_groups import RouteGroupBindingRecord, RouteGroupRecord
from src.services.asset_scopes import normalize_scope_type
from src.services.callable_targets import CallableTarget


class _FakeMigrationDB:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.organizations = [
            {"organization_id": "org-1", "organization_name": "Org One"},
        ]
        self.teams = [
            {
                "team_id": "team-1",
                "team_alias": "Team One",
                "organization_id": "org-1",
                "models": ["gpt-4o-mini", "missing-model"],
                "created_at": now,
                "updated_at": now,
            }
        ]
        self.keys = [
            {
                "token": "key-1",
                "key_name": "Key One",
                "team_id": "team-1",
                "organization_id": "org-1",
                "models": ["support-fast"],
            }
        ]
        self.users = [
            {
                "user_id": "user-1",
                "user_email": "user-1@example.com",
                "team_id": "team-1",
                "organization_id": "org-1",
                "models": ["gpt-4o-mini"],
            }
        ]

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        if "FROM deltallm_organizationtable" in query:
            if params:
                org_id = str(params[0])
                return [row for row in self.organizations if row["organization_id"] == org_id]
            return list(self.organizations)
        if "FROM deltallm_teamtable" in query and "SELECT team_id, team_alias, organization_id, models" in query:
            if params:
                org_id = str(params[0])
                return [row for row in self.teams if row["organization_id"] == org_id]
            return list(self.teams)
        if "FROM deltallm_verificationtoken vt" in query and "JOIN deltallm_teamtable t ON vt.team_id = t.team_id" in query:
            if params:
                org_id = str(params[0])
                return [row for row in self.keys if row["organization_id"] == org_id]
            return list(self.keys)
        if "FROM deltallm_usertable u" in query and "COALESCE(u.team_id, vt.team_id) AS team_id" in query:
            if params:
                org_id = str(params[0])
                return [row for row in self.users if row["organization_id"] == org_id]
            return list(self.users)
        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        if "UPDATE deltallm_teamtable" in query and "SET models = ARRAY[]::text[]" in query:
            team_id = str(params[0])
            for row in self.teams:
                if row["team_id"] == team_id:
                    row["models"] = []
                    return 1
            return 0
        if "UPDATE deltallm_verificationtoken" in query and "SET models = ARRAY[]::text[]" in query:
            token = str(params[0])
            for row in self.keys:
                if row["token"] == token:
                    row["models"] = []
                    return 1
            return 0
        if "UPDATE deltallm_usertable" in query and "SET models = ARRAY[]::text[]" in query:
            user_id = str(params[0])
            for row in self.users:
                if row["user_id"] == user_id:
                    row["models"] = []
                    return 1
            return 0
        return 0


class _FakeReadyMigrationDB(_FakeMigrationDB):
    def __init__(self) -> None:
        super().__init__()
        self.teams[0]["models"] = ["gpt-4o-mini"]


class _FakeMultiOrgMigrationDB(_FakeMigrationDB):
    def __init__(self) -> None:
        super().__init__()
        now = datetime.now(tz=UTC)
        self.organizations.append({"organization_id": "org-2", "organization_name": "Org Two"})
        self.teams.append(
            {
                "team_id": "team-2",
                "team_alias": "Team Two",
                "organization_id": "org-2",
                "models": ["gpt-4o-mini"],
                "created_at": now,
                "updated_at": now,
            }
        )
        self.keys.append(
            {
                "token": "key-2",
                "key_name": "Key Two",
                "team_id": "team-2",
                "organization_id": "org-2",
                "models": ["support-fast"],
            }
        )
        self.users.append(
            {
                "user_id": "user-2",
                "user_email": "user-2@example.com",
                "team_id": "team-2",
                "organization_id": "org-2",
                "models": ["gpt-4o-mini"],
            }
        )


class _FakeCallableTargetBindingRepository:
    def __init__(self) -> None:
        self.bindings: list[CallableTargetBindingRecord] = []
        self._counter = 0

    async def list_bindings(self, *, callable_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if callable_key:
            items = [item for item in items if item.callable_key == callable_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)

    async def upsert_binding(self, *, callable_key, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        normalized_scope_type = normalize_scope_type(scope_type)
        for index, item in enumerate(self.bindings):
            if item.callable_key == callable_key and item.scope_type == normalized_scope_type and item.scope_id == scope_id:
                updated = replace(item, enabled=enabled, metadata=metadata)
                self.bindings[index] = updated
                return updated
        self._counter += 1
        record = CallableTargetBindingRecord(
            callable_target_binding_id=f"ctb-{self._counter}",
            callable_key=callable_key,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        self.bindings.append(record)
        return record

    async def get_binding(self, binding_id: str):  # noqa: ANN201
        return next((item for item in self.bindings if item.callable_target_binding_id == binding_id), None)

    async def delete_binding(self, binding_id: str) -> bool:
        kept = [item for item in self.bindings if item.callable_target_binding_id != binding_id]
        if len(kept) == len(self.bindings):
            return False
        self.bindings = kept
        return True


class _FakeCallableTargetScopePolicyRepository:
    def __init__(self) -> None:
        self.policies: list[CallableTargetScopePolicyRecord] = []
        self._counter = 0

    async def list_policies(self, *, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.policies)
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)

    async def upsert_policy(self, *, scope_type, scope_id, mode, metadata):  # noqa: ANN001, ANN201
        normalized_scope_type = normalize_scope_type(scope_type)
        for index, item in enumerate(self.policies):
            if item.scope_type == normalized_scope_type and item.scope_id == scope_id:
                updated = replace(item, mode=mode, metadata=metadata)
                self.policies[index] = updated
                return updated
        self._counter += 1
        record = CallableTargetScopePolicyRecord(
            callable_target_scope_policy_id=f"ctp-{self._counter}",
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            mode=mode,
            metadata=metadata,
        )
        self.policies.append(record)
        return record

    async def get_policy(self, policy_id: str):  # noqa: ANN201
        return next((item for item in self.policies if item.callable_target_scope_policy_id == policy_id), None)

    async def delete_policy(self, policy_id: str) -> bool:
        kept = [item for item in self.policies if item.callable_target_scope_policy_id != policy_id]
        if len(kept) == len(self.policies):
            return False
        self.policies = kept
        return True


class _FakeRouteGroupRepository:
    def __init__(self) -> None:
        self.groups: dict[str, RouteGroupRecord] = {}
        self.bindings: list[RouteGroupBindingRecord] = []
        self._group_counter = 0
        self._binding_counter = 0

    async def create_group(self, *, group_key, name, mode, routing_strategy, enabled, metadata):  # noqa: ANN001, ANN201
        self._group_counter += 1
        record = RouteGroupRecord(
            route_group_id=f"rg-{self._group_counter}",
            group_key=group_key,
            name=name,
            mode=mode,
            routing_strategy=routing_strategy,
            enabled=enabled,
            metadata=metadata,
            owner_scope_type="global",
            owner_scope_id=None,
        )
        self.groups[group_key] = record
        return record

    async def get_group(self, group_key: str):  # noqa: ANN201
        return self.groups.get(group_key)

    async def list_bindings(self, *, group_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if group_key:
            items = [item for item in items if item.group_key == group_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        return items[offset : offset + limit], len(items)

    async def upsert_binding(self, group_key: str, *, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        group = self.groups.get(group_key)
        if group is None:
            return None
        normalized_scope_type = normalize_scope_type(scope_type)
        for index, item in enumerate(self.bindings):
            if item.group_key == group_key and item.scope_type == normalized_scope_type and item.scope_id == scope_id:
                updated = replace(item, enabled=enabled, metadata=metadata)
                self.bindings[index] = updated
                return updated
        self._binding_counter += 1
        record = RouteGroupBindingRecord(
            route_group_binding_id=f"rgb-{self._binding_counter}",
            route_group_id=group.route_group_id,
            group_key=group_key,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        self.bindings.append(record)
        return record

    async def delete_binding(self, binding_id: str) -> bool:
        kept = [item for item in self.bindings if item.route_group_binding_id != binding_id]
        if len(kept) == len(self.bindings):
            return False
        self.bindings = kept
        return True


class _ReloadTracker:
    def __init__(self) -> None:
        self.reload_count = 0

    async def reload(self) -> None:
        self.reload_count += 1


@pytest.mark.asyncio
async def test_list_callable_targets_uses_runtime_catalog(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-fast": CallableTarget(key="support-fast", target_type="route_group"),
    }

    response = await client.get("/ui/api/callable-targets", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] == 2
    assert {item["callable_key"] for item in payload["data"]} == {"gpt-4o-mini", "support-fast"}


@pytest.mark.asyncio
async def test_callable_target_binding_admin_lifecycle(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakeCallableTargetBindingRepository()
    policy_repo = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_binding_repository = repo
    test_app.state.callable_target_scope_policy_repository = policy_repo
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-fast": CallableTarget(key="support-fast", target_type="route_group"),
    }
    headers = {"Authorization": "Bearer mk-test"}

    upsert = await client.post(
        "/ui/api/callable-target-bindings",
        headers=headers,
        json={
            "callable_key": "support-fast",
            "scope_type": "org",
            "scope_id": "org-1",
            "enabled": True,
            "metadata": {"source": "bootstrap"},
        },
    )
    assert upsert.status_code == 200
    payload = upsert.json()
    assert payload["callable_key"] == "support-fast"
    assert payload["scope_type"] == "organization"

    detail = await client.get("/ui/api/callable-targets/support-fast", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["target"]["target_type"] == "route_group"
    assert detail.json()["bindings"][0]["scope_id"] == "org-1"

    listing = await client.get(
        "/ui/api/callable-target-bindings",
        headers=headers,
        params={"callable_key": "support-fast", "scope_type": "organization", "scope_id": "org-1"},
    )
    assert listing.status_code == 200
    assert listing.json()["pagination"]["total"] == 1

    delete = await client.delete(
        f"/ui/api/callable-target-bindings/{payload['callable_target_binding_id']}",
        headers=headers,
    )
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True


@pytest.mark.asyncio
async def test_callable_target_route_group_binding_mirrors_route_group_bindings(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakeCallableTargetBindingRepository()
    policy_repo = _FakeCallableTargetScopePolicyRepository()
    route_group_repo = _FakeRouteGroupRepository()
    await route_group_repo.create_group(
        group_key="support-fast",
        name="Support Fast",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = repo
    test_app.state.callable_target_scope_policy_repository = policy_repo
    test_app.state.route_group_repository = route_group_repo
    test_app.state.callable_target_catalog = {
        "support-fast": CallableTarget(key="support-fast", target_type="route_group"),
    }
    headers = {"Authorization": "Bearer mk-test"}

    upsert = await client.post(
        "/ui/api/callable-target-bindings",
        headers=headers,
        json={
            "callable_key": "support-fast",
            "scope_type": "organization",
            "scope_id": "org-1",
            "enabled": True,
        },
    )

    assert upsert.status_code == 200
    assert {(binding.group_key, binding.scope_type, binding.scope_id) for binding in route_group_repo.bindings} == {
        ("support-fast", "organization", "org-1")
    }

    delete = await client.delete(
        f"/ui/api/callable-target-bindings/{upsert.json()['callable_target_binding_id']}",
        headers=headers,
    )

    assert delete.status_code == 200
    assert route_group_repo.bindings == []


@pytest.mark.asyncio
async def test_callable_target_binding_rejects_unknown_target(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
    }

    response = await client.post(
        "/ui/api/callable-target-bindings",
        headers={"Authorization": "Bearer mk-test"},
        json={"callable_key": "missing-target", "scope_type": "organization", "scope_id": "org-1"},
    )

    assert response.status_code == 404
    assert "Callable target not found" in response.text


@pytest.mark.asyncio
async def test_callable_target_scope_policy_admin_lifecycle(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    repo = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_scope_policy_repository = repo
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
    }
    headers = {"Authorization": "Bearer mk-test"}

    upsert = await client.post(
        "/ui/api/callable-target-scope-policies",
        headers=headers,
        json={
            "scope_type": "team",
            "scope_id": "team-1",
            "mode": "restrict",
            "metadata": {"source": "pilot"},
        },
    )
    assert upsert.status_code == 200
    payload = upsert.json()
    assert payload["scope_type"] == "team"
    assert payload["mode"] == "restrict"

    listing = await client.get(
        "/ui/api/callable-target-scope-policies",
        headers=headers,
        params={"scope_type": "team", "scope_id": "team-1"},
    )
    assert listing.status_code == 200
    assert listing.json()["pagination"]["total"] == 1

    delete = await client.delete(
        f"/ui/api/callable-target-scope-policies/{payload['callable_target_scope_policy_id']}",
        headers=headers,
    )
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True


@pytest.mark.asyncio
async def test_callable_target_migration_report_summarizes_legacy_scope_data(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeMigrationDB()})()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-fast": CallableTarget(key="support-fast", target_type="route_group"),
    }

    response = await client.get(
        "/ui/api/callable-target-migration/report",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["organizations_needing_bootstrap"] == 1
    assert payload["summary"]["teams_with_legacy_models"] == 1
    assert payload["summary"]["api_keys_with_legacy_models"] == 1
    assert payload["summary"]["users_with_legacy_models"] == 1
    assert payload["summary"]["missing_callable_keys_total"] == 1
    assert payload["summary"]["organizations_by_rollout_state"] == {"missing_catalog_keys": 1}
    assert payload["summary"]["organization_ids_by_rollout_state"] == {"missing_catalog_keys": ["org-1"]}
    assert payload["filters"]["rollout_states"] == []

    organization = payload["organizations"][0]
    assert organization["rollout_state"] == "missing_catalog_keys"
    assert organization["will_bootstrap_org_bindings"] is True
    assert organization["bootstrap_callable_keys"] == ["gpt-4o-mini", "support-fast"]
    assert organization["teams"][0]["valid_callable_keys"] == ["gpt-4o-mini"]
    assert organization["teams"][0]["missing_callable_keys"] == ["missing-model"]
    assert organization["teams"][0]["rollout_state"] == "missing_catalog_keys"
    assert organization["api_keys"][0]["valid_callable_keys"] == ["support-fast"]
    assert organization["api_keys"][0]["rollout_state"] == "needs_scope_backfill"
    assert organization["users"][0]["valid_callable_keys"] == ["gpt-4o-mini"]
    assert organization["users"][0]["rollout_state"] == "needs_scope_backfill"


@pytest.mark.asyncio
async def test_callable_target_migration_backfill_creates_bindings_and_policies(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeMigrationDB()})()
    binding_repo = _FakeCallableTargetBindingRepository()
    policy_repo = _FakeCallableTargetScopePolicyRepository()
    reload_tracker = _ReloadTracker()
    test_app.state.callable_target_binding_repository = binding_repo
    test_app.state.callable_target_scope_policy_repository = policy_repo
    test_app.state.callable_target_grant_service = reload_tracker
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-fast": CallableTarget(key="support-fast", target_type="route_group"),
    }

    response = await client.post(
        "/ui/api/callable-target-migration/backfill",
        headers={"Authorization": "Bearer mk-test"},
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["applied"]["organization_bindings_upserted"] == 2
    assert payload["applied"]["team_bindings_upserted"] == 1
    assert payload["applied"]["api_key_bindings_upserted"] == 1
    assert payload["applied"]["user_bindings_upserted"] == 1
    assert payload["applied"]["team_policies_upserted"] == 1
    assert payload["applied"]["api_key_policies_upserted"] == 1
    assert payload["applied"]["team_legacy_models_cleared"] == 0
    assert payload["applied"]["api_key_legacy_models_cleared"] == 1
    assert payload["applied"]["user_legacy_models_cleared"] == 1
    assert payload["applied"]["route_group_bindings_mirrored"] == 0
    assert reload_tracker.reload_count == 1

    org_bindings = {(item.scope_type, item.scope_id, item.callable_key) for item in binding_repo.bindings}
    assert ("organization", "org-1", "gpt-4o-mini") in org_bindings
    assert ("organization", "org-1", "support-fast") in org_bindings
    assert ("team", "team-1", "gpt-4o-mini") in org_bindings
    assert ("api_key", "key-1", "support-fast") in org_bindings
    assert ("user", "user-1", "gpt-4o-mini") in org_bindings

    policies = {(item.scope_type, item.scope_id, item.mode) for item in policy_repo.policies}
    assert ("team", "team-1", "restrict") in policies
    assert ("api_key", "key-1", "restrict") in policies

    report = await client.get(
        "/ui/api/callable-target-migration/report",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert report.status_code == 200
    report_payload = report.json()
    assert report_payload["summary"]["teams_with_legacy_models"] == 1
    assert report_payload["summary"]["api_keys_with_legacy_models"] == 0
    assert report_payload["summary"]["users_with_legacy_models"] == 0
    assert report_payload["summary"]["organizations_by_rollout_state"] == {"missing_catalog_keys": 1}
    assert payload["summary"]["organizations_by_rollout_state"] == {"missing_catalog_keys": 1}
    assert report_payload["organizations"][0]["api_keys"][0]["rollout_state"] == "ready_for_enforce"
    assert report_payload["organizations"][0]["users"][0]["rollout_state"] == "ready_for_enforce"


@pytest.mark.asyncio
async def test_callable_target_migration_backfill_returns_ready_for_enforce_when_catalog_is_complete(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeReadyMigrationDB()})()
    binding_repo = _FakeCallableTargetBindingRepository()
    policy_repo = _FakeCallableTargetScopePolicyRepository()
    reload_tracker = _ReloadTracker()
    test_app.state.callable_target_binding_repository = binding_repo
    test_app.state.callable_target_scope_policy_repository = policy_repo
    test_app.state.callable_target_grant_service = reload_tracker
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-fast": CallableTarget(key="support-fast", target_type="route_group"),
    }

    response = await client.post(
        "/ui/api/callable-target-migration/backfill",
        headers={"Authorization": "Bearer mk-test"},
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["organizations_by_rollout_state"] == {"ready_for_enforce": 1}
    assert payload["organizations"][0]["rollout_state"] == "ready_for_enforce"
    assert payload["organizations"][0]["teams"][0]["rollout_state"] == "ready_for_enforce"
    assert payload["organizations"][0]["api_keys"][0]["rollout_state"] == "ready_for_enforce"
    assert payload["organizations"][0]["users"][0]["rollout_state"] == "ready_for_enforce"
    assert payload["applied"]["user_bindings_upserted"] == 1
    assert payload["applied"]["team_legacy_models_cleared"] == 1
    assert payload["applied"]["api_key_legacy_models_cleared"] == 1
    assert payload["applied"]["user_legacy_models_cleared"] == 1
    assert reload_tracker.reload_count == 1


@pytest.mark.asyncio
async def test_callable_target_migration_backfill_mirrors_existing_route_group_bindings(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeReadyMigrationDB()})()
    binding_repo = _FakeCallableTargetBindingRepository()
    policy_repo = _FakeCallableTargetScopePolicyRepository()
    route_group_repo = _FakeRouteGroupRepository()
    await route_group_repo.create_group(
        group_key="legacy-route",
        name="Legacy Route",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    await route_group_repo.upsert_binding(
        "legacy-route",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata={"source": "legacy-route-group-binding"},
    )
    test_app.state.callable_target_binding_repository = binding_repo
    test_app.state.callable_target_scope_policy_repository = policy_repo
    test_app.state.route_group_repository = route_group_repo
    test_app.state.callable_target_grant_service = _ReloadTracker()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-fast": CallableTarget(key="support-fast", target_type="route_group"),
    }

    response = await client.post(
        "/ui/api/callable-target-migration/backfill",
        headers={"Authorization": "Bearer mk-test"},
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["applied"]["route_group_bindings_mirrored"] == 1
    assert ("organization", "org-1", "legacy-route") in {
        (item.scope_type, item.scope_id, item.callable_key) for item in binding_repo.bindings
    }


@pytest.mark.asyncio
async def test_callable_target_migration_filter_targets_actionable_orgs_only(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeMultiOrgMigrationDB()})()
    binding_repo = _FakeCallableTargetBindingRepository()
    policy_repo = _FakeCallableTargetScopePolicyRepository()
    reload_tracker = _ReloadTracker()
    test_app.state.callable_target_binding_repository = binding_repo
    test_app.state.callable_target_scope_policy_repository = policy_repo
    test_app.state.callable_target_grant_service = reload_tracker
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-fast": CallableTarget(key="support-fast", target_type="route_group"),
    }
    headers = {"Authorization": "Bearer mk-test"}

    report = await client.get(
        "/ui/api/callable-target-migration/report",
        headers=headers,
        params=[("rollout_state", "needs_org_bootstrap")],
    )

    assert report.status_code == 200
    report_payload = report.json()
    assert report_payload["filters"]["rollout_states"] == ["needs_org_bootstrap"]
    assert report_payload["summary"]["organizations_total"] == 1
    assert report_payload["summary"]["organizations_by_rollout_state"] == {"needs_org_bootstrap": 1}
    assert report_payload["summary"]["organization_ids_by_rollout_state"] == {"needs_org_bootstrap": ["org-2"]}
    assert [org["organization_id"] for org in report_payload["organizations"]] == ["org-2"]

    alias_report = await client.get(
        "/ui/api/callable-target-migration/report",
        headers=headers,
        params=[("rollout_state", "ready_for_shadow")],
    )

    assert alias_report.status_code == 200
    assert alias_report.json()["filters"]["rollout_states"] == ["ready_for_enforce"]

    backfill = await client.post(
        "/ui/api/callable-target-migration/backfill",
        headers=headers,
        json={"rollout_states": ["needs_org_bootstrap"]},
    )

    assert backfill.status_code == 200
    payload = backfill.json()
    assert payload["filters"]["rollout_states"] == ["needs_org_bootstrap"]
    assert payload["applied"]["organization_bindings_upserted"] == 2
    assert payload["applied"]["team_bindings_upserted"] == 1
    assert payload["applied"]["api_key_bindings_upserted"] == 1
    assert payload["applied"]["user_bindings_upserted"] == 1
    assert payload["applied"]["team_policies_upserted"] == 1
    assert payload["applied"]["api_key_policies_upserted"] == 1
    assert payload["applied"]["route_group_bindings_mirrored"] == 0
    assert payload["summary"]["organizations_total"] == 0
    assert reload_tracker.reload_count == 1

    org_bindings = {(item.scope_type, item.scope_id, item.callable_key) for item in binding_repo.bindings}
    assert ("organization", "org-2", "gpt-4o-mini") in org_bindings
    assert ("organization", "org-2", "support-fast") in org_bindings
    assert ("team", "team-2", "gpt-4o-mini") in org_bindings
    assert ("api_key", "key-2", "support-fast") in org_bindings
    assert ("user", "user-2", "gpt-4o-mini") in org_bindings
    assert ("organization", "org-1", "gpt-4o-mini") not in org_bindings
    assert ("team", "team-1", "gpt-4o-mini") not in org_bindings
    assert ("user", "user-1", "gpt-4o-mini") not in org_bindings
