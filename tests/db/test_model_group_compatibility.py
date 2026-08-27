from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.db.repositories import ModelDeploymentRecord, ModelDeploymentRepository
from src.services.model_deployments import build_model_registry_from_records
from tests.db.tier_migration_helpers import connect_prisma


@pytest.mark.asyncio
@pytest.mark.integration
async def test_database_and_registry_preserve_implicit_model_group_members() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    model_name = f"implicit-model-group-{suffix}"
    deployment_ids = [f"implicit-deployment-a-{suffix}", f"implicit-deployment-b-{suffix}"]
    repository = ModelDeploymentRepository(db)
    try:
        indexes = await db.query_raw(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'deltallm_modeldeployment'
              AND indexname IN (
                'deltallm_modeldeployment_model_name_key',
                'deltallm_modeldeployment_model_name_idx'
              )
            """
        )
        assert [str(index["indexname"]) for index in indexes] == [
            "deltallm_modeldeployment_model_name_idx"
        ]
        assert "UNIQUE" not in str(indexes[0]["indexdef"])

        for deployment_id, upstream_model in zip(
            deployment_ids,
            ("openai/gpt-4o-mini", "azure/gpt-4o-mini"),
            strict=True,
        ):
            await repository.create(
                ModelDeploymentRecord(
                    deployment_id=deployment_id,
                    model_name=model_name,
                    deltallm_params={"model": upstream_model},
                    model_info={"mode": "chat"},
                )
            )

        records = [
            record for record in await repository.list_all() if record.model_name == model_name
        ]
        registry = await build_model_registry_from_records(
            records,
            SimpleNamespace(openai_api_key=None, openai_base_url=None),
        )

        assert [record.deployment_id for record in records] == deployment_ids
        assert [
            deployment["deployment_id"] for deployment in registry[model_name]
        ] == deployment_ids
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_modeldeployment WHERE deployment_id IN ($1, $2)",
            *deployment_ids,
        )
        await db.disconnect()
