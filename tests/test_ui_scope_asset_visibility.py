from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.db.callable_targets import CallableTargetBindingRecord
from src.db.callable_target_policies import CallableTargetScopePolicyRecord
from src.db.route_groups import RouteGroupBindingRecord, RouteGroupRecord
from src.services.asset_ownership import apply_owner_scope_to_metadata, owner_scope_from_metadata
from src.services.callable_targets import CallableTarget


class _FakeScopeDB:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.organizations = {
            "org-1": {
                "organization_id": "org-1",
                "organization_name": "Org One",
                "max_budget": None,
                "spend": 0.0,
                "rpm_limit": None,
                "tpm_limit": None,
                "audit_content_storage_enabled": False,
                "metadata": {},
                "created_at": now,
                "updated_at": now,
            }
        }
        self.users = {
            "user-1": {
                "user_id": "user-1",
                "team_id": "team-1",
                "organization_id": "org-1",
                "models": ["gpt-4o-mini"],
            }
        }
        self.teams = {
            "team-1": {
                "team_id": "team-1",
                "team_alias": "Team One",
                "organization_id": "org-1",
                "max_budget": None,
                "spend": 0.0,
                "models": [],
                "rpm_limit": None,
                "tpm_limit": None,
                "blocked": False,
                "created_at": datetime.now(tz=UTC),
                "updated_at": datetime.now(tz=UTC),
            }
        }
        self.keys = {
            "key-hash-1": {
                "token": "key-hash-1",
                "user_id": None,
                "team_id": "team-1",
                "organization_id": "org-1",
            }
        }

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        if "FROM deltallm_organizationtable" in query and "WHERE organization_id = $1" in query:
            organization = self.organizations.get(str(params[0]))
            return [organization] if organization else []
        if "FROM deltallm_teamtable" in query and "WHERE team_id = $1" in query:
            team = self.teams.get(str(params[0]))
            return [team] if team else []
        if "FROM deltallm_usertable u" in query and "WHERE u.user_id = $1" in query:
            user = self.users.get(str(params[0]))
            return [user] if user else []
        if "FROM deltallm_verificationtoken vt" in query and "WHERE vt.token = $1" in query:
            key = self.keys.get(str(params[0]))
            return [key] if key else []
        return []


