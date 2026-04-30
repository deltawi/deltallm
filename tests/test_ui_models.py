from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.db.named_credentials import NamedCredentialRecord
from src.db.callable_targets import CallableTargetBindingRecord
from src.db.route_groups import RouteGroupBindingRecord, RouteGroupRecord
from src.router import build_deployment_registry
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


class _FakeNamedCredentialRepository:
    def __init__(self, records: list[NamedCredentialRecord]) -> None:
        self.records = {record.credential_id: record for record in records}

    async def get_by_id(self, credential_id: str) -> NamedCredentialRecord | None:
        return self.records.get(credential_id)


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
    assert payload["credential_source"] == "inline"
    assert payload["inline_credentials_present"] is True
    assert payload["connection_summary"]["api_base"] is None
    assert "auth_header_name" not in payload["connection_summary"]
    assert "custom_auth_label" not in payload["connection_summary"]
    assert payload["deltallm_params"]["api_key"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_get_model_redacts_named_credential_backed_params(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.model_registry = {
        "gpt-4o-mini": [
            {
                "deployment_id": "dep-named-1",
                "named_credential_id": "cred-1",
                "named_credential_name": "OpenAI prod",
                "deltallm_params": {
                    "provider": "openai",
                    "model": "openai/gpt-4o-mini",
                    "api_base": "https://api.openai.com/v1",
                    "api_key": "provider-key",
                    "auth_header_format": "Token {api_key}",
                },
                "model_info": {"mode": "chat"},
            }
        ]
    }
    rebuilt = build_deployment_registry(test_app.state.model_registry)
    test_app.state.router.deployment_registry.clear()
    test_app.state.router.deployment_registry.update(rebuilt)

    response = await client.get("/ui/api/models/dep-named-1", headers={"Authorization": "Bearer mk-test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["credential_source"] == "named"
    assert payload["inline_credentials_present"] is False
    assert payload["named_credential_id"] == "cred-1"
    assert payload["named_credential_name"] == "OpenAI prod"
    assert payload["deltallm_params"]["api_key"] == "***REDACTED***"
    assert payload["deltallm_params"]["api_base"] == "https://api.openai.com/v1"
    assert payload["deltallm_params"]["auth_header_format"] == "Token {api_key}"
    assert payload["connection_summary"]["api_base"] == "https://api.openai.com/v1"
    assert payload["connection_summary"]["auth_header_name"] == "Authorization"
    assert payload["connection_summary"]["custom_auth_label"] == "Authorization (Token)"


@pytest.mark.asyncio
async def test_update_model_preserves_inline_api_key_when_omitted(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.put(
        "/ui/api/models/gpt-4o-mini-0",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "gpt-4o-mini",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
                "api_base": "https://api.openai.com/v1",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deltallm_params"]["api_key"] == "***REDACTED***"
    assert test_app.state.model_registry["gpt-4o-mini"][0]["deltallm_params"]["api_key"] == "provider-key"


@pytest.mark.asyncio
async def test_update_model_normalizes_access_groups(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.put(
        "/ui/api/models/gpt-4o-mini-0",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "gpt-4o-mini",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
                "api_base": "https://api.openai.com/v1",
            },
            "model_info": {"mode": "chat", "access_groups": ["Beta", "support", "beta"]},
        },
    )

    assert response.status_code == 200
    assert response.json()["model_info"]["access_groups"] == ["beta", "support"]


@pytest.mark.asyncio
async def test_update_model_clears_old_connection_fields_when_provider_changes(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.put(
        "/ui/api/models/gpt-4o-mini-0",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "gpt-4o-mini",
            "deltallm_params": {
                "provider": "anthropic",
                "model": "anthropic/claude-sonnet-4-20250514",
                "api_base": "https://api.anthropic.com/v1",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deltallm_params"]["provider"] == "anthropic"
    assert payload["deltallm_params"]["api_base"] == "https://api.anthropic.com/v1"
    assert "api_key" not in payload["deltallm_params"]


@pytest.mark.asyncio
async def test_create_model_accepts_azure_named_credential_alias_match(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository(
        [
            NamedCredentialRecord(
                credential_id="cred-azure",
                name="Azure Shared",
                provider="azure_openai",
                connection_config={
                    "api_key": "provider-key",
                    "api_base": "https://example.azure.com/openai/v1",
                    "api_version": "2024-02-01",
                },
            )
        ]
    )

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "azure-gpt-4o-mini",
            "named_credential_id": "cred-azure",
            "deltallm_params": {
                "provider": "azure",
                "model": "azure/gpt-4o-mini",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["named_credential_id"] == "cred-azure"


@pytest.mark.asyncio
async def test_create_model_response_redacts_inline_api_key(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "inline-created-model",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
                "api_base": "https://api.openai.com/v1",
                "api_key": "provider-key",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["credential_source"] == "inline"
    assert payload["deltallm_params"]["api_key"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_create_model_rejects_invalid_access_groups(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "invalid-access-groups-model",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
                "api_base": "https://api.openai.com/v1",
                "api_key": "provider-key",
            },
            "model_info": {"mode": "chat", "access_groups": [123]},
        },
    )

    assert response.status_code == 400
    assert "access group key must be a string" in response.text


@pytest.mark.asyncio
async def test_create_model_response_uses_effective_named_credential_custom_auth_summary(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository(
        [
            NamedCredentialRecord(
                credential_id="cred-vllm",
                name="vLLM Shared Gateway",
                provider="vllm",
                connection_config={
                    "api_key": "provider-key",
                    "api_base": "https://vllm.example/v1",
                    "auth_header_format": "Token {api_key}",
                },
            )
        ]
    )

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "named-custom-auth-model",
            "named_credential_id": "cred-vllm",
            "deltallm_params": {
                "provider": "vllm",
                "model": "vllm/meta-llama/Llama-3.1-8B-Instruct",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["credential_source"] == "named"
    assert payload["named_credential_id"] == "cred-vllm"
    assert payload["connection_summary"]["api_base"] == "https://vllm.example/v1"
    assert payload["connection_summary"]["auth_header_name"] == "Authorization"
    assert payload["connection_summary"]["custom_auth_label"] == "Authorization (Token)"
    assert "auth_header_format" not in payload["deltallm_params"]


@pytest.mark.asyncio
async def test_create_model_accepts_custom_auth_headers_for_openai_compatible_provider(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "custom-auth-model",
            "deltallm_params": {
                "provider": "vllm",
                "model": "vllm/meta-llama/Llama-3.1-8B-Instruct",
                "api_base": "https://vllm.example/v1",
                "api_key": "provider-key",
                "auth_header_name": "X-API-Key",
                "auth_header_format": "{api_key}",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deltallm_params"]["api_key"] == "***REDACTED***"
    assert payload["deltallm_params"]["auth_header_name"] == "X-API-Key"
    assert payload["deltallm_params"]["auth_header_format"] == "{api_key}"
    assert payload["connection_summary"]["auth_header_name"] == "X-API-Key"
    assert payload["connection_summary"]["custom_auth_label"] == "X-API-Key"


@pytest.mark.asyncio
async def test_create_model_rejects_custom_auth_headers_for_azure_provider(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "azure-custom-auth-model",
            "deltallm_params": {
                "provider": "azure_openai",
                "model": "azure/gpt-4o-mini",
                "api_base": "https://example.azure.com/openai/v1",
                "api_key": "provider-key",
                "auth_header_name": "X-API-Key",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 400
    assert "Custom auth headers are not supported" in response.text


@pytest.mark.asyncio
async def test_create_model_rejects_reserved_auth_header_names(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "reserved-auth-header-model",
            "deltallm_params": {
                "provider": "vllm",
                "model": "vllm/meta-llama/Llama-3.1-8B-Instruct",
                "api_base": "https://vllm.example/v1",
                "api_key": "provider-key",
                "auth_header_name": "Content-Type",
                "auth_header_format": "Token {api_key}",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 400
    assert "reserved header name" in response.text


@pytest.mark.asyncio
async def test_update_model_response_redacts_inline_api_key(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")

    response = await client.put(
        "/ui/api/models/gpt-4o-mini-0",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "gpt-4o-mini",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
                "api_base": "https://api.openai.com/v1",
                "api_key": "provider-key-updated",
                "auth_header_name": "X-Provider-Auth",
                "auth_header_format": "Token {api_key}",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["credential_source"] == "inline"
    assert payload["deltallm_params"]["api_key"] == "***REDACTED***"
    assert payload["deltallm_params"]["auth_header_name"] == "X-Provider-Auth"
    assert payload["deltallm_params"]["auth_header_format"] == "Token {api_key}"
    assert payload["connection_summary"]["auth_header_name"] == "X-Provider-Auth"
    assert payload["connection_summary"]["custom_auth_label"] == "X-Provider-Auth"


@pytest.mark.asyncio
async def test_update_model_response_uses_effective_named_credential_custom_auth_summary(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository(
        [
            NamedCredentialRecord(
                credential_id="cred-vllm",
                name="vLLM Shared Gateway",
                provider="vllm",
                connection_config={
                    "api_key": "provider-key",
                    "api_base": "https://vllm.example/v1",
                    "auth_header_format": "Token {api_key}",
                },
            )
        ]
    )

    response = await client.put(
        "/ui/api/models/gpt-4o-mini-0",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "model_name": "named-custom-auth-model",
            "named_credential_id": "cred-vllm",
            "deltallm_params": {
                "provider": "vllm",
                "model": "vllm/meta-llama/Llama-3.1-8B-Instruct",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["credential_source"] == "named"
    assert payload["named_credential_id"] == "cred-vllm"
    assert payload["connection_summary"]["api_base"] == "https://vllm.example/v1"
    assert payload["connection_summary"]["auth_header_name"] == "Authorization"
    assert payload["connection_summary"]["custom_auth_label"] == "Authorization (Token)"
    assert "auth_header_format" not in payload["deltallm_params"]


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
