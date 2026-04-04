from __future__ import annotations

from datetime import UTC, datetime

import pytest
from src.db.named_credentials import NamedCredentialRecord
from src.services.named_credentials import connection_fingerprint


class _FakeNamedCredentialRepository:
    def __init__(self) -> None:
        self.records: dict[str, NamedCredentialRecord] = {}
        self.usage_counts: dict[str, int] = {}

    async def list_all(self, *, provider: str | None = None) -> list[NamedCredentialRecord]:
        records = list(self.records.values())
        if provider:
            records = [record for record in records if record.provider == provider]
        return sorted(records, key=lambda item: item.name)

    async def list_usage_counts(self) -> dict[str, int]:
        return dict(self.usage_counts)

    async def get_by_id(self, credential_id: str) -> NamedCredentialRecord | None:
        return self.records.get(credential_id)

    async def get_by_name(self, name: str) -> NamedCredentialRecord | None:
        for record in self.records.values():
            if record.name == name:
                return record
        return None

    async def create(self, record: NamedCredentialRecord) -> NamedCredentialRecord:
        now = datetime.now(tz=UTC)
        stored = NamedCredentialRecord(
            credential_id=record.credential_id,
            name=record.name,
            provider=record.provider,
            connection_config=dict(record.connection_config),
            metadata=dict(record.metadata) if record.metadata is not None else None,
            created_by_account_id=record.created_by_account_id,
            created_at=now,
            updated_at=now,
        )
        self.records[record.credential_id] = stored
        return stored

    async def update(
        self,
        credential_id: str,
        *,
        name: str,
        provider: str,
        connection_config: dict[str, object],
        metadata: dict[str, object] | None,
    ) -> NamedCredentialRecord | None:
        existing = self.records.get(credential_id)
        if existing is None:
            return None
        updated = NamedCredentialRecord(
            credential_id=credential_id,
            name=name,
            provider=provider,
            connection_config=dict(connection_config),
            metadata=dict(metadata) if metadata is not None else None,
            created_by_account_id=existing.created_by_account_id,
            created_at=existing.created_at,
            updated_at=datetime.now(tz=UTC),
        )
        self.records[credential_id] = updated
        return updated

    async def delete(self, credential_id: str) -> bool:
        return self.records.pop(credential_id, None) is not None

    async def count_linked_deployments(self, credential_id: str) -> int:
        return int(self.usage_counts.get(credential_id, 0))

    async def list_linked_deployments(self, credential_id: str, *, limit: int = 25) -> list[dict[str, str]]:
        del limit
        if self.usage_counts.get(credential_id):
            return [{"deployment_id": "dep-1", "model_name": "gpt-4o-mini"}]
        return []


class _FakeHotReloadManager:
    def __init__(self) -> None:
        self.reloads = 0

    async def reload_runtime(self) -> None:
        self.reloads += 1


class _FakeModelDeploymentRepository:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = {str(record["deployment_id"]): dict(record) for record in records}

    async def list_all(self):  # noqa: ANN201
        from src.db.repositories import ModelDeploymentRecord

        return [
            ModelDeploymentRecord(
                deployment_id=str(record["deployment_id"]),
                model_name=str(record["model_name"]),
                named_credential_id=str(record["named_credential_id"]) if record.get("named_credential_id") is not None else None,
                deltallm_params=dict(record["deltallm_params"]),
                model_info=dict(record.get("model_info") or {}),
            )
            for record in self.records.values()
        ]

    async def list_by_deployment_ids(self, deployment_ids):  # noqa: ANN201
        from src.db.repositories import ModelDeploymentRecord

        results = []
        for deployment_id in deployment_ids:
            record = self.records.get(str(deployment_id))
            if record is None:
                continue
            results.append(
                ModelDeploymentRecord(
                    deployment_id=str(record["deployment_id"]),
                    model_name=str(record["model_name"]),
                    named_credential_id=str(record["named_credential_id"]) if record.get("named_credential_id") is not None else None,
                    deltallm_params=dict(record["deltallm_params"]),
                    model_info=dict(record.get("model_info") or {}),
                )
            )
        return results

    async def update(self, deployment_id: str, *, model_name: str, named_credential_id: str | None, deltallm_params: dict[str, object], model_info: dict[str, object] | None):  # noqa: ANN201
        record = self.records.get(deployment_id)
        if record is None:
            return None
        record["model_name"] = model_name
        record["named_credential_id"] = named_credential_id
        record["deltallm_params"] = dict(deltallm_params)
        record["model_info"] = dict(model_info or {})
        return record


