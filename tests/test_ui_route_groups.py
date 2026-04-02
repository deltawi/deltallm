from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from src.db.callable_targets import CallableTargetBindingRecord
from src.db.prompt_registry import PromptResolvedRecord
from src.db.route_groups import RouteGroupBindingRecord, RouteGroupMemberRecord, RouteGroupRecord, RoutePolicyRecord
from src.services.asset_ownership import owner_scope_from_metadata
from src.services.asset_scopes import normalize_scope_type


def _extract_default_prompt(metadata: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("default_prompt")
    if not isinstance(raw, dict):
        return None
    template_key = str(raw.get("template_key") or "").strip()
    if not template_key:
        return None
    label = str(raw.get("label") or "").strip()
    payload: dict[str, str] = {"template_key": template_key}
    if label:
        payload["label"] = label
    return payload


class _FakeRouteGroupRepository:
    def __init__(self) -> None:
        self.groups: dict[str, RouteGroupRecord] = {}
        self.members: dict[str, list[RouteGroupMemberRecord]] = {}
        self.bindings: dict[str, list[RouteGroupBindingRecord]] = {}
        self.policies: dict[str, RoutePolicyRecord] = {}
        self._group_counter = 0
        self._member_counter = 0
        self._binding_counter = 0
        self._policy_counter = 0

    async def list_groups(self, *, search=None, limit=100, offset=0):  # noqa: ANN001, ANN201
        del search
        items = list(self.groups.values())[offset : offset + limit]
        return items, len(self.groups)

    async def get_group(self, group_key: str):  # noqa: ANN201
        return self.groups.get(group_key)

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
            default_prompt=_extract_default_prompt(metadata),
            owner_scope_type=owner_scope.scope_type,
            owner_scope_id=owner_scope.scope_id,
        )
        self.groups[group_key] = record
        return record

    async def update_group(self, group_key: str, *, name, mode, routing_strategy, enabled, metadata):  # noqa: ANN001, ANN201
        existing = self.groups.get(group_key)
        if existing is None:
            return None
        owner_scope = owner_scope_from_metadata(metadata)
        updated = replace(
            existing,
            name=name,
            mode=mode,
            routing_strategy=routing_strategy,
            enabled=enabled,
            metadata=metadata,
            default_prompt=_extract_default_prompt(metadata),
            owner_scope_type=owner_scope.scope_type,
            owner_scope_id=owner_scope.scope_id,
        )
        self.groups[group_key] = updated
        return updated

    async def delete_group(self, group_key: str) -> bool:
        if group_key not in self.groups:
            return False
        self.groups.pop(group_key, None)
        self.members.pop(group_key, None)
        self.bindings.pop(group_key, None)
        self.policies.pop(group_key, None)
        return True

    async def list_bindings(self, *, group_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = [binding for bindings in self.bindings.values() for binding in bindings]
        if group_key:
            items = [binding for binding in items if binding.group_key == group_key]
        if scope_type:
            items = [binding for binding in items if binding.scope_type == scope_type]
        if scope_id:
            items = [binding for binding in items if binding.scope_id == scope_id]
        sliced = items[offset : offset + limit]
        return sliced, len(items)

    async def upsert_binding(self, group_key: str, *, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        group = self.groups.get(group_key)
        if group is None:
            return None
        normalized_scope_type = normalize_scope_type(scope_type)
        items = self.bindings.setdefault(group_key, [])
        for idx, binding in enumerate(items):
            if binding.scope_type == normalized_scope_type and binding.scope_id == scope_id:
                updated = replace(binding, enabled=enabled, metadata=metadata)
                items[idx] = updated
                return updated
        self._binding_counter += 1
        binding = RouteGroupBindingRecord(
            route_group_binding_id=f"rgb-{self._binding_counter}",
            route_group_id=group.route_group_id,
            group_key=group.group_key,
            scope_type=normalized_scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        items.append(binding)
        return binding

    async def get_binding(self, binding_id: str):  # noqa: ANN201
        for items in self.bindings.values():
            for binding in items:
                if binding.route_group_binding_id == binding_id:
                    return binding
        return None

    async def delete_binding(self, binding_id: str) -> bool:
        for group_key, items in self.bindings.items():
            kept = [binding for binding in items if binding.route_group_binding_id != binding_id]
            if len(kept) != len(items):
                self.bindings[group_key] = kept
                return True
        return False

    async def list_members(self, group_key: str):  # noqa: ANN201
        return list(self.members.get(group_key, []))

    async def upsert_member(self, group_key: str, *, deployment_id, enabled, weight, priority):  # noqa: ANN001, ANN201
        group = self.groups.get(group_key)
        if group is None:
            return None
        items = self.members.setdefault(group_key, [])
        for idx, member in enumerate(items):
            if member.deployment_id == deployment_id:
                updated = replace(member, enabled=enabled, weight=weight, priority=priority)
                items[idx] = updated
                return updated
        self._member_counter += 1
        member = RouteGroupMemberRecord(
            membership_id=f"m-{self._member_counter}",
            route_group_id=group.route_group_id,
            deployment_id=deployment_id,
            enabled=enabled,
            weight=weight,
            priority=priority,
        )
        items.append(member)
        return member

    async def remove_member(self, group_key: str, deployment_id: str) -> bool:
        items = self.members.get(group_key, [])
        kept = [member for member in items if member.deployment_id != deployment_id]
        if len(kept) == len(items):
            return False
        self.members[group_key] = kept
        return True

    async def get_published_policy(self, group_key: str):  # noqa: ANN201
        return self.policies.get(group_key)

    async def list_policies(self, group_key: str):  # noqa: ANN201
        policy = self.policies.get(group_key)
        return [policy] if policy is not None else []

    async def list_runtime_groups(self):  # noqa: ANN201
        runtime_groups: list[dict] = []
        for group_key, group in self.groups.items():
            policy = self.policies.get(group_key)
            policy_json = policy.policy_json if policy is not None and policy.status == "published" else {}
            strategy = policy_json.get("strategy") if isinstance(policy_json.get("strategy"), str) else group.routing_strategy
            runtime_groups.append(
                {
                    "key": group.group_key,
                    "mode": group.mode,
                    "enabled": group.enabled,
                    "strategy": strategy,
                    "policy_version": policy.version if policy is not None and policy.status == "published" else None,
                    "timeouts": policy_json.get("timeouts") if isinstance(policy_json.get("timeouts"), dict) else None,
                    "retry": policy_json.get("retry") if isinstance(policy_json.get("retry"), dict) else None,
                    "default_prompt": group.default_prompt,
                    "members": [
                        {
                            "deployment_id": member.deployment_id,
                            "enabled": member.enabled,
                            "weight": member.weight,
                            "priority": member.priority,
                        }
                        for member in self.members.get(group_key, [])
                    ],
                }
            )
        return runtime_groups

    async def save_draft_policy(self, group_key: str, policy_json: dict):  # noqa: ANN001, ANN201
        group = self.groups.get(group_key)
        if group is None:
            return None
        self._policy_counter += 1
        policy = RoutePolicyRecord(
            route_policy_id=f"p-{self._policy_counter}",
            route_group_id=group.route_group_id,
            version=self._policy_counter,
            status="draft",
            policy_json=policy_json,
            published_by=None,
        )
        self.policies[group_key] = policy
        return policy

    async def publish_policy(self, group_key: str, policy_json: dict, *, published_by=None):  # noqa: ANN001, ANN201
        group = self.groups.get(group_key)
        if group is None:
            return None
        self._policy_counter += 1
        policy = RoutePolicyRecord(
            route_policy_id=f"p-{self._policy_counter}",
            route_group_id=group.route_group_id,
            version=self._policy_counter,
            status="published",
            policy_json=policy_json,
            published_by=published_by,
        )
        self.policies[group_key] = policy
        return policy

    async def publish_latest_draft(self, group_key: str, *, published_by=None):  # noqa: ANN001, ANN201
        policy = self.policies.get(group_key)
        if policy is None:
            return None
        if policy.status != "draft":
            return None
        published = replace(policy, status="published", published_by=published_by)
        self.policies[group_key] = published
        return published

    async def rollback_policy(self, group_key: str, *, target_version: int, published_by=None):  # noqa: ANN001, ANN201
        policy = self.policies.get(group_key)
        if policy is None:
            return None
        if target_version < 1:
            return None
        self._policy_counter += 1
        rolled = replace(
            policy,
            route_policy_id=f"p-{self._policy_counter}",
            version=self._policy_counter,
            status="published",
            published_by=published_by,
        )
        self.policies[group_key] = rolled
        return rolled


class _FakeOrganizationMetadataDB:
    def __init__(self) -> None:
        self.organizations = {
            "org-1": {
                "organization_id": "org-1",
                "metadata": {"_callable_target_access": {"auto_follow_catalog": True}},
            }
        }

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        if "FROM deltallm_organizationtable" in query and "WHERE organization_id = $1" in query:
            row = self.organizations.get(str(params[0]))
            return [row] if row else []
        if "FROM deltallm_organizationtable" in query:
            return list(self.organizations.values())
        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        if "UPDATE deltallm_organizationtable" in query and "metadata = $2::jsonb" in query:
            row = self.organizations.get(str(params[0]))
            if row is None:
                return 0
            row["metadata"] = params[1]
            return 1
        return 0


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
        return items[offset : offset + limit], len(items)

    async def upsert_binding(self, *, callable_key, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        for index, binding in enumerate(self.bindings):
            if binding.callable_key == callable_key and binding.scope_type == scope_type and binding.scope_id == scope_id:
                updated = replace(binding, enabled=enabled, metadata=metadata)
                self.bindings[index] = updated
                return updated
        self._counter += 1
        binding = CallableTargetBindingRecord(
            callable_target_binding_id=f"ctb-{self._counter}",
            callable_key=callable_key,
            scope_type=scope_type,
            scope_id=scope_id,
            enabled=enabled,
            metadata=metadata,
        )
        self.bindings.append(binding)
        return binding

    async def delete_binding(self, binding_id: str) -> bool:
        kept = [binding for binding in self.bindings if binding.callable_target_binding_id != binding_id]
        if len(kept) == len(self.bindings):
            return False
        self.bindings = kept
        return True


class _FakeGrantReload:
    def __init__(self) -> None:
        self.reload_count = 0

    async def reload(self) -> None:
        self.reload_count += 1


class _FakeHotReload:
    def __init__(self) -> None:
        self.calls = 0

    async def reload_runtime(self) -> None:
        self.calls += 1


class _FakeRouteGroupRuntimeCache:
    def __init__(self) -> None:
        self.invalidate_calls = 0

    async def invalidate(self) -> None:
        self.invalidate_calls += 1


@pytest.mark.asyncio
async def test_route_group_admin_crud_and_policy_publish(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    runtime_cache = _FakeRouteGroupRuntimeCache()
    test_app.state.route_group_runtime_cache = runtime_cache
    headers = {"Authorization": "Bearer mk-test"}

    create_response = await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={"group_key": "support-route", "mode": "chat", "strategy": "weighted"},
    )
    assert create_response.status_code == 200
    assert create_response.json()["group_key"] == "support-route"
    assert create_response.json()["owner_scope_type"] == "global"
    assert create_response.json()["owner_scope_id"] is None

    member_response = await client.post(
        "/ui/api/route-groups/support-route/members",
        headers=headers,
        json={"deployment_id": "dep-a", "weight": 5},
    )
    assert member_response.status_code == 200
    assert member_response.json()["deployment_id"] == "dep-a"

    policy_response = await client.put(
        "/ui/api/route-groups/support-route/policy",
        headers=headers,
        json={"strategy": "least-busy"},
    )
    assert policy_response.status_code == 200
    assert policy_response.json()["policy"]["status"] == "published"

    detail_response = await client.get("/ui/api/route-groups/support-route", headers=headers)
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["group"]["group_key"] == "support-route"
    assert payload["group"]["owner_scope_type"] == "global"
    assert len(payload["members"]) == 1
    assert payload["policy"]["policy_json"]["strategy"] == "least-busy"

    assert test_app.state.model_hot_reload_manager.calls == 3
    assert runtime_cache.invalidate_calls == 3


@pytest.mark.asyncio
async def test_route_group_policy_draft_validate_publish_and_rollback(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    runtime_cache = _FakeRouteGroupRuntimeCache()
    test_app.state.route_group_runtime_cache = runtime_cache
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={"group_key": "ops-route", "mode": "chat", "strategy": "weighted"},
    )
    await client.post(
        "/ui/api/route-groups/ops-route/members",
        headers=headers,
        json={"deployment_id": "dep-a", "weight": 5},
    )

    validate_response = await client.post(
        "/ui/api/route-groups/ops-route/policy/validate",
        headers=headers,
        json={"strategy": "least-busy", "mode": "weighted", "members": [{"deployment_id": "dep-a"}]},
    )
    assert validate_response.status_code == 200
    assert validate_response.json()["valid"] is True

    draft_response = await client.post(
        "/ui/api/route-groups/ops-route/policy/draft",
        headers=headers,
        json={"strategy": "least-busy"},
    )
    assert draft_response.status_code == 200
    assert draft_response.json()["policy"]["status"] == "draft"

    publish_response = await client.post(
        "/ui/api/route-groups/ops-route/policy/publish",
        headers=headers,
        json={},
    )
    assert publish_response.status_code == 200
    assert publish_response.json()["policy"]["status"] == "published"

    list_response = await client.get("/ui/api/route-groups/ops-route/policies", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()["policies"]) == 1

    rollback_response = await client.post(
        "/ui/api/route-groups/ops-route/policy/rollback",
        headers=headers,
        json={"version": 1},
    )
    assert rollback_response.status_code == 200
    assert rollback_response.json()["policy"]["status"] == "published"
    assert runtime_cache.invalidate_calls == 4


@pytest.mark.asyncio
async def test_route_group_policy_validate_rejects_unknown_members(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={"group_key": "invalid-members", "mode": "chat", "strategy": "weighted"},
    )
    await client.post(
        "/ui/api/route-groups/invalid-members/members",
        headers=headers,
        json={"deployment_id": "dep-a", "weight": 1},
    )

    response = await client.post(
        "/ui/api/route-groups/invalid-members/policy/validate",
        headers=headers,
        json={"members": [{"deployment_id": "dep-z", "weight": 1}]},
    )

    assert response.status_code == 400
    assert "unknown members" in response.text


@pytest.mark.asyncio
async def test_route_group_policy_simulation_returns_selection_summary(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    test_app.state.model_registry = {
        "gpt-4o-mini": [
            {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"}}
        ]
    }
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={"group_key": "sim-route", "mode": "chat", "strategy": "weighted"},
    )
    await client.post(
        "/ui/api/route-groups/sim-route/members",
        headers=headers,
        json={"deployment_id": "dep-a", "weight": 1},
    )

    response = await client.post(
        "/ui/api/route-groups/sim-route/policy/simulate",
        headers=headers,
        json={"iterations": 20, "policy": {"strategy": "weighted", "members": [{"deployment_id": "dep-a", "weight": 1}]}},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["group_key"] == "sim-route"
    assert payload["iterations"] == 20
    assert payload["summary"]["no_selection_requests"] == 0
    assert payload["selections"][0]["deployment_id"] == "dep-a"
    assert payload["selections"][0]["count"] == 20


@pytest.mark.asyncio
async def test_route_group_policy_simulation_applies_prompt_route_preferences(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    test_app.state.model_registry = {
        "gpt-4o-mini": [
            {"deployment_id": "dep-a", "deltallm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"}},
            {
                "deployment_id": "dep-b",
                "deltallm_params": {"model": "openai/gpt-4o-mini", "api_key": "provider-key"},
                "model_info": {"tags": ["vip"]},
            },
        ]
    }

    class _PromptRepoForSimulation:
        async def resolve_prompt(self, *, template_key: str, label: str | None = None, version: int | None = None):  # noqa: ANN201
            del label, version
            if template_key != "support.prompt":
                return None
            return PromptResolvedRecord(
                prompt_template_id="tmpl-1",
                template_key="support.prompt",
                prompt_version_id="ver-1",
                version=2,
                status="published",
                label="production",
                template_body={"text": "hello"},
                variables_schema=None,
                model_hints=None,
                route_preferences={"tags": ["vip"]},
            )

    test_app.state.prompt_registry_repository = _PromptRepoForSimulation()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={"group_key": "sim-route", "mode": "chat", "strategy": "weighted"},
    )
    await client.post(
        "/ui/api/route-groups/sim-route/members",
        headers=headers,
        json={"deployment_id": "dep-a", "weight": 1},
    )
    await client.post(
        "/ui/api/route-groups/sim-route/members",
        headers=headers,
        json={"deployment_id": "dep-b", "weight": 1},
    )

    response = await client.post(
        "/ui/api/route-groups/sim-route/policy/simulate",
        headers=headers,
        json={
            "iterations": 20,
            "policy": {"strategy": "weighted", "members": [{"deployment_id": "dep-a"}, {"deployment_id": "dep-b"}]},
            "prompt_ref": {"template_key": "support.prompt", "label": "production"},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["prompt"]["template_key"] == "support.prompt"
    assert payload["effective_metadata"]["tags"] == ["vip"]
    assert payload["selections"][0]["deployment_id"] == "dep-b"
    assert payload["selections"][0]["count"] == 20


@pytest.mark.asyncio
async def test_route_group_default_prompt_is_saved_and_cleared(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    runtime_cache = _FakeRouteGroupRuntimeCache()
    test_app.state.route_group_runtime_cache = runtime_cache

    class _FakePromptRepository:
        async def get_template(self, template_key: str):  # noqa: ANN201
            if template_key == "support.prompt":
                return {"template_key": template_key}
            return None

    class _FakePromptService:
        def __init__(self) -> None:
            self.invalidations: list[tuple[str, str]] = []

        async def invalidate_scope(self, *, scope_type: str, scope_id: str) -> None:
            self.invalidations.append((scope_type, scope_id))

    test_app.state.prompt_registry_repository = _FakePromptRepository()
    prompt_service = _FakePromptService()
    test_app.state.prompt_registry_service = prompt_service
    headers = {"Authorization": "Bearer mk-test"}

    create_response = await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={
            "group_key": "support-route",
            "mode": "chat",
            "strategy": "weighted",
            "default_prompt": {"template_key": "support.prompt", "label": "production"},
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["default_prompt"] == {"template_key": "support.prompt", "label": "production"}

    detail_response = await client.get("/ui/api/route-groups/support-route", headers=headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["group"]["default_prompt"] == {"template_key": "support.prompt", "label": "production"}

    update_response = await client.put(
        "/ui/api/route-groups/support-route",
        headers=headers,
        json={"default_prompt": None},
    )
    assert update_response.status_code == 200
    assert update_response.json()["default_prompt"] is None
    assert runtime_cache.invalidate_calls >= 2
    assert ("group", "support-route") in prompt_service.invalidations


@pytest.mark.asyncio
async def test_route_group_can_be_created_with_organization_owner_scope(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakeRouteGroupRepository()
    test_app.state.route_group_repository = repo
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={
            "group_key": "org-owned-route",
            "mode": "chat",
            "strategy": "weighted",
            "owner_scope_type": "organization",
            "owner_scope_id": "org-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["owner_scope_type"] == "organization"
    assert payload["owner_scope_id"] == "org-1"
    assert repo.groups["org-owned-route"].owner_scope_type == "organization"
    assert repo.groups["org-owned-route"].owner_scope_id == "org-1"


@pytest.mark.asyncio
async def test_route_group_owner_scope_can_be_reset_to_global(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakeRouteGroupRepository()
    test_app.state.route_group_repository = repo
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={
            "group_key": "owned-route",
            "mode": "chat",
            "strategy": "weighted",
            "owner_scope_type": "organization",
            "owner_scope_id": "org-1",
        },
    )

    response = await client.put(
        "/ui/api/route-groups/owned-route",
        headers=headers,
        json={"owner_scope_type": "global"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["owner_scope_type"] == "global"
    assert payload["owner_scope_id"] is None
    assert repo.groups["owned-route"].owner_scope_type == "global"
    assert repo.groups["owned-route"].owner_scope_id is None


@pytest.mark.asyncio
async def test_route_group_rejects_organization_owner_without_scope_id(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    headers = {"Authorization": "Bearer mk-test"}

    response = await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={
            "group_key": "invalid-owned-route",
            "mode": "chat",
            "strategy": "weighted",
            "owner_scope_type": "organization",
        },
    )

    assert response.status_code == 400
    assert "owner_scope_id is required" in response.text


@pytest.mark.asyncio
async def test_route_group_binding_admin_lifecycle(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakeRouteGroupRepository()
    callable_repo = _FakeCallableTargetBindingRepository()
    grant_reload = _FakeGrantReload()
    scope_db = _FakeOrganizationMetadataDB()
    test_app.state.route_group_repository = repo
    test_app.state.callable_target_binding_repository = callable_repo
    test_app.state.callable_target_grant_service = grant_reload
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    test_app.state.prisma_manager = type("Prisma", (), {"client": scope_db})()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={"group_key": "bound-route", "mode": "chat"},
    )

    upsert_response = await client.post(
        "/ui/api/route-group-bindings",
        headers=headers,
        json={
            "group_key": "bound-route",
            "scope_type": "org",
            "scope_id": "org-1",
            "enabled": True,
            "metadata": {"source": "bootstrap"},
        },
    )
    assert upsert_response.status_code == 200
    payload = upsert_response.json()
    assert payload["group_key"] == "bound-route"
    assert payload["scope_type"] == "organization"
    assert payload["scope_id"] == "org-1"
    assert {(binding.callable_key, binding.scope_type, binding.scope_id) for binding in callable_repo.bindings} == {
        ("bound-route", "organization", "org-1")
    }
    assert scope_db.organizations["org-1"]["metadata"] is None
    assert grant_reload.reload_count == 1

    list_response = await client.get(
        "/ui/api/route-group-bindings",
        headers=headers,
        params={"group_key": "bound-route", "scope_type": "organization", "scope_id": "org-1"},
    )
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 1
    assert list_response.json()["data"][0]["scope_type"] == "organization"

    detail_response = await client.get("/ui/api/route-groups/bound-route", headers=headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["bindings"][0]["scope_id"] == "org-1"

    scope_db.organizations["org-1"]["metadata"] = {"_callable_target_access": {"auto_follow_catalog": True}}
    delete_response = await client.delete(
        f"/ui/api/route-group-bindings/{payload['route_group_binding_id']}",
        headers=headers,
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
    assert callable_repo.bindings == []
    assert scope_db.organizations["org-1"]["metadata"] is None
    assert grant_reload.reload_count == 2


@pytest.mark.asyncio
async def test_route_group_binding_rejects_invalid_scope_type(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repo = _FakeRouteGroupRepository()
    test_app.state.route_group_repository = repo
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    headers = {"Authorization": "Bearer mk-test"}

    await client.post(
        "/ui/api/route-groups",
        headers=headers,
        json={"group_key": "bound-route", "mode": "chat"},
    )

    response = await client.post(
        "/ui/api/route-group-bindings",
        headers=headers,
        json={"group_key": "bound-route", "scope_type": "group", "scope_id": "nested"},
    )

    assert response.status_code == 400
    assert "scope_type must be one of" in response.text
