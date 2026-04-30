from __future__ import annotations

from dataclasses import replace

import pytest

from src.db.callable_target_access_groups import (
    CallableTargetAccessGroupBindingCount,
    CallableTargetAccessGroupBindingRecord,
)
from src.db.callable_target_policies import CallableTargetScopePolicyRecord
from src.db.callable_targets import CallableTargetBindingRecord
from src.db.route_groups import RouteGroupBindingRecord, RouteGroupRecord
from src.governance.access_groups import normalize_access_group_key
from src.services.callable_targets import CallableTarget


class _FakeScopeDB:
    def __init__(self) -> None:
        self.organizations = {
            "org-1": {"organization_id": "org-1", "metadata": None},
        }
        self.teams = {
            "team-1": {
                "team_id": "team-1",
                "team_alias": "Team One",
                "organization_id": "org-1",
                "max_budget": None,
                "spend": 0.0,
                "rpm_limit": None,
                "tpm_limit": None,
                "blocked": False,
                "created_at": None,
                "updated_at": None,
            }
        }
        self.keys = {
            "key-1": {
                "token": "key-1",
                "user_id": None,
                "team_id": "team-1",
                "organization_id": "org-1",
            }
        }

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        if "FROM deltallm_organizationtable" in query and "WHERE organization_id = $1" in query:
            row = self.organizations.get(str(params[0]))
            return [row] if row else []
        if "FROM deltallm_organizationtable" in query:
            return list(self.organizations.values())
        if "FROM deltallm_teamtable" in query and "WHERE team_id = $1" in query:
            row = self.teams.get(str(params[0]))
            return [row] if row else []
        if "FROM deltallm_verificationtoken vt" in query and "WHERE vt.token = $1" in query:
            row = self.keys.get(str(params[0]))
            return [row] if row else []
        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        if "UPDATE deltallm_organizationtable" in query and "metadata = $2::jsonb" in query:
            row = self.organizations.get(str(params[0]))
            if row is None:
                return 0
            row["metadata"] = params[1]
            return 1
        return 0

    def tx(self):  # noqa: ANN201
        db = self

        class _TxContext:
            async def __aenter__(self_inner):  # noqa: ANN202
                return db

            async def __aexit__(self_inner, exc_type, exc, tb):  # noqa: ANN202
                return False

        return _TxContext()


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
        page = items[offset : offset + limit]
        return page, len(items)

    async def upsert_binding(self, *, callable_key, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        for index, item in enumerate(self.bindings):
            if item.callable_key == callable_key and item.scope_type == scope_type and item.scope_id == scope_id:
                updated = replace(item, enabled=enabled, metadata=metadata)
                self.bindings[index] = updated
                return updated
        self._counter += 1
        record = CallableTargetBindingRecord(
            callable_target_binding_id=f"ctb-{self._counter}",
            callable_key=callable_key,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        self.bindings.append(record)
        return record

    async def delete_binding(self, binding_id: str) -> bool:
        kept = [item for item in self.bindings if item.callable_target_binding_id != binding_id]
        if len(kept) == len(self.bindings):
            return False
        self.bindings = kept
        return True


class _FakeCallableTargetAccessGroupBindingRepository:
    def __init__(self) -> None:
        self.bindings: list[CallableTargetAccessGroupBindingRecord] = []
        self._counter = 0
        self.unfiltered_list_calls = 0
        self.group_count_calls = 0

    async def list_bindings(self, *, group_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        if group_key is None and scope_type is None and scope_id is None:
            self.unfiltered_list_calls += 1
        items = list(self.bindings)
        if group_key:
            normalized_group_key = normalize_access_group_key(group_key)
            items = [item for item in items if item.group_key == normalized_group_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        page = items[offset : offset + limit]
        return page, len(items)

    async def list_group_binding_counts(self, *, search=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        self.group_count_calls += 1
        query = str(search or "").strip().lower()
        counts: dict[str, int] = {}
        for item in self.bindings:
            if query and query not in item.group_key.lower():
                continue
            counts[item.group_key] = counts.get(item.group_key, 0) + 1
        rows = [
            CallableTargetAccessGroupBindingCount(group_key=group_key, binding_count=count)
            for group_key, count in sorted(counts.items())
        ]
        return rows[offset : offset + limit], len(rows)

    async def upsert_binding(self, *, group_key, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        normalized_group_key = normalize_access_group_key(group_key, strict=True)
        for index, item in enumerate(self.bindings):
            if item.group_key == normalized_group_key and item.scope_type == scope_type and item.scope_id == scope_id:
                updated = replace(item, enabled=enabled, metadata=metadata)
                self.bindings[index] = updated
                return updated
        self._counter += 1
        record = CallableTargetAccessGroupBindingRecord(
            callable_target_access_group_binding_id=f"ctagb-{self._counter}",
            group_key=normalized_group_key,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        self.bindings.append(record)
        return record

    async def delete_binding(self, binding_id: str) -> bool:
        kept = [item for item in self.bindings if item.callable_target_access_group_binding_id != binding_id]
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
        page = items[offset : offset + limit]
        return page, len(items)

    async def upsert_policy(self, *, scope_type, scope_id, mode, metadata):  # noqa: ANN001, ANN201
        for index, item in enumerate(self.policies):
            if item.scope_type == scope_type and item.scope_id == scope_id:
                updated = replace(item, mode=mode, metadata=metadata)
                self.policies[index] = updated
                return updated
        self._counter += 1
        record = CallableTargetScopePolicyRecord(
            callable_target_scope_policy_id=f"ctp-{self._counter}",
            scope_type=scope_type,
            scope_id=scope_id,
            mode=mode,
            metadata=metadata,
        )
        self.policies.append(record)
        return record

    async def delete_policy(self, policy_id: str) -> bool:
        kept = [item for item in self.policies if item.callable_target_scope_policy_id != policy_id]
        if len(kept) == len(self.policies):
            return False
        self.policies = kept
        return True


class _FakeGrantService:
    def __init__(self) -> None:
        self.reloads = 0

    async def reload(self) -> None:
        self.reloads += 1


class _SharedRouteGroupState:
    def __init__(self) -> None:
        self.groups = {
            "support-chat": RouteGroupRecord(
                route_group_id="rg-1",
                group_key="support-chat",
                name="Support Chat",
                mode="chat",
                routing_strategy="weighted",
                enabled=True,
                metadata=None,
                owner_scope_type="global",
                owner_scope_id=None,
            )
        }
        self.bindings: list[RouteGroupBindingRecord] = []


class _CountingRouteGroupRepository:
    def __init__(self, shared: _SharedRouteGroupState) -> None:
        self.shared = shared
        self.upsert_calls = 0
        self.delete_calls = 0

    async def get_group(self, group_key: str):  # noqa: ANN201
        return self.shared.groups.get(group_key)

    async def list_groups(self, *, limit=100, offset=0):  # noqa: ANN001, ANN201
        items = list(self.shared.groups.values())[offset : offset + limit]
        return items, len(self.shared.groups)

    async def list_bindings(self, *, group_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.shared.bindings)
        if group_key:
            items = [item for item in items if item.group_key == group_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        page = items[offset : offset + limit]
        return page, len(items)

    async def upsert_binding(self, group_key: str, *, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        self.upsert_calls += 1
        for index, item in enumerate(self.shared.bindings):
            if item.group_key == group_key and item.scope_type == scope_type and item.scope_id == scope_id:
                self.shared.bindings[index] = replace(item, enabled=enabled, metadata=metadata)
                return self.shared.bindings[index]
        group = self.shared.groups[group_key]
        record = RouteGroupBindingRecord(
            route_group_binding_id=f"rgb-{len(self.shared.bindings) + 1}",
            route_group_id=group.route_group_id,
            group_key=group_key,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        self.shared.bindings.append(record)
        return record

    async def delete_binding(self, binding_id: str) -> bool:
        self.delete_calls += 1
        kept = [item for item in self.shared.bindings if item.route_group_binding_id != binding_id]
        if len(kept) == len(self.shared.bindings):
            return False
        self.shared.bindings = kept
        return True


@pytest.mark.asyncio
async def test_get_organization_asset_access_returns_selected_targets(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group"),
    }

    response = await client.get(
        "/ui/api/organizations/org-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "grant"
    assert payload["auto_follow_catalog"] is False
    assert payload["selected_callable_keys"] == ["gpt-4o-mini"]
    assert payload["summary"]["selectable_total"] == 2
    assert payload["summary"]["effective_total"] == 1


@pytest.mark.asyncio
async def test_get_organization_asset_access_returns_selected_access_groups(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    await access_group_repository.upsert_binding(
        group_key="beta",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = access_group_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group"),
    }

    response = await client.get(
        "/ui/api/organizations/org-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_callable_keys"] == []
    assert payload["selected_access_group_keys"] == ["beta"]
    assert payload["summary"]["selected_access_group_total"] == 1
    assert payload["summary"]["effective_total"] == 1
    access_groups = {item["group_key"]: item for item in payload["selectable_access_groups"]}
    assert access_groups["beta"]["selected"] is True
    assert access_groups["beta"]["callable_keys"] == ["gpt-4o-mini"]
    effective_targets = {item["callable_key"]: item for item in payload["effective_targets"]}
    assert effective_targets["gpt-4o-mini"]["via_access_groups"] == ["beta"]
    assert access_group_repository.group_count_calls == 1
    assert access_group_repository.unfiltered_list_calls == 0


@pytest.mark.asyncio
async def test_get_organization_asset_access_pages_access_groups(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    test_app.state.callable_target_binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_access_group_repository = access_group_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "alpha-model": CallableTarget(key="alpha-model", target_type="model", access_groups=frozenset({"alpha"})),
        "beta-model": CallableTarget(key="beta-model", target_type="model", access_groups=frozenset({"beta"})),
        "gamma-model": CallableTarget(key="gamma-model", target_type="model", access_groups=frozenset({"gamma"})),
    }

    response = await client.get(
        "/ui/api/organizations/org-1/asset-access?access_group_search=a&access_group_limit=1&access_group_offset=1",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["group_key"] for item in payload["selectable_access_groups"]] == ["beta"]
    assert payload["access_group_pagination"] == {
        "total": 3,
        "limit": 1,
        "offset": 1,
        "has_more": True,
    }
    assert payload["summary"]["selectable_access_group_total"] == 3


@pytest.mark.asyncio
async def test_update_organization_asset_access_select_all_enables_auto_follow(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    scope_db = _FakeScopeDB()
    test_app.state.prisma_manager = type("Prisma", (), {"client": scope_db})()
    binding_repository = _FakeCallableTargetBindingRepository()
    grant_service = _FakeGrantService()
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = grant_service
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group"),
    }

    response = await client.put(
        "/ui/api/organizations/org-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "grant", "selected_callable_keys": [], "select_all_selectable": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["auto_follow_catalog"] is True
    assert payload["selected_callable_keys"] == ["gpt-4o-mini", "support-chat"]
    assert scope_db.organizations["org-1"]["metadata"] == {
        "_callable_target_access": {"auto_follow_catalog": True}
    }


@pytest.mark.asyncio
async def test_update_team_asset_access_accepts_parent_visible_access_group(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    policy_repository = _FakeCallableTargetScopePolicyRepository()
    grant_service = _FakeGrantService()
    await access_group_repository.upsert_binding(
        group_key="beta",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = access_group_repository
    test_app.state.callable_target_scope_policy_repository = policy_repository
    test_app.state.callable_target_grant_service = grant_service
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group", access_groups=frozenset({"support"})),
    }

    response = await client.put(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": [], "selected_access_group_keys": ["beta"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "restrict"
    assert payload["selected_access_group_keys"] == ["beta"]
    assert payload["selected_callable_keys"] == []
    assert payload["summary"]["effective_total"] == 1
    assert any(
        item.scope_type == "team" and item.scope_id == "team-1" and item.group_key == "beta"
        for item in access_group_repository.bindings
    )
    assert any(
        item.scope_type == "team" and item.scope_id == "team-1" and item.mode == "restrict"
        for item in policy_repository.policies
    )
    assert grant_service.reloads == 1


@pytest.mark.asyncio
async def test_update_team_asset_access_preserves_access_groups_when_field_is_omitted(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    policy_repository = _FakeCallableTargetScopePolicyRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    await access_group_repository.upsert_binding(
        group_key="beta",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = access_group_repository
    test_app.state.callable_target_scope_policy_repository = policy_repository
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
    }

    response = await client.put(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": ["gpt-4o-mini"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_callable_keys"] == ["gpt-4o-mini"]
    assert payload["selected_access_group_keys"] == ["beta"]
    assert any(
        item.scope_type == "team" and item.scope_id == "team-1" and item.group_key == "beta"
        for item in access_group_repository.bindings
    )


@pytest.mark.asyncio
async def test_update_team_asset_access_clears_access_groups_when_empty_array_is_provided(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    await access_group_repository.upsert_binding(
        group_key="beta",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = access_group_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
    }

    response = await client.put(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": [], "selected_access_group_keys": []},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_access_group_keys"] == []
    assert not any(
        item.scope_type == "team" and item.scope_id == "team-1" and item.group_key == "beta"
        for item in access_group_repository.bindings
    )


@pytest.mark.asyncio
async def test_update_team_asset_access_rejects_explicit_group_sync_without_repository(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    policy_repository = _FakeCallableTargetScopePolicyRepository()
    grant_service = _FakeGrantService()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = None
    test_app.state.callable_target_scope_policy_repository = policy_repository
    test_app.state.callable_target_grant_service = grant_service
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
    }

    response = await client.put(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": ["gpt-4o-mini"], "selected_access_group_keys": []},
    )

    assert response.status_code == 503
    assert "Callable target access group repository unavailable" in response.text
    assert {
        (item.scope_type, item.scope_id, item.callable_key)
        for item in binding_repository.bindings
    } == {("organization", "org-1", "gpt-4o-mini")}
    assert policy_repository.policies == []
    assert grant_service.reloads == 0


@pytest.mark.asyncio
async def test_update_team_asset_access_rejects_null_access_group_array(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
    }

    response = await client.put(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": [], "selected_access_group_keys": None},
    )

    assert response.status_code == 400
    assert "selected_access_group_keys must be an array" in response.text


@pytest.mark.asyncio
async def test_update_key_asset_access_inherit_rejects_missing_group_repository_before_partial_clear(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    policy_repository = _FakeCallableTargetScopePolicyRepository()
    grant_service = _FakeGrantService()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="api_key",
        scope_id="key-1",
        enabled=True,
        metadata=None,
    )
    await policy_repository.upsert_policy(
        scope_type="api_key",
        scope_id="key-1",
        mode="restrict",
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = None
    test_app.state.callable_target_scope_policy_repository = policy_repository
    test_app.state.callable_target_grant_service = grant_service
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
    }

    response = await client.put(
        "/ui/api/keys/key-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "inherit", "selected_callable_keys": []},
    )

    assert response.status_code == 503
    assert "Callable target access group repository unavailable" in response.text
    assert any(
        item.scope_type == "api_key" and item.scope_id == "key-1" and item.callable_key == "gpt-4o-mini"
        for item in binding_repository.bindings
    )
    assert any(
        item.scope_type == "api_key" and item.scope_id == "key-1" and item.mode == "restrict"
        for item in policy_repository.policies
    )
    assert grant_service.reloads == 0


@pytest.mark.asyncio
async def test_update_key_asset_access_rejects_access_group_outside_parent_scope(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group", access_groups=frozenset({"support"})),
    }

    response = await client.put(
        "/ui/api/keys/key-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": [], "selected_access_group_keys": ["support"]},
    )

    assert response.status_code == 400
    assert "Selected access groups are outside the parent scope" in response.text


@pytest.mark.asyncio
async def test_get_team_asset_access_marks_stale_selected_access_group_not_selectable(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    await access_group_repository.upsert_binding(
        group_key="support",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = access_group_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group", access_groups=frozenset({"support"})),
    }

    response = await client.get(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    access_groups = {item["group_key"]: item for item in payload["selectable_access_groups"]}
    assert payload["selected_access_group_keys"] == ["support"]
    assert access_groups["support"]["selected"] is True
    assert access_groups["support"]["selectable"] is False
    assert access_groups["support"]["effective_visible"] is False


@pytest.mark.asyncio
async def test_update_team_asset_access_rejects_stale_selected_access_group(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    await access_group_repository.upsert_binding(
        group_key="support",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = access_group_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group", access_groups=frozenset({"support"})),
    }

    response = await client.put(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": [], "selected_access_group_keys": ["support"]},
    )

    assert response.status_code == 400
    assert "Selected access groups are outside the parent scope" in response.text
    assert any(
        item.scope_type == "team" and item.scope_id == "team-1" and item.group_key == "support"
        for item in access_group_repository.bindings
    )


@pytest.mark.asyncio
async def test_update_organization_asset_access_uses_transaction_route_group_repository(client, test_app, monkeypatch) -> None:
    from src.api.admin.endpoints import organizations as organizations_endpoints

    setattr(test_app.state.settings, "master_key", "mk-test")
    scope_db = _FakeScopeDB()
    shared_route_groups = _SharedRouteGroupState()
    request_route_repo = _CountingRouteGroupRepository(shared_route_groups)
    tx_route_repo = _CountingRouteGroupRepository(shared_route_groups)
    binding_repository = _FakeCallableTargetBindingRepository()
    grant_service = _FakeGrantService()
    test_app.state.prisma_manager = type("Prisma", (), {"client": scope_db})()
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = grant_service
    test_app.state.route_group_repository = request_route_repo
    test_app.state.callable_target_catalog = {
        "support-chat": CallableTarget(key="support-chat", target_type="route_group"),
    }

    def _route_group_repository_for_request(request, *, db_client=None):  # noqa: ANN001, ANN202
        del request
        return tx_route_repo if db_client is not None else request_route_repo

    monkeypatch.setattr(
        organizations_endpoints,
        "_route_group_repository_for_request",
        _route_group_repository_for_request,
    )

    response = await client.put(
        "/ui/api/organizations/org-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "grant", "selected_callable_keys": ["support-chat"]},
    )

    assert response.status_code == 200
    assert tx_route_repo.upsert_calls == 1
    assert request_route_repo.upsert_calls == 0
    assert {
        (binding.group_key, binding.scope_type, binding.scope_id)
        for binding in shared_route_groups.bindings
    } == {("support-chat", "organization", "org-1")}


@pytest.mark.asyncio
async def test_get_team_asset_access_can_skip_target_lists(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
    }

    response = await client.get(
        "/ui/api/teams/team-1/asset-access?include_targets=false",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "inherit"
    assert payload["selectable_targets"] == []
    assert payload["effective_targets"] == []
    assert payload["summary"]["selectable_total"] == 1


@pytest.mark.asyncio
async def test_get_team_asset_access_does_not_scan_all_access_group_bindings_for_selectable_groups(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    await access_group_repository.upsert_binding(
        group_key="beta",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = access_group_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model", access_groups=frozenset({"beta"})),
    }

    response = await client.get(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    assert access_group_repository.unfiltered_list_calls == 0


@pytest.mark.asyncio
async def test_update_team_asset_access_restricts_to_selected_assets(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    policy_repository = _FakeCallableTargetScopePolicyRepository()
    grant_service = _FakeGrantService()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    await binding_repository.upsert_binding(
        callable_key="support-chat",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    test_app.state.callable_target_scope_policy_repository = policy_repository
    test_app.state.callable_target_grant_service = grant_service
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group"),
    }

    response = await client.put(
        "/ui/api/teams/team-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": ["support-chat"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "restrict"
    assert payload["selected_callable_keys"] == ["support-chat"]
    assert payload["summary"]["effective_total"] == 1
    assert any(
        item.scope_type == "team" and item.scope_id == "team-1" and item.callable_key == "support-chat"
        for item in binding_repository.bindings
    )
    assert any(
        item.scope_type == "team" and item.scope_id == "team-1" and item.mode == "restrict"
        for item in policy_repository.policies
    )
    assert grant_service.reloads == 1


@pytest.mark.asyncio
async def test_update_organization_asset_access_can_select_all_current_assets(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group"),
    }

    response = await client.put(
        "/ui/api/organizations/org-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "grant", "selected_callable_keys": [], "select_all_selectable": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["selected_total"] == 2
    assert payload["selected_callable_keys"] == ["gpt-4o-mini", "support-chat"]
    assert {item.callable_key for item in binding_repository.bindings} == {"gpt-4o-mini", "support-chat"}


@pytest.mark.asyncio
async def test_update_key_asset_access_inherit_clears_existing_state(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    policy_repository = _FakeCallableTargetScopePolicyRepository()
    grant_service = _FakeGrantService()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="api_key",
        scope_id="key-1",
        enabled=True,
        metadata=None,
    )
    await policy_repository.upsert_policy(
        scope_type="api_key",
        scope_id="key-1",
        mode="restrict",
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_access_group_repository = _FakeCallableTargetAccessGroupBindingRepository()
    test_app.state.callable_target_scope_policy_repository = policy_repository
    test_app.state.callable_target_grant_service = grant_service
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
    }

    response = await client.put(
        "/ui/api/keys/key-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "inherit", "selected_callable_keys": []},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "inherit"
    assert payload["selected_callable_keys"] == []
    assert not any(item.scope_type == "api_key" and item.scope_id == "key-1" for item in binding_repository.bindings)
    assert not any(item.scope_type == "api_key" and item.scope_id == "key-1" for item in policy_repository.policies)
    assert grant_service.reloads == 1


@pytest.mark.asyncio
async def test_update_key_asset_access_rejects_targets_outside_parent_scope(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    binding_repository = _FakeCallableTargetBindingRepository()
    await binding_repository.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata=None,
    )
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_scope_policy_repository = _FakeCallableTargetScopePolicyRepository()
    test_app.state.callable_target_grant_service = _FakeGrantService()
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "support-chat": CallableTarget(key="support-chat", target_type="route_group"),
    }

    response = await client.put(
        "/ui/api/keys/key-1/asset-access",
        headers={"Authorization": "Bearer mk-test"},
        json={"mode": "restrict", "selected_callable_keys": ["support-chat"]},
    )

    assert response.status_code == 400
    assert "outside the parent scope" in response.text
