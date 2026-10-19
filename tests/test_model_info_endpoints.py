from __future__ import annotations

from dataclasses import replace

import pytest

from src.db.routing.callable_targets import CallableTargetBindingRecord
from src.router.router import Deployment, build_deployment_registry
from src.services.access.callable_target_grants import CallableTargetGrantService
from src.services.access.callable_targets import build_callable_target_catalog
from src.services.models.public_model_info import deployment_info, model_group_info
from tests.conftest import InMemoryCallableTargetBindingRepository

_SECRETS = ("provider-key", "sk-secret", "https://internal.example", "AKIA", "aws-secret")


def _registry() -> dict[str, list[dict[str, object]]]:
    return {
        "gpt-4o-mini": [
            {
                "deployment_id": "dep-openai",
                "deltallm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "provider-key",
                    "api_base": "https://internal.example/v1",
                    "rpm": 100,
                },
                "model_info": {
                    "mode": "chat",
                    "max_input_tokens": 128000,
                    "max_output_tokens": 16384,
                    "input_cost_per_token": 0.00000015,
                    "output_cost_per_token": 0.0000006,
                    "input_cost_per_token_cache_hit": 0.000000075,
                    "tpm_limit": 1000,
                    "default_params": {"system": "internal prompt"},
                    "chat_capabilities": {
                        "tools": True,
                        "json_schema": True,
                        "image": True,
                        "streaming": True,
                    },
                },
            },
            {
                "deployment_id": "dep-azure",
                "deltallm_params": {
                    "model": "azure/gpt-4o-mini",
                    "provider": "azure",
                    "api_key": "sk-secret",
                    "rpm": 50,
                },
                "model_info": {
                    "mode": "chat",
                    "max_input_tokens": 64000,
                    "max_output_tokens": 0,
                    "input_cost_per_token": 0.0000002,
                    "output_cost_per_token": 0.0000006,
                    "tpm_limit": 500,
                    "chat_capabilities": {"tools": True, "image": False, "streaming": True},
                },
            },
        ],
        "titan": [
            {
                "deployment_id": "dep-bedrock",
                "deltallm_params": {
                    "model": "bedrock/amazon.titan-embed-text-v2:0",
                    "aws_access_key_id": "AKIA123",
                    "aws_secret_access_key": "aws-secret",
                },
                "model_info": {"mode": "embedding", "output_vector_size": 1024},
            }
        ],
        "hidden-model": [
            {
                "deployment_id": "dep-hidden",
                "deltallm_params": {"model": "openai/gpt-4o", "api_key": "provider-key"},
                "model_info": {"mode": "chat"},
            }
        ],
    }


async def _install(test_app, *, grants: list[str]) -> None:  # noqa: ANN001
    registry = _registry()
    route_groups = [
        {
            "key": "support-fast",
            "enabled": True,
            "mode": "chat",
            "members": [
                {"deployment_id": "dep-openai", "enabled": True},
                {"deployment_id": "dep-azure", "enabled": True},
            ],
        }
    ]
    record = next(iter(test_app.state._test_repo.records.values()))
    record.models = []
    service = CallableTargetGrantService(
        repository=InMemoryCallableTargetBindingRepository(
            [
                CallableTargetBindingRecord(
                    callable_target_binding_id=f"ctb-{key}",
                    callable_key=key,
                    scope_type="organization",
                    scope_id=str(record.organization_id),
                    enabled=True,
                )
                for key in grants
            ]
        ),
        policy_repository=None,
    )
    await service.reload()
    test_app.state.callable_target_grant_service = service
    store = test_app.state.routing_runtime_generation_store
    store.replace(
        replace(
            store.require_snapshot(),
            model_registry=registry,
            route_groups=tuple(route_groups),
            callable_target_catalog=build_callable_target_catalog(registry, route_groups),
            authorization_snapshot=service.snapshot(),
            deployment_registry=build_deployment_registry(registry, route_groups=route_groups),
        )
    )


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    return {"Authorization": f"Bearer {test_app.state._test_key}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/model/info", "/v1/model/info"])
async def test_model_info_lists_visible_deployments_without_secrets(client, test_app, path) -> None:
    await _install(test_app, grants=["gpt-4o-mini", "titan", "support-fast"])

    response = await client.get(path, headers=_headers(test_app))

    assert response.status_code == 200
    body = response.text
    for secret in _SECRETS:
        assert secret not in body
    assert "internal prompt" not in body
    rows = response.json()["data"]
    assert [(row["model_name"], row["model_info"]["id"]) for row in rows] == [
        ("gpt-4o-mini", "dep-openai"),
        ("gpt-4o-mini", "dep-azure"),
        ("support-fast", "dep-openai"),
        ("support-fast", "dep-azure"),
        ("titan", "dep-bedrock"),
    ]
    openai = rows[0]
    assert openai["litellm_params"] == {
        "model": "openai/gpt-4o-mini",
        "rpm": 100,
        "custom_llm_provider": "openai",
    }
    info = openai["model_info"]
    assert info["mode"] == "chat"
    assert info["litellm_provider"] == "openai"
    assert info["max_input_tokens"] == 128000
    assert info["max_output_tokens"] == 16384
    assert info["input_cost_per_token"] == 0.00000015
    assert info["cache_read_input_token_cost"] == 0.000000075
    assert info["rpm"] == 100
    assert info["tpm"] == 1000
    assert info["supports_function_calling"] is True
    assert info["supports_vision"] is True
    assert info["supports_response_schema"] is True
    assert info["supports_audio_input"] is False
    titan = rows[-1]["model_info"]
    assert titan["mode"] == "embedding"
    assert titan["output_vector_size"] == 1024
    assert titan["supports_function_calling"] is None


