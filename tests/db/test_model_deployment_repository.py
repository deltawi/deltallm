from __future__ import annotations

import json

import pytest

from src.db.repositories import ModelDeploymentRecord, ModelDeploymentRepository


class FakePrisma:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, object]] = {}

    async def query_raw(self, query: str, *args):
        if "SELECT deployment_id, model_name, named_credential_id, deltallm_params, model_info" in query and "WHERE deployment_id = $1" not in query:
            values = sorted(self.rows.values(), key=lambda item: (str(item["model_name"]), str(item["deployment_id"])))
            return [dict(item) for item in values]
        if "WHERE deployment_id = $1" in query and "SELECT deployment_id, model_name, named_credential_id, deltallm_params, model_info" in query:
            deployment_id = str(args[0])
            row = self.rows.get(deployment_id)
            return [dict(row)] if row else []
        if "UPDATE deltallm_modeldeployment" in query:
            deployment_id = str(args[0])
            if deployment_id not in self.rows:
                return []
            self.rows[deployment_id] = {
                "deployment_id": deployment_id,
                "model_name": str(args[1]),
                "named_credential_id": str(args[2]) if args[2] is not None else None,
                "deltallm_params": json.loads(str(args[3])),
                "model_info": json.loads(str(args[4])) if args[4] is not None else None,
            }
            return [dict(self.rows[deployment_id])]
        if "DELETE FROM deltallm_modeldeployment" in query:
            deployment_id = str(args[0])
            existed = deployment_id in self.rows
            self.rows.pop(deployment_id, None)
            return [{"deployment_id": deployment_id}] if existed else []
        if "SELECT COUNT(*)::int AS count FROM deltallm_modeldeployment" in query:
            return [{"count": len(self.rows)}]
        return []

    async def execute_raw(self, query: str, *args):
        if "INSERT INTO deltallm_modeldeployment" in query:
            deployment_id = str(args[0])
            if "ON CONFLICT (deployment_id) DO NOTHING" in query and deployment_id in self.rows:
                return
            self.rows[deployment_id] = {
                "deployment_id": deployment_id,
                "model_name": str(args[1]),
                "named_credential_id": str(args[2]) if args[2] is not None else None,
                "deltallm_params": json.loads(str(args[3])),
                "model_info": json.loads(str(args[4])) if args[4] is not None else None,
            }


@pytest.mark.asyncio
async def test_model_deployment_repository_crud_roundtrip():
    repo = ModelDeploymentRepository(FakePrisma())

    record = ModelDeploymentRecord(
        deployment_id="dep-1",
        model_name="openai/gpt-4o-mini",
        deltallm_params={"model": "openai/gpt-4o-mini"},
        named_credential_id="cred-1",
        model_info={"priority": 1},
    )
    await repo.create(record)

    loaded = await repo.get_by_deployment_id("dep-1")
    assert loaded is not None
    assert loaded.model_name == "openai/gpt-4o-mini"
    assert loaded.named_credential_id == "cred-1"

    updated = await repo.update(
        "dep-1",
        model_name="openai/gpt-4.1-mini",
        named_credential_id=None,
        deltallm_params={"model": "openai/gpt-4.1-mini"},
        model_info={"priority": 2},
    )
    assert updated is not None
    assert updated.model_name == "openai/gpt-4.1-mini"
    assert updated.named_credential_id is None
    assert updated.model_info == {"priority": 2}

    rows = await repo.list_all()
    assert len(rows) == 1
    assert rows[0].deployment_id == "dep-1"

    deleted = await repo.delete("dep-1")
    assert deleted is True
    assert await repo.get_by_deployment_id("dep-1") is None


@pytest.mark.asyncio
async def test_model_deployment_repository_bulk_insert_if_empty_only_once():
    repo = ModelDeploymentRepository(FakePrisma())
    records = [
        ModelDeploymentRecord(
            deployment_id="dep-a",
            model_name="m-a",
            deltallm_params={"model": "openai/a"},
            model_info={},
        )
    ]

    assert await repo.bulk_insert_if_empty(records) is True
    assert await repo.bulk_insert_if_empty(
        [
            ModelDeploymentRecord(
                deployment_id="dep-b",
                model_name="m-b",
                deltallm_params={"model": "openai/b"},
                model_info={},
            )
        ]
    ) is False
    rows = await repo.list_all()
    assert [item.deployment_id for item in rows] == ["dep-a"]
