from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.db.callable_targets import CallableTargetBindingRecord
from src.db.route_groups import RouteGroupBindingRecord, RouteGroupRecord
from src.services.callable_target_grants import CallableTargetGrantService
from src.services.callable_targets import CallableTarget
from src.services.organization_callable_target_sync import sync_auto_follow_organization_bindings


class _FakeOrganizationMetadataDB:
    def __init__(self) -> None:
        self.organizations = {
            "org-default": {
                "organization_id": "org-default",
                "metadata": {
                    "_callable_target_access": {"auto_follow_catalog": True},
                },
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


class _MalformedOrganizationMetadataDB(_FakeOrganizationMetadataDB):
    def __init__(self) -> None:
        super().__init__()
        self.organizations["org-default"]["metadata"] = {
            "_callable_target_access": {"auto_follow_catalog": "yes"},
        }


class _MutableCallableTargetBindingRepository:
    def __init__(self, bindings: list[CallableTargetBindingRecord]) -> None:
        self.bindings = list(bindings)
        self._counter = len(bindings)

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


class _MutableRouteGroupRepository:
    def __init__(self) -> None:
        self.groups = {
            "support-fast": RouteGroupRecord(
                route_group_id="rg-1",
                group_key="support-fast",
                name="Support Fast",
                mode="chat",
                routing_strategy="weighted",
                enabled=True,
                metadata=None,
                owner_scope_type="global",
                owner_scope_id=None,
            )
        }
        self.bindings: list[RouteGroupBindingRecord] = [
            RouteGroupBindingRecord(
                route_group_binding_id="rgb-1",
                route_group_id="rg-1",
                group_key="support-fast",
                scope_type="organization",
                scope_id="org-default",
                enabled=True,
                metadata=None,
            )
        ]

    async def list_bindings(self, *, group_key=None, scope_type=None, scope_id=None, limit=200, offset=0):  # noqa: ANN001, ANN201
        items = list(self.bindings)
        if group_key:
            items = [item for item in items if item.group_key == group_key]
        if scope_type:
            items = [item for item in items if item.scope_type == scope_type]
        if scope_id:
            items = [item for item in items if item.scope_id == scope_id]
        page = items[offset : offset + limit]
        return page, len(items)

    async def upsert_binding(self, group_key: str, *, scope_type, scope_id, enabled, metadata):  # noqa: ANN001, ANN201
        for index, item in enumerate(self.bindings):
            if item.group_key == group_key and item.scope_type == scope_type and item.scope_id == scope_id:
                updated = replace(item, enabled=enabled, metadata=metadata)
                self.bindings[index] = updated
                return updated
        group = self.groups[group_key]
        record = RouteGroupBindingRecord(
            route_group_binding_id=f"rgb-{len(self.bindings) + 1}",
            route_group_id=group.route_group_id,
            group_key=group_key,
            scope_type=scope_type,
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


@pytest.mark.asyncio
async def test_list_models_returns_runtime_models(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/models", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total"] >= 1
    model_names = {item["model_name"] for item in payload["data"]}
    assert "gpt-4o-mini" in model_names


@pytest.mark.asyncio
async def test_get_model_returns_health_block(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.get("/ui/api/models/gpt-4o-mini-0", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["deployment_id"] == "gpt-4o-mini-0"
    assert payload["health"]["healthy"] is True


@pytest.mark.asyncio
async def test_model_health_check_uses_runtime_checker_when_available(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    async def _check_once(deployment):  # noqa: ANN001, ANN202
        return SimpleNamespace(healthy=True, error=None, status_code=200, checked_at=123)

    test_app.state.background_health_checker = SimpleNamespace(check_deployment_once=_check_once)

    response = await client.post("/ui/api/models/gpt-4o-mini-0/health-check", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["deployment_id"] == "gpt-4o-mini-0"
    assert payload["status_code"] == 200
    assert payload["checked_at"] == 123


@pytest.mark.asyncio
async def test_provider_health_summary_aggregates_provider_statuses(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": "dep-openai-1",
                "deltallm_params": {
                    "provider": "openai",
                    "model": "openai/gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "provider-key",
                },
            },
            {
                "deployment_id": "dep-openai-2",
                "deltallm_params": {
                    "provider": "openai",
                    "model": "openai/gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "provider-key",
                },
            },
        ],
        "claude-sonnet": [
            {
                "deployment_id": "dep-anthropic-1",
                "deltallm_params": {
                    "provider": "anthropic",
                    "model": "anthropic/claude-sonnet-4-20250514",
                    "api_base": "https://api.anthropic.com/v1",
                    "api_key": "provider-key",
                },
            },
        ],
        "azure-gpt": [
            {
                "deployment_id": "dep-azure-1",
                "deltallm_params": {
                    "provider": "azure_openai",
                    "model": "azure_openai/gpt-4o-mini",
                    "api_base": "https://resource.openai.azure.com/openai/v1",
                    "api_key": "provider-key",
                },
            },
        ],
    }

    await test_app.state.router_state_backend.set_health("dep-openai-2", False)
    await test_app.state.router_state_backend.set_cooldown("dep-anthropic-1", 60, "test-cooldown")

    response = await client.get("/ui/api/models/provider-health-summary", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_models"] == 4
    assert payload["summary"] == {
        "total_providers": 3,
        "active_providers": 2,
        "down_providers": 1,
    }

    providers = {item["provider"]: item for item in payload["providers"]}
    assert providers["openai"]["models"] == 2
    assert providers["openai"]["healthy_models"] == 1
    assert providers["openai"]["unhealthy_models"] == 1
    assert providers["openai"]["status"] == "degraded"
    assert providers["anthropic"]["status"] == "down"
    assert providers["azure_openai"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_provider_health_summary_counts_models_beyond_paginated_limit(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": f"dep-openai-{index}",
                "deltallm_params": {
                    "provider": "openai",
                    "model": "openai/gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "provider-key",
                },
            }
            for index in range(501)
        ]
    }

    response = await client.get("/ui/api/models/provider-health-summary", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_models"] == 501
    assert payload["summary"]["total_providers"] == 1
    assert payload["providers"][0]["provider"] == "openai"
    assert payload["providers"][0]["models"] == 501
    assert payload["providers"][0]["healthy_models"] == 501


@pytest.mark.asyncio
async def test_create_model_syncs_auto_follow_org_bindings(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    binding_repository = _MutableCallableTargetBindingRepository(
        [
            CallableTargetBindingRecord(
                callable_target_binding_id="ctb-org-1",
                callable_key="gpt-4o-mini",
                scope_type="organization",
                scope_id="org-default",
                enabled=True,
                metadata=None,
            ),
            CallableTargetBindingRecord(
                callable_target_binding_id="ctb-org-2",
                callable_key="text-embedding-3-small",
                scope_type="organization",
                scope_id="org-default",
                enabled=True,
                metadata=None,
            ),
        ]
    )
    test_app.state.prisma_manager = type("Prisma", (), {"client": _FakeOrganizationMetadataDB()})()
    test_app.state.callable_target_binding_repository = binding_repository
    test_app.state.callable_target_grant_service = CallableTargetGrantService(
        repository=binding_repository,
        policy_repository=None,
    )
    await test_app.state.callable_target_grant_service.reload()

    create_response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "gpt-5-mini",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-5-mini",
                "api_base": "https://api.openai.com/v1",
                "api_key": "provider-key",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert create_response.status_code == 200
    assert any(
        binding.scope_type == "organization"
        and binding.scope_id == "org-default"
        and binding.callable_key == "gpt-5-mini"
        for binding in binding_repository.bindings
    )

    models_response = await client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
    )

    assert models_response.status_code == 200
    model_ids = {item["id"] for item in models_response.json()["data"]}
    assert "gpt-5-mini" in model_ids


@pytest.mark.asyncio
async def test_auto_follow_sync_prunes_stale_org_bindings() -> None:
    binding_repository = _MutableCallableTargetBindingRepository(
        [
            CallableTargetBindingRecord(
                callable_target_binding_id="ctb-org-1",
                callable_key="gpt-4o-mini",
                scope_type="organization",
                scope_id="org-default",
                enabled=True,
                metadata=None,
            ),
            CallableTargetBindingRecord(
                callable_target_binding_id="ctb-org-2",
                callable_key="support-fast",
                scope_type="organization",
                scope_id="org-default",
                enabled=True,
                metadata=None,
            ),
        ]
    )
    route_group_repository = _MutableRouteGroupRepository()

    changed = await sync_auto_follow_organization_bindings(
        db=_FakeOrganizationMetadataDB(),
        callable_target_binding_repository=binding_repository,
        route_group_repository=route_group_repository,
        callable_target_catalog={
            "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        },
    )

    assert changed == 2
    assert {(item.scope_type, item.scope_id, item.callable_key) for item in binding_repository.bindings} == {
        ("organization", "org-default", "gpt-4o-mini")
    }
    assert route_group_repository.bindings == []


@pytest.mark.asyncio
async def test_auto_follow_sync_ignores_malformed_metadata() -> None:
    binding_repository = _MutableCallableTargetBindingRepository([])

    changed = await sync_auto_follow_organization_bindings(
        db=_MalformedOrganizationMetadataDB(),
        callable_target_binding_repository=binding_repository,
        route_group_repository=None,
        callable_target_catalog={
            "gpt-4o-mini": CallableTarget(key="gpt-4o-mini", target_type="model"),
        },
    )

    assert changed == 0
    assert binding_repository.bindings == []