@pytest.mark.asyncio
async def test_model_info_filters_by_deployment_id(client, test_app) -> None:
    await _install(test_app, grants=["titan"])

    found = await client.get(
        "/v1/model/info", params={"litellm_model_id": "dep-bedrock"}, headers=_headers(test_app)
    )
    hidden = await client.get(
        "/v1/model/info", params={"litellm_model_id": "dep-hidden"}, headers=_headers(test_app)
    )

    assert [row["model_info"]["id"] for row in found.json()["data"]] == ["dep-bedrock"]
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_model_group_info_aggregates_conservatively(client, test_app) -> None:
    await _install(test_app, grants=["gpt-4o-mini", "support-fast"])

    response = await client.get("/model_group/info", headers=_headers(test_app))

    assert response.status_code == 200
    rows = {row["model_group"]: row for row in response.json()["data"]}
    assert set(rows) == {"gpt-4o-mini", "support-fast"}
    group = rows["gpt-4o-mini"]
    assert group["target_type"] == "model"
    assert group["providers"] == ["azure", "openai"]
    assert group["mode"] == "chat"
    assert group["max_input_tokens"] == 64000
    assert group["max_output_tokens"] == 16384
    assert group["input_cost_per_token"] == 0.0000002
    assert group["rpm"] == 150
    assert group["tpm"] == 1500
    assert group["supports_function_calling"] is True
    assert group["supports_vision"] is False
    assert group["supports_response_schema"] is False
    assert rows["support-fast"]["target_type"] == "route_group"
    assert rows["support-fast"]["providers"] == ["azure", "openai"]


@pytest.mark.asyncio
async def test_model_group_info_filter_respects_access(client, test_app) -> None:
    await _install(test_app, grants=["titan"])

    visible = await client.get(
        "/model_group/info", params={"model_group": "titan"}, headers=_headers(test_app)
    )
    hidden = await client.get(
        "/model_group/info", params={"model_group": "hidden-model"}, headers=_headers(test_app)
    )

    assert [row["model_group"] for row in visible.json()["data"]] == ["titan"]
    assert hidden.json() == {"data": []}


@pytest.mark.asyncio
async def test_model_info_accepts_x_api_key(client, test_app) -> None:
    await _install(test_app, grants=["titan"])

    response = await client.get(
        "/model_group/info", headers={"x-api-key": test_app.state._test_key}
    )

    assert response.status_code == 200
    assert [row["model_group"] for row in response.json()["data"]] == ["titan"]


@pytest.mark.asyncio
async def test_model_info_requires_api_key(client) -> None:
    for path in ("/model/info", "/v1/model/info", "/model_group/info", "/v1/models/titan"):
        response = await client.get(path)
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_retrieve_model_respects_access(client, test_app) -> None:
    await _install(test_app, grants=["titan", "support-fast"])

    found = await client.get("/v1/models/titan", headers=_headers(test_app))
    hidden = await client.get("/v1/models/hidden-model", headers=_headers(test_app))
    unknown = await client.get("/v1/models/nope", headers=_headers(test_app))

    assert found.status_code == 200
    assert found.json()["id"] == "titan"
    assert found.json()["object"] == "model"
    assert hidden.status_code == 404
    assert unknown.status_code == 404
    assert hidden.json()["error"]["type"] == unknown.json()["error"]["type"] == "model_not_found"


def test_model_group_info_without_deployments_is_unknown() -> None:
    row = model_group_info("empty", [], mode="chat", target_type="route_group")

    assert row["providers"] == []
    assert row["max_input_tokens"] is None
    assert row["rpm"] is None
    assert row["supports_function_calling"] is None


def test_deployment_info_never_copies_unlisted_params() -> None:
    deployment = Deployment(
        deployment_id="dep-1",
        model_name="m",
        deltallm_params={
            "model": "openai/x",
            "auth_header_name": "X-Secret",
            "auth_header_format": "Token {api_key}",
            "aws_session_token": "token",
            "region": "us-east-1",
        },
    )

    row = deployment_info("m", deployment, mode=None)

    assert row["litellm_params"] == {"model": "openai/x", "custom_llm_provider": "openai"}
    assert row["model_info"]["mode"] is None