class _FailingModelDeploymentRepository(_FakeModelDeploymentRepository):
    def __init__(self, records: list[dict[str, object]], *, fail_on_deployment_id: str) -> None:
        super().__init__(records)
        self.fail_on_deployment_id = fail_on_deployment_id

    async def update(self, deployment_id: str, *, model_name: str, named_credential_id: str | None, deltallm_params: dict[str, object], model_info: dict[str, object] | None):  # noqa: ANN201
        if deployment_id == self.fail_on_deployment_id:
            raise RuntimeError("simulated update failure")
        return await super().update(
            deployment_id,
            model_name=model_name,
            named_credential_id=named_credential_id,
            deltallm_params=deltallm_params,
            model_info=model_info,
        )


@pytest.mark.asyncio
async def test_named_credentials_create_and_list_are_redacted(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    test_app.state.named_credential_repository = repository

    create_response = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "OpenAI Prod",
            "provider": "openai",
            "connection_config": {
                "api_key": "sk-secret",
                "api_base": "https://api.openai.com/v1",
            },
        },
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["connection_config"]["api_key"] == "***REDACTED***"
    assert created["connection_config"]["api_base"] == "https://api.openai.com/v1"
    assert created["credentials_present"] is True
    assert created["usage_count"] == 0

    list_response = await client.get(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert list_response.status_code == 200
    payload = list_response.json()
    assert payload["data"][0]["name"] == "OpenAI Prod"
    assert payload["data"][0]["connection_config"]["api_key"] == "***REDACTED***"


@pytest.mark.asyncio
async def test_named_credentials_update_reloads_runtime_when_in_use_and_delete_blocks(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    hot_reload = _FakeHotReloadManager()
    record = await repository.create(
        NamedCredentialRecord(
            credential_id="cred-1",
            name="OpenAI Prod",
            provider="openai",
            connection_config={"api_key": "sk-secret", "api_base": "https://api.openai.com/v1"},
        )
    )
    repository.usage_counts[record.credential_id] = 1
    test_app.state.named_credential_repository = repository
    test_app.state.model_hot_reload_manager = hot_reload

    update_response = await client.put(
        "/ui/api/named-credentials/cred-1",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "OpenAI Prod",
            "provider": "openai",
            "connection_config": {"api_key": "sk-rotated", "api_base": "https://api.openai.com/v1"},
        },
    )

    assert update_response.status_code == 200
    assert hot_reload.reloads == 1

    delete_response = await client.delete(
        "/ui/api/named-credentials/cred-1",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert delete_response.status_code == 409


@pytest.mark.asyncio
async def test_named_credentials_reject_provider_change_on_update(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    await repository.create(
        NamedCredentialRecord(
            credential_id="cred-1",
            name="OpenAI Prod",
            provider="openai",
            connection_config={"api_key": "sk-secret", "api_base": "https://api.openai.com/v1"},
        )
    )
    test_app.state.named_credential_repository = repository

    response = await client.put(
        "/ui/api/named-credentials/cred-1",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "OpenAI Prod",
            "provider": "anthropic",
            "connection_config": {"api_key": "sk-new"},
        },
    )

    assert response.status_code == 400
    assert "provider cannot be changed" in response.text


@pytest.mark.asyncio
async def test_named_credentials_validate_provider_specific_fields(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    test_app.state.named_credential_repository = repository

    invalid_openai = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "OpenAI Prod",
            "provider": "openai",
            "connection_config": {"aws_access_key_id": "AKIA...", "api_key": "sk-secret"},
        },
    )
    assert invalid_openai.status_code == 400
    assert "unsupported fields" in invalid_openai.text

    invalid_bedrock = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "Bedrock Prod",
            "provider": "bedrock",
            "connection_config": {"aws_access_key_id": "AKIA...", "region": "us-east-1"},
        },
    )
    assert invalid_bedrock.status_code == 400
    assert "require both aws_access_key_id and aws_secret_access_key" in invalid_bedrock.text


@pytest.mark.asyncio
async def test_named_credentials_update_preserves_omitted_secret_fields(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    await repository.create(
        NamedCredentialRecord(
            credential_id="cred-1",
            name="OpenAI Prod",
            provider="openai",
            connection_config={"api_key": "sk-secret", "api_base": "https://api.openai.com/v1"},
        )
    )
    test_app.state.named_credential_repository = repository

    response = await client.put(
        "/ui/api/named-credentials/cred-1",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "OpenAI Prod",
            "provider": "openai",
            "connection_config": {"api_base": "https://proxy.example/v1"},
        },
    )

    assert response.status_code == 200
    stored = await repository.get_by_id("cred-1")
    assert stored is not None
    assert stored.connection_config["api_key"] == "sk-secret"
    assert stored.connection_config["api_base"] == "https://proxy.example/v1"