class _FakeRouteGroupRepository:
    def __init__(self) -> None:
        self.groups: dict[str, RouteGroupRecord] = {}
        self.bindings: dict[str, list[RouteGroupBindingRecord]] = {}
        self._group_counter = 0
        self._binding_counter = 0

    async def create_group(self, *, group_key, name, mode, routing_strategy, enabled, metadata):  # noqa: ANN001, ANN201
        self._group_counter += 1
        owner_scope = owner_scope_from_metadata(metadata)
        record = RouteGroupRecord(
            route_group_id=f"rg-{self._group_counter}",
            group_key=group_key,
            name=name,
            mode=mode,
            routing_strategy=routing_strategy,
            enabled=enabled,
            metadata=metadata,
            owner_scope_type=owner_scope.scope_type,
            owner_scope_id=owner_scope.scope_id,
        )
        self.groups[group_key] = record
        return record

    async def list_groups(self, *, search=None, limit=1000, offset=0):  # noqa: ANN001, ANN201
        del search
        items = list(self.groups.values())[offset : offset + limit]
        return items, len(self.groups)

    async def list_bindings(self, *, group_key=None, scope_type=None, scope_id=None, limit=500, offset=0):  # noqa: ANN001, ANN201
        items = [binding for bindings in self.bindings.values() for binding in bindings]
        if group_key:
            items = [binding for binding in items if binding.group_key == group_key]
        if scope_type:
            items = [binding for binding in items if binding.scope_type == scope_type]
        if scope_id:
            items = [binding for binding in items if binding.scope_id == scope_id]
        return items[offset : offset + limit], len(items)

    async def upsert_binding(self, group_key: str, *, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        group = self.groups[group_key]
        items = self.bindings.setdefault(group_key, [])
        for index, binding in enumerate(items):
            if binding.scope_type == scope_type and binding.scope_id == scope_id:
                updated = replace(binding, enabled=enabled, metadata=metadata)
                items[index] = updated
                return updated
        self._binding_counter += 1
        binding = RouteGroupBindingRecord(
            route_group_binding_id=f"rgb-{self._binding_counter}",
            route_group_id=group.route_group_id,
            group_key=group.group_key,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        items.append(binding)
        return binding


class _FakeCallableTargetBindingRepository:
    def __init__(self) -> None:
        self.bindings: list[CallableTargetBindingRecord] = []
        self._counter = 0

    async def list_bindings(self, *, callable_key=None, scope_type=None, scope_id=None, limit=500, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if callable_key:
            items = [item for item in items if item.callable_key == callable_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        return items[offset : offset + limit], len(items)

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
        return items[offset : offset + limit], len(items)

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


@pytest.mark.asyncio
async def test_team_asset_visibility_includes_org_inherited_and_team_direct_sources(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    route_groups = _FakeRouteGroupRepository()
    callable_targets = _FakeCallableTargetBindingRepository()
    policies = _FakeCallableTargetScopePolicyRepository()
    test_app.state.route_group_repository = route_groups
    test_app.state.callable_target_binding_repository = callable_targets
    test_app.state.callable_target_scope_policy_repository = policies
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "team-assistant": CallableTarget(key="team-assistant", target_type="model"),
    }

    await route_groups.create_group(
        group_key="support-owned",
        name="Support",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=apply_owner_scope_to_metadata({}, scope_type="organization", scope_id="org-1"),
    )
    await route_groups.create_group(
        group_key="org-shared",
        name="Org Shared",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    await route_groups.create_group(
        group_key="team-only",
        name="Team Only",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    await route_groups.upsert_binding("support-owned", scope_type="team", scope_id="team-1", enabled=True, metadata={"source": "team"})
    await route_groups.upsert_binding("org-shared", scope_type="organization", scope_id="org-1", enabled=True, metadata={"source": "org"})
    await route_groups.upsert_binding("team-only", scope_type="team", scope_id="team-1", enabled=True, metadata={"source": "team"})
    await callable_targets.upsert_binding(
        callable_key="support-owned",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata={"source": "team"},
    )
    await callable_targets.upsert_binding(
        callable_key="org-shared",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata={"source": "org"},
    )
    await callable_targets.upsert_binding(
        callable_key="team-only",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata={"source": "team"},
    )
    await callable_targets.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata={"source": "org"},
    )
    await callable_targets.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata={"source": "team"},
    )
    await callable_targets.upsert_binding(
        callable_key="team-assistant",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata={"source": "team"},
    )
    await policies.upsert_policy(scope_type="team", scope_id="team-1", mode="restrict", metadata={"rollout": "pilot"})

    response = await client.get(
        "/ui/api/teams/team-1/asset-visibility",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == "org-1"
    assert payload["team_id"] == "team-1"
    assert payload["direct_scope_type"] == "team"
    assert payload["scope_policies"]["team"] == "restrict"

    route_groups_payload = {item["group_key"]: item for item in payload["route_groups"]["items"]}
    assert route_groups_payload["support-owned"]["visibility_source"] == "inherited_and_granted"
    assert {source["scope_type"] for source in route_groups_payload["support-owned"]["sources"]} == {"organization", "team"}
    assert route_groups_payload["support-owned"]["effective_visible"] is True
    assert route_groups_payload["org-shared"]["visibility_source"] == "inherited"
    assert route_groups_payload["org-shared"]["effective_visible"] is False
    assert route_groups_payload["team-only"]["visibility_source"] == "granted"
    assert route_groups_payload["team-only"]["effective_visible"] is False

    callable_payload = {item["callable_key"]: item for item in payload["callable_targets"]["items"]}
    assert callable_payload["gpt-4o-mini"]["visibility_source"] == "inherited_and_granted"
    assert {source["scope_type"] for source in callable_payload["gpt-4o-mini"]["sources"]} == {"organization", "team"}
    assert callable_payload["gpt-4o-mini"]["effective_visible"] is True
    assert callable_payload["team-assistant"]["visibility_source"] == "granted"
    assert callable_payload["team-assistant"]["effective_visible"] is False


@pytest.mark.asyncio
async def test_key_asset_visibility_includes_org_team_and_key_sources(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    route_groups = _FakeRouteGroupRepository()
    callable_targets = _FakeCallableTargetBindingRepository()
    policies = _FakeCallableTargetScopePolicyRepository()
    test_app.state.route_group_repository = route_groups
    test_app.state.callable_target_binding_repository = callable_targets
    test_app.state.callable_target_scope_policy_repository = policies
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "key-assistant": CallableTarget(key="key-assistant", target_type="model"),
    }

    await route_groups.create_group(
        group_key="support-owned",
        name="Support",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=apply_owner_scope_to_metadata({}, scope_type="organization", scope_id="org-1"),
    )
    await route_groups.create_group(
        group_key="key-only",
        name="Key Only",
        mode="chat",
        routing_strategy="weighted",
        enabled=True,
        metadata=None,
    )
    await route_groups.upsert_binding("support-owned", scope_type="team", scope_id="team-1", enabled=True, metadata={"source": "team"})
    await route_groups.upsert_binding("support-owned", scope_type="api_key", scope_id="key-hash-1", enabled=True, metadata={"source": "key"})
    await route_groups.upsert_binding("key-only", scope_type="api_key", scope_id="key-hash-1", enabled=True, metadata={"source": "key"})
    await callable_targets.upsert_binding(
        callable_key="support-owned",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata={"source": "team"},
    )
    await callable_targets.upsert_binding(
        callable_key="support-owned",
        scope_type="api_key",
        scope_id="key-hash-1",
        enabled=True,
        metadata={"source": "key"},
    )
    await callable_targets.upsert_binding(
        callable_key="key-only",
        scope_type="api_key",
        scope_id="key-hash-1",
        enabled=True,
        metadata={"source": "key"},
    )
    await callable_targets.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata={"source": "org"},
    )
    await callable_targets.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="team",
        scope_id="team-1",
        enabled=True,
        metadata={"source": "team"},
    )
    await callable_targets.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="api_key",
        scope_id="key-hash-1",
        enabled=True,
        metadata={"source": "key"},
    )
    await callable_targets.upsert_binding(
        callable_key="key-assistant",
        scope_type="api_key",
        scope_id="key-hash-1",
        enabled=True,
        metadata={"source": "key"},
    )
    await policies.upsert_policy(scope_type="team", scope_id="team-1", mode="restrict", metadata=None)
    await policies.upsert_policy(scope_type="api_key", scope_id="key-hash-1", mode="restrict", metadata=None)

    response = await client.get(
        "/ui/api/keys/key-hash-1/asset-visibility",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == "org-1"
    assert payload["team_id"] == "team-1"
    assert payload["api_key_id"] == "key-hash-1"
    assert payload["direct_scope_type"] == "api_key"
    assert payload["scope_policies"]["team"] == "restrict"
    assert payload["scope_policies"]["api_key"] == "restrict"

    route_groups_payload = {item["group_key"]: item for item in payload["route_groups"]["items"]}
    assert route_groups_payload["support-owned"]["visibility_source"] == "inherited_and_granted"
    assert {source["scope_type"] for source in route_groups_payload["support-owned"]["sources"]} == {"organization", "team", "api_key"}
    assert route_groups_payload["support-owned"]["effective_visible"] is True
    assert route_groups_payload["key-only"]["visibility_source"] == "granted"
    assert route_groups_payload["key-only"]["effective_visible"] is False

    callable_payload = {item["callable_key"]: item for item in payload["callable_targets"]["items"]}
    assert callable_payload["gpt-4o-mini"]["visibility_source"] == "inherited_and_granted"
    assert {source["scope_type"] for source in callable_payload["gpt-4o-mini"]["sources"]} == {"organization", "team", "api_key"}
    assert callable_payload["gpt-4o-mini"]["effective_visible"] is True
    assert callable_payload["key-assistant"]["visibility_source"] == "granted"
    assert callable_payload["key-assistant"]["effective_visible"] is False


@pytest.mark.asyncio
async def test_key_asset_visibility_applies_user_scope_narrowing(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    scope_db = _FakeScopeDB()
    scope_db.keys["key-hash-1"]["user_id"] = "user-1"
    test_app.state.prisma_manager = type("Prisma", (), {"client": scope_db})()
    callable_targets = _FakeCallableTargetBindingRepository()
    policies = _FakeCallableTargetScopePolicyRepository()
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.callable_target_binding_repository = callable_targets
    test_app.state.callable_target_scope_policy_repository = policies
    test_app.state.callable_target_catalog = {
        "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        "text-embedding-3-small": CallableTarget(key="text-embedding-3-small", target_type="model"),
    }

    await callable_targets.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata={"source": "org"},
    )
    await callable_targets.upsert_binding(
        callable_key="text-embedding-3-small",
        scope_type="organization",
        scope_id="org-1",
        enabled=True,
        metadata={"source": "org"},
    )
    await callable_targets.upsert_binding(
        callable_key="gpt-4o-mini",
        scope_type="user",
        scope_id="user-1",
        enabled=True,
        metadata={"source": "user"},
    )

    response = await client.get(
        "/ui/api/keys/key-hash-1/asset-visibility",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "user-1"
    assert payload["direct_scope_type"] == "user"

    callable_payload = {item["callable_key"]: item for item in payload["callable_targets"]["items"]}
    assert callable_payload["gpt-4o-mini"]["effective_visible"] is True
    assert callable_payload["text-embedding-3-small"]["effective_visible"] is False


@pytest.mark.asyncio
async def test_org_asset_visibility_paginates_large_route_group_and_callable_target_sets(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeScopeDB()})()
    route_groups = _FakeRouteGroupRepository()
    callable_targets = _FakeCallableTargetBindingRepository()
    policies = _FakeCallableTargetScopePolicyRepository()
    test_app.state.route_group_repository = route_groups
    test_app.state.callable_target_binding_repository = callable_targets
    test_app.state.callable_target_scope_policy_repository = policies
    test_app.state.callable_target_catalog = {}

    for index in range(520):
        group_key = f"rg-{index}"
        await route_groups.create_group(
            group_key=group_key,
            name=group_key,
            mode="chat",
            routing_strategy="weighted",
            enabled=True,
            metadata=None,
        )
        await callable_targets.upsert_binding(
            callable_key=group_key,
            scope_type="organization",
            scope_id="org-1",
            enabled=True,
            metadata={"index": index},
        )

    response = await client.get(
        "/ui/api/organizations/org-1/asset-visibility",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["route_groups"]["total"] == 520
    assert payload["callable_targets"]["total"] == 520
    route_group_keys = {item["group_key"] for item in payload["route_groups"]["items"]}
    callable_keys = {item["callable_key"] for item in payload["callable_targets"]["items"]}
    assert {"rg-0", "rg-519"} <= route_group_keys
    assert {"rg-0", "rg-519"} <= callable_keys
