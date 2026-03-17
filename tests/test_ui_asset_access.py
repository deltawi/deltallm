from __future__ import annotations

from dataclasses import replace

import pytest

from src.db.callable_target_policies import CallableTargetScopePolicyRecord
from src.db.callable_targets import CallableTargetBindingRecord
from src.services.callable_targets import CallableTarget


class _FakeScopeDB:
    def __init__(self) -> None:
        self.organizations = {
            "org-1": {"organization_id": "org-1"},
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
        if "FROM deltallm_teamtable" in query and "WHERE team_id = $1" in query:
            row = self.teams.get(str(params[0]))
            return [row] if row else []
        if "FROM deltallm_verificationtoken vt" in query and "WHERE vt.token = $1" in query:
            row = self.keys.get(str(params[0]))
            return [row] if row else []
        return []


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
    assert payload["selected_callable_keys"] == ["gpt-4o-mini"]
    assert payload["summary"]["selectable_total"] == 2
    assert payload["summary"]["effective_total"] == 1


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
