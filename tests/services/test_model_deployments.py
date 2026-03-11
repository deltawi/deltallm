from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.config import AppConfig
from src.db.repositories import ModelDeploymentRecord
from src.services.model_deployments import DuplicateModelNameError, bootstrap_model_deployments_from_config, load_model_registry


class FakeModelRepository:
    def __init__(self, records: list[ModelDeploymentRecord] | None = None) -> None:
        self.records = records or []
        self.bootstrap_payload: list[ModelDeploymentRecord] = []

    async def list_all(self) -> list[ModelDeploymentRecord]:
        return list(self.records)

    async def bulk_insert_if_empty(self, records: list[ModelDeploymentRecord]) -> bool:
        self.bootstrap_payload = list(records)
        if self.records:
            return False
        self.records = list(records)
        return True


@pytest.mark.asyncio
async def test_load_model_registry_prefers_db_records():
    settings = SimpleNamespace(openai_api_key="default-key", openai_base_url="https://api.openai.com/v1")
    cfg = AppConfig.model_validate(
        {
            "model_list": [
                {
                    "model_name": "fallback-model",
                    "deployment_id": "fallback-1",
                    "deltallm_params": {"model": "openai/fallback-model"},
                }
            ]
        }
    )
    repo = FakeModelRepository(
        records=[
            ModelDeploymentRecord(
                deployment_id="db-1",
                model_name="db-model",
                deltallm_params={"model": "openai/db-model"},
                model_info={"weight": 2},
            )
        ]
    )

    model_registry, source = await load_model_registry(repo, cfg, settings)

    assert source == "db"
    assert sorted(model_registry.keys()) == ["db-model"]
    assert model_registry["db-model"][0]["deployment_id"] == "db-1"
    assert model_registry["db-model"][0]["deltallm_params"]["api_key"] == "default-key"


@pytest.mark.asyncio
async def test_load_model_registry_falls_back_to_config_when_table_empty():
    settings = SimpleNamespace(openai_api_key="default-key", openai_base_url="https://api.openai.com/v1")
    cfg = AppConfig.model_validate(
        {
            "model_list": [
                {
                    "model_name": "gpt-4o-mini",
                    "deployment_id": "cfg-1",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                    "model_info": {"priority": 1},
                }
            ]
        }
    )
    repo = FakeModelRepository(records=[])

    model_registry, source = await load_model_registry(repo, cfg, settings)

    assert source == "config"
    assert model_registry["gpt-4o-mini"][0]["deployment_id"] == "cfg-1"
    assert model_registry["gpt-4o-mini"][0]["deltallm_params"]["api_key"] == "default-key"
    assert model_registry["gpt-4o-mini"][0]["model_info"]["priority"] == 1


@pytest.mark.asyncio
async def test_load_model_registry_db_only_raises_when_table_empty():
    settings = SimpleNamespace(openai_api_key="default-key", openai_base_url="https://api.openai.com/v1")
    cfg = AppConfig.model_validate(
        {
            "model_list": [
                {
                    "model_name": "gpt-4o-mini",
                    "deployment_id": "cfg-1",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                }
            ]
        }
    )
    repo = FakeModelRepository(records=[])

    with pytest.raises(RuntimeError, match="db_only"):
        await load_model_registry(repo, cfg, settings, source_mode="db_only")


@pytest.mark.asyncio
async def test_load_model_registry_config_only_ignores_db_records():
    settings = SimpleNamespace(openai_api_key="default-key", openai_base_url="https://api.openai.com/v1")
    cfg = AppConfig.model_validate(
        {
            "model_list": [
                {
                    "model_name": "cfg-model",
                    "deployment_id": "cfg-1",
                    "deltallm_params": {"model": "openai/cfg-model"},
                }
            ]
        }
    )
    repo = FakeModelRepository(
        records=[
            ModelDeploymentRecord(
                deployment_id="db-1",
                model_name="db-model",
                deltallm_params={"model": "openai/db-model"},
                model_info={},
            )
        ]
    )

    model_registry, source = await load_model_registry(repo, cfg, settings, source_mode="config_only")

    assert source == "config"
    assert sorted(model_registry.keys()) == ["cfg-model"]
    assert model_registry["cfg-model"][0]["deployment_id"] == "cfg-1"


@pytest.mark.asyncio
async def test_bootstrap_model_deployments_from_config_prepares_records():
    cfg = AppConfig.model_validate(
        {
            "model_list": [
                {
                    "model_name": "gpt-4o-mini",
                    "deployment_id": "dep-1",
                    "deltallm_params": {"model": "openai/gpt-4o-mini"},
                },
                {
                    "model_name": "gpt-4.1-mini",
                    "deltallm_params": {"model": "openai/gpt-4.1-mini"},
                },
            ]
        }
    )
    repo = FakeModelRepository(records=[])

    inserted = await bootstrap_model_deployments_from_config(repo, cfg)

    assert inserted is True
    assert [item.deployment_id for item in repo.bootstrap_payload] == ["dep-1", "gpt-4.1-mini-1"]
    assert [item.model_name for item in repo.bootstrap_payload] == ["gpt-4o-mini", "gpt-4.1-mini"]


@pytest.mark.asyncio
async def test_load_model_registry_rejects_duplicate_model_names_from_db() -> None:
    settings = SimpleNamespace(openai_api_key="default-key", openai_base_url="https://api.openai.com/v1")
    cfg = AppConfig.model_validate({})
    repo = FakeModelRepository(
        records=[
            ModelDeploymentRecord(
                deployment_id="db-1",
                model_name="shared-model",
                deltallm_params={"model": "openai/gpt-4o-mini"},
                model_info={},
            ),
            ModelDeploymentRecord(
                deployment_id="db-2",
                model_name="shared-model",
                deltallm_params={"model": "azure/gpt-4o-mini"},
                model_info={},
            ),
        ]
    )

    with pytest.raises(DuplicateModelNameError, match="Duplicate model_name 'shared-model' is not allowed"):
        await load_model_registry(repo, cfg, settings)
