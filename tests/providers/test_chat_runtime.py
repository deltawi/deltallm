from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.config import AppConfig
from src.config_runtime.secrets import SecretResolver
from src.db.named_credentials import NamedCredentialRecord
from src.db.repositories import ModelDeploymentRecord
from src.services.model_deployments import (
    build_model_registry_from_config,
    build_model_registry_from_records,
    resolve_runtime_deltallm_params,
)
from tests.services.test_model_deployments import FakeNamedCredentialRepository


def test_runtime_uses_provider_defaults_without_inheriting_openai_credentials(contract):
    settings = SimpleNamespace(
        openai_api_key="unrelated-key", openai_base_url="https://openai.invalid"
    )
    result = resolve_runtime_deltallm_params(
        {
            "model": f"{contract['provider']}/{contract['model']}",
        },
        settings,
    )
    assert result["api_base"] == contract["api_base"]
    assert not result.get("api_key")
    override = resolve_runtime_deltallm_params(
        {
            "provider": contract["provider"],
            "model": contract["model"],
            "api_key": "correct-key",
            "api_base": "https://regional.example/v1",
        },
        settings,
    )
    assert override["api_base"] == "https://regional.example/v1"
    assert override["api_key"] == "correct-key"


@pytest.mark.parametrize("source", ["config", "records"])
async def test_named_credentials_resolve_on_reload_for_both_registry_sources(
    contract, monkeypatch, source
):
    settings = SimpleNamespace(
        openai_api_key="unrelated-key", openai_base_url="https://openai.invalid"
    )
    credential = NamedCredentialRecord(
        credential_id="provider-credential",
        name="Direct chat",
        provider=contract["provider"],
        connection_config={"api_key": "os.environ/PROVIDER_RELOAD_TEST_KEY"},
    )
    repository = FakeNamedCredentialRepository([credential])
    params = {"provider": contract["provider"], "model": contract["model"]}
    entry = {
        "deployment_id": "direct-chat",
        "model_name": "direct-chat",
        "named_credential_id": credential.credential_id,
        "deltallm_params": params,
        "model_info": {"mode": "chat"},
    }
    snapshots = []
    for value in ("first-key", "rotated-key"):
        monkeypatch.setenv("PROVIDER_RELOAD_TEST_KEY", value)
        if source == "config":
            registry = await build_model_registry_from_config(
                AppConfig.model_validate({"model_list": [entry]}),
                settings,
                named_credential_repository=repository,
                secret_resolver=SecretResolver(),
            )
        else:
            registry = await build_model_registry_from_records(
                [ModelDeploymentRecord(**entry)],
                settings,
                named_credential_repository=repository,
                secret_resolver=SecretResolver(),
            )
        snapshots.append(registry)
        resolved = registry["direct-chat"][0]["deltallm_params"]
        assert resolved["api_key"] == value
        assert resolved["api_base"] == contract["api_base"]
    assert snapshots[0]["direct-chat"][0]["deltallm_params"]["api_key"] == "first-key"
    assert credential.connection_config["api_key"] == "os.environ/PROVIDER_RELOAD_TEST_KEY"


@pytest.mark.parametrize(
    ("binding_state", "credential_available"),
    [("revoked", True), ("active", False)],
)
async def test_creator_model_missing_or_revoked_credential_never_uses_platform_defaults(
    binding_state: str,
    credential_available: bool,
) -> None:
    settings = SimpleNamespace(
        openai_api_key="platform-key-must-not-leak",
        openai_base_url="https://platform-default.invalid/v1",
    )
    credential = NamedCredentialRecord(
        credential_id="private-credential",
        name="Private provider",
        provider="openai",
        connection_config={
            "api_key": "owner-key",
            "api_base": "https://owner.example/v1",
        },
    )
    repository = FakeNamedCredentialRepository([credential] if credential_available else [])
    record = ModelDeploymentRecord(
        deployment_id="creator-deployment",
        model_name="creator/model",
        model_id="logical-model",
        named_credential_id="private-credential",
        credential_binding_mode="owner_delegated",
        credential_binding_state=binding_state,
        credential_bound_by_account_id="owner-account",
        governance_source="creator",
        deltallm_params={"provider": "openai", "model": "openai/gpt-4o-mini"},
        model_info={"mode": "chat"},
    )

    registry = await build_model_registry_from_records(
        [record],
        settings,
        named_credential_repository=repository,
    )

    assert registry == {}


async def test_creator_model_with_unresolved_credential_secret_is_excluded(
    monkeypatch,
) -> None:
    monkeypatch.delenv("MISSING_CREATOR_MODEL_KEY", raising=False)
    settings = SimpleNamespace(
        openai_api_key="platform-key-must-not-leak",
        openai_base_url="https://platform-default.invalid/v1",
    )
    credential = NamedCredentialRecord(
        credential_id="private-credential",
        name="Private provider",
        provider="openai",
        connection_config={
            "api_key": "os.environ/MISSING_CREATOR_MODEL_KEY",
            "api_base": "https://owner.example/v1",
        },
    )
    record = ModelDeploymentRecord(
        deployment_id="creator-deployment",
        model_name="creator/model",
        model_id="logical-model",
        named_credential_id=credential.credential_id,
        credential_binding_mode="owner_delegated",
        credential_binding_state="active",
        credential_bound_by_account_id="owner-account",
        governance_source="creator",
        deltallm_params={"provider": "openai", "model": "openai/gpt-4o-mini"},
        model_info={"mode": "chat"},
    )

    registry = await build_model_registry_from_records(
        [record],
        settings,
        named_credential_repository=FakeNamedCredentialRepository([credential]),
        secret_resolver=SecretResolver(),
    )

    assert registry == {}