@pytest.mark.asyncio
async def test_named_credentials_update_clears_secret_field_when_null(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    await repository.create(
        NamedCredentialRecord(
            credential_id="cred-1",
            name="OpenAI Prod",
            provider="openai",
            connection_config={"api_key": "sk-secret", "api_base": "https://api.openai.com/v1"},
        )
    )
    test_app.state.named_credential_repository = repository

    response = await client.put(
        "/ui/api/named-credentials/cred-1",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "OpenAI Prod",
            "provider": "openai",
            "connection_config": {"api_key": None},
        },
    )

    assert response.status_code == 200
    stored = await repository.get_by_id("cred-1")
    assert stored is not None
    assert "api_key" not in stored.connection_config


@pytest.mark.asyncio
async def test_inline_named_credential_report_redacts_connection_config(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    test_app.state.model_deployment_repository = _FakeModelDeploymentRepository(
        [
            {
                "deployment_id": "dep-1",
                "model_name": "gpt-4o-mini",
                "named_credential_id": None,
                "deltallm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-secret",
                    "api_base": "https://api.openai.com/v1",
                },
                "model_info": {"mode": "chat"},
            }
        ]
    )

    response = await client.get(
        "/ui/api/named-credentials/inline-report",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"][0]["provider"] == "openai"
    assert payload["data"][0]["connection_config"]["api_key"] == "***REDACTED***"
    assert payload["data"][0]["connection_config"]["api_base"] == "https://api.openai.com/v1"


@pytest.mark.asyncio
async def test_convert_inline_group_creates_named_credential_and_links_deployments(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    named_repository = _FakeNamedCredentialRepository()
    model_repository = _FakeModelDeploymentRepository(
        [
            {
                "deployment_id": "dep-1",
                "model_name": "gpt-4o-mini",
                "named_credential_id": None,
                "deltallm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-secret",
                    "api_base": "https://api.openai.com/v1",
                },
                "model_info": {"mode": "chat"},
            },
            {
                "deployment_id": "dep-2",
                "model_name": "gpt-4.1-mini",
                "named_credential_id": None,
                "deltallm_params": {
                    "model": "openai/gpt-4.1-mini",
                    "api_key": "sk-secret",
                    "api_base": "https://api.openai.com/v1",
                },
                "model_info": {"mode": "chat"},
            },
        ]
    )
    hot_reload = _FakeHotReloadManager()
    test_app.state.named_credential_repository = named_repository
    test_app.state.model_deployment_repository = model_repository
    test_app.state.model_hot_reload_manager = hot_reload

    response = await client.post(
        "/ui/api/named-credentials/convert-inline-group",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "OpenAI Shared",
            "provider": "openai",
            "fingerprint": connection_fingerprint(
                "openai",
                {
                    "api_key": "sk-secret",
                    "api_base": "https://api.openai.com/v1",
                },
            ),
            "deployment_ids": ["dep-1", "dep-2"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["credential"]["name"] == "OpenAI Shared"
    assert payload["credential"]["connection_config"]["api_key"] == "***REDACTED***"
    assert hot_reload.reloads == 1
    assert model_repository.records["dep-1"]["named_credential_id"] is not None
    assert "api_key" not in model_repository.records["dep-1"]["deltallm_params"]


@pytest.mark.asyncio
async def test_convert_inline_group_rolls_back_created_credential_on_non_transaction_failure(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    named_repository = _FakeNamedCredentialRepository()
    model_repository = _FailingModelDeploymentRepository(
        [
            {
                "deployment_id": "dep-1",
                "model_name": "gpt-4o-mini",
                "named_credential_id": None,
                "deltallm_params": {
                    "model": "openai/gpt-4o-mini",
                    "api_key": "sk-secret",
                    "api_base": "https://api.openai.com/v1",
                },
                "model_info": {"mode": "chat"},
            },
            {
                "deployment_id": "dep-2",
                "model_name": "gpt-4.1-mini",
                "named_credential_id": None,
                "deltallm_params": {
                    "model": "openai/gpt-4.1-mini",
                    "api_key": "sk-secret",
                    "api_base": "https://api.openai.com/v1",
                },
                "model_info": {"mode": "chat"},
            },
        ],
        fail_on_deployment_id="dep-2",
    )
    test_app.state.named_credential_repository = named_repository
    test_app.state.model_deployment_repository = model_repository
    test_app.state.model_hot_reload_manager = _FakeHotReloadManager()

    with pytest.raises(RuntimeError, match="simulated update failure"):
        await client.post(
            "/ui/api/named-credentials/convert-inline-group",
            headers={"Authorization": "Bearer mk-test"},
            json={
                "name": "OpenAI Shared",
                "provider": "openai",
                "fingerprint": connection_fingerprint(
                    "openai",
                    {
                        "api_key": "sk-secret",
                        "api_base": "https://api.openai.com/v1",
                    },
                ),
                "deployment_ids": ["dep-1", "dep-2"],
            },
        )

    assert named_repository.records == {}
    assert model_repository.records["dep-1"]["named_credential_id"] is None
    assert model_repository.records["dep-1"]["deltallm_params"]["api_key"] == "sk-secret"
