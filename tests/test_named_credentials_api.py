from __future__ import annotations

from datetime import UTC, datetime

import pytest
from src.db.catalog.named_credentials import NamedCredentialRecord
from src.models.platform_auth import PlatformAuthContext
from src.services.access.managed_asset_access import (
    AssetAccessPolicy,
    AssetKind,
    AssetPrincipal,
    GovernanceSource,
    ManagedAsset,
    resolve_asset_capabilities,
)
from src.services.models.named_credentials import connection_fingerprint


class _FakeNamedCredentialRepository:
    def __init__(self) -> None:
        self.records: dict[str, NamedCredentialRecord] = {}
        self.usage_counts: dict[str, int] = {}

    async def list_all(self, *, provider: str | None = None) -> list[NamedCredentialRecord]:
        records = list(self.records.values())
        if provider:
            records = [record for record in records if record.provider == provider]
        return sorted(records, key=lambda item: item.name)

    async def list_by_managed_asset_ids(
        self,
        managed_asset_ids: list[str],
        *,
        provider: str | None = None,
    ) -> list[NamedCredentialRecord]:
        records = [
            record
            for record in self.records.values()
            if record.managed_asset_id in managed_asset_ids
        ]
        if provider:
            records = [record for record in records if record.provider == provider]
        return sorted(records, key=lambda item: item.name)

    async def list_usage_counts(
        self,
        credential_ids: list[str] | None = None,
    ) -> dict[str, int]:
        if credential_ids is None:
            return dict(self.usage_counts)
        return {
            credential_id: count
            for credential_id, count in self.usage_counts.items()
            if credential_id in credential_ids
        }

    async def get_by_id(self, credential_id: str) -> NamedCredentialRecord | None:
        return self.records.get(credential_id)

    async def get_by_name(
        self, name: str, *, name_scope: str = "platform"
    ) -> NamedCredentialRecord | None:
        for record in self.records.values():
            if record.name == name and record.name_scope == name_scope:
                return record
        return None

    async def create(self, record: NamedCredentialRecord) -> NamedCredentialRecord:
        now = datetime.now(tz=UTC)
        stored = NamedCredentialRecord(
            credential_id=record.credential_id,
            name=record.name,
            provider=record.provider,
            connection_config=dict(record.connection_config),
            name_scope=record.name_scope,
            metadata=dict(record.metadata) if record.metadata is not None else None,
            created_by_account_id=record.created_by_account_id,
            managed_asset_id=record.managed_asset_id,
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
            name_scope=existing.name_scope,
            metadata=dict(metadata) if metadata is not None else None,
            created_by_account_id=existing.created_by_account_id,
            managed_asset_id=existing.managed_asset_id,
            created_at=existing.created_at,
            updated_at=datetime.now(tz=UTC),
        )
        self.records[credential_id] = updated
        return updated

    async def delete(self, credential_id: str) -> bool:
        return self.records.pop(credential_id, None) is not None

    async def count_linked_deployments(self, credential_id: str) -> int:
        return int(self.usage_counts.get(credential_id, 0))

    async def list_linked_deployments(
        self, credential_id: str, *, limit: int = 25
    ) -> list[dict[str, str]]:
        del limit
        if self.usage_counts.get(credential_id):
            return [{"deployment_id": "dep-1", "model_name": "gpt-4o-mini"}]
        return []


class _FakeManagedAssetAccessRepository:
    prisma = None

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app
        self.policies: dict[str, AssetAccessPolicy] = {}

    async def create_policy(
        self,
        policy: AssetAccessPolicy,
        *,
        created_by_account_id: str | None,
    ) -> AssetAccessPolicy:
        del created_by_account_id
        self.policies[policy.asset.asset_id] = policy
        return policy

    async def delete_policy(self, asset_id: str) -> bool:
        return self.policies.pop(asset_id, None) is not None

    async def get_policy(self, asset_id: str) -> AssetAccessPolicy | None:
        return self.policies.get(asset_id)

    async def get_accessible_policy(
        self,
        asset_id: str,
        principal: AssetPrincipal,
    ) -> AssetAccessPolicy | None:
        policy = self.policies.get(asset_id)
        if policy is None or not resolve_asset_capabilities(policy, principal).can_read:
            return None
        return policy

    async def replace_grant(
        self,
        policy: AssetAccessPolicy,
        *,
        expected_policy_version: int,
        changed_by_account_id: str | None,
    ) -> AssetAccessPolicy:
        del changed_by_account_id
        current = self.policies[policy.asset.asset_id]
        if current.asset.policy_version != expected_policy_version:
            from src.db.catalog.managed_assets import ManagedAssetPolicyConflictError

            raise ManagedAssetPolicyConflictError("asset policy changed")
        updated = AssetAccessPolicy(
            asset=ManagedAsset(
                asset_id=policy.asset.asset_id,
                asset_kind=policy.asset.asset_kind,
                governance_source=policy.asset.governance_source,
                owner_account_id=policy.asset.owner_account_id,
                policy_version=expected_policy_version + 1,
                state=policy.asset.state,
            ),
            grant=policy.grant,
        )
        self.policies[policy.asset.asset_id] = updated
        return updated

    def _legacy_policy(self, credential_id: str) -> AssetAccessPolicy | None:
        named_repository = getattr(self.app.state, "named_credential_repository", None)
        record = getattr(named_repository, "records", {}).get(credential_id)
        if record is None:
            return None
        asset_id = record.managed_asset_id or f"legacy-{credential_id}"
        return AssetAccessPolicy(
            asset=ManagedAsset(
                asset_id=asset_id,
                asset_kind=AssetKind.NAMED_CREDENTIAL,
                governance_source=GovernanceSource.PLATFORM,
                owner_account_id=None,
            )
        )

    async def get_policy_for_resource(
        self,
        asset_kind: AssetKind,
        resource_id: str,
        *,
        principal: AssetPrincipal | None = None,
    ) -> AssetAccessPolicy | None:
        assert asset_kind is AssetKind.NAMED_CREDENTIAL
        named_repository = getattr(self.app.state, "named_credential_repository", None)
        record = getattr(named_repository, "records", {}).get(resource_id)
        if record is None:
            return None
        if record.managed_asset_id in self.policies:
            policy = self.policies[record.managed_asset_id]
        else:
            policy = self._legacy_policy(resource_id)
        if policy is None or principal is None:
            return policy
        return policy if resolve_asset_capabilities(policy, principal).can_read else None

    async def list_accessible_policies(
        self,
        asset_kind: AssetKind,
        principal: AssetPrincipal,
    ) -> list[AssetAccessPolicy]:
        assert asset_kind is AssetKind.NAMED_CREDENTIAL
        named_repository = getattr(self.app.state, "named_credential_repository", None)
        policies: list[AssetAccessPolicy] = []
        for credential_id, record in getattr(named_repository, "records", {}).items():
            policy = self.policies.get(record.managed_asset_id or "")
            policy = policy or self._legacy_policy(credential_id)
            if policy is not None and resolve_asset_capabilities(policy, principal).can_read:
                policies.append(policy)
        return policies


@pytest.fixture(autouse=True)
def _managed_asset_access_repository(test_app):  # noqa: ANN001, ANN202
    test_app.state.managed_asset_access_repository = _FakeManagedAssetAccessRepository(test_app)


class _IdentityService:
    def __init__(self, context: PlatformAuthContext) -> None:
        self.context = context

    async def get_context_for_session(self, token: str) -> PlatformAuthContext | None:
        return self.context if token == "asset-session" else None


def _user_context(
    account_id: str,
    *,
    team_ids: tuple[str, ...] = (),
    organization_ids: tuple[str, ...] = (),
) -> PlatformAuthContext:
    return PlatformAuthContext(
        account_id=account_id,
        email=f"{account_id}@example.com",
        role="org_user",
        organization_memberships=[
            {"organization_id": organization_id, "role": "org_member"}
            for organization_id in organization_ids
        ],
        team_memberships=[{"team_id": team_id, "role": "team_developer"} for team_id in team_ids],
    )


@pytest.mark.asyncio
async def test_creator_owns_private_named_credential_and_sees_only_accessible_rows(
    client,
    test_app,
):
    repository = _FakeNamedCredentialRepository()
    test_app.state.named_credential_repository = repository
    identity = _IdentityService(_user_context("acct-owner"))
    test_app.state.platform_identity_service = identity

    created_response = await client.post(
        "/ui/api/named-credentials",
        cookies={"deltallm_session": "asset-session"},
        json={
            "name": "Creator OpenAI",
            "provider": "openai",
            "connection_config": {"api_key": "sk-secret"},
        },
    )

    assert created_response.status_code == 200
    created = created_response.json()
    assert created["connection_config"]["api_key"] == "***REDACTED***"
    assert created["access"]["governance_source"] == "creator"
    assert created["access"]["visibility"] == "private"
    assert created["access"]["effective_role"] == "owner"
    assert created["access"]["capabilities"]["manage_access"] is True

    identity.context = _user_context("acct-other")
    other_list = await client.get(
        "/ui/api/named-credentials",
        cookies={"deltallm_session": "asset-session"},
    )
    assert other_list.status_code == 200
    assert other_list.json()["data"] == []


@pytest.mark.asyncio
async def test_creator_can_share_named_credential_with_multiple_teams(
    client,
    test_app,
):
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository()
    test_app.state.platform_identity_service = _IdentityService(
        _user_context("acct-owner", team_ids=("team-1", "team-2"))
    )

    response = await client.post(
        "/ui/api/named-credentials",
        cookies={"deltallm_session": "asset-session"},
        json={
            "name": "Shared credential",
            "provider": "openai",
            "connection_config": {"api_key": "sk-secret"},
            "access": {
                "grants": [
                    {
                        "subject_type": "team",
                        "subject_id": "team-1",
                        "access_role": "reader",
                    },
                    {
                        "subject_type": "team",
                        "subject_id": "team-2",
                        "access_role": "editor",
                    },
                ]
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["access"]["visibility"] == "team"
    assert response.json()["access"]["subject_id"] is None
    assert response.json()["access"]["grants"] == [
        {"subject_type": "team", "subject_id": "team-1", "access_role": "reader"},
        {"subject_type": "team", "subject_id": "team-2", "access_role": "editor"},
    ]


@pytest.mark.asyncio
async def test_team_editor_can_update_but_cannot_delete_or_reshare_named_credential(
    client,
    test_app,
):
    repository = _FakeNamedCredentialRepository()
    test_app.state.named_credential_repository = repository
    identity = _IdentityService(_user_context("acct-owner", team_ids=("team-1",)))
    test_app.state.platform_identity_service = identity

    created_response = await client.post(
        "/ui/api/named-credentials",
        cookies={"deltallm_session": "asset-session"},
        json={
            "name": "Team OpenAI",
            "provider": "openai",
            "connection_config": {"api_key": "sk-secret"},
            "access": {
                "visibility": "team",
                "subject_id": "team-1",
                "access_role": "editor",
            },
        },
    )
    assert created_response.status_code == 200
    created = created_response.json()

    identity.context = _user_context("acct-editor", team_ids=("team-1",))
    update_response = await client.put(
        f"/ui/api/named-credentials/{created['credential_id']}",
        cookies={"deltallm_session": "asset-session"},
        json={
            "name": "Team OpenAI Updated",
            "provider": "openai",
            "connection_config": {"api_key": "sk-rotated"},
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["access"]["effective_role"] == "editor"

    delete_response = await client.delete(
        f"/ui/api/named-credentials/{created['credential_id']}",
        cookies={"deltallm_session": "asset-session"},
    )
    assert delete_response.status_code == 404

    access_response = await client.put(
        f"/ui/api/assets/{created['access']['managed_asset_id']}/access",
        cookies={"deltallm_session": "asset-session"},
        json={
            "visibility": "private",
            "expected_policy_version": created["access"]["policy_version"],
        },
    )
    assert access_response.status_code == 403


@pytest.mark.asyncio
async def test_creator_cannot_share_named_credential_outside_memberships_or_publicly(
    client,
    test_app,
):
    test_app.state.named_credential_repository = _FakeNamedCredentialRepository()
    test_app.state.platform_identity_service = _IdentityService(
        _user_context("acct-owner", team_ids=("team-1",))
    )
    base_payload = {
        "name": "Invalid share",
        "provider": "openai",
        "connection_config": {"api_key": "sk-secret"},
    }

    unrelated_team = await client.post(
        "/ui/api/named-credentials",
        cookies={"deltallm_session": "asset-session"},
        json={
            **base_payload,
            "access": {
                "visibility": "team",
                "subject_id": "team-other",
                "access_role": "reader",
            },
        },
    )
    assert unrelated_team.status_code == 403

    public = await client.post(
        "/ui/api/named-credentials",
        cookies={"deltallm_session": "asset-session"},
        json={**base_payload, "access": {"visibility": "public"}},
    )
    assert public.status_code == 403


class _FakeHotReloadManager:
    def __init__(self) -> None:
        self.reloads = 0

    async def reload_runtime(self) -> None:
        self.reloads += 1


class _FailingHotReloadManager(_FakeHotReloadManager):
    async def reload_runtime(self) -> None:
        await super().reload_runtime()
        raise RuntimeError("simulated routing refresh failure")


class _FakeModelDeploymentRepository:
    def __init__(self, records: list[dict[str, object]]) -> None:
        self.records = {str(record["deployment_id"]): dict(record) for record in records}

    async def list_all(self):  # noqa: ANN201
        from src.db.catalog.model_deployments import ModelDeploymentRecord

        return [
            ModelDeploymentRecord(
                deployment_id=str(record["deployment_id"]),
                model_name=str(record["model_name"]),
                named_credential_id=str(record["named_credential_id"])
                if record.get("named_credential_id") is not None
                else None,
                deltallm_params=dict(record["deltallm_params"]),
                model_info=dict(record.get("model_info") or {}),
            )
            for record in self.records.values()
        ]

    async def list_by_deployment_ids(self, deployment_ids):  # noqa: ANN201
        from src.db.catalog.model_deployments import ModelDeploymentRecord

        results = []
        for deployment_id in deployment_ids:
            record = self.records.get(str(deployment_id))
            if record is None:
                continue
            results.append(
                ModelDeploymentRecord(
                    deployment_id=str(record["deployment_id"]),
                    model_name=str(record["model_name"]),
                    named_credential_id=str(record["named_credential_id"])
                    if record.get("named_credential_id") is not None
                    else None,
                    deltallm_params=dict(record["deltallm_params"]),
                    model_info=dict(record.get("model_info") or {}),
                )
            )
        return results

    async def update(
        self,
        deployment_id: str,
        *,
        model_name: str,
        named_credential_id: str | None,
        deltallm_params: dict[str, object],
        model_info: dict[str, object] | None,
    ):  # noqa: ANN201
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

    async def update(
        self,
        deployment_id: str,
        *,
        model_name: str,
        named_credential_id: str | None,
        deltallm_params: dict[str, object],
        model_info: dict[str, object] | None,
    ):  # noqa: ANN201
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
async def test_named_credentials_create_accepts_custom_auth_header_fields(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    test_app.state.named_credential_repository = repository

    response = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "vLLM Shared",
            "provider": "vllm",
            "connection_config": {
                "api_key": "provider-key",
                "api_base": "https://vllm.example/v1",
                "auth_header_name": "X-API-Key",
                "auth_header_format": "{api_key}",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["connection_config"]["api_key"] == "***REDACTED***"
    assert payload["connection_config"]["auth_header_name"] == "X-API-Key"
    assert payload["connection_config"]["auth_header_format"] == "{api_key}"


@pytest.mark.asyncio
async def test_named_credentials_create_accepts_elevenlabs_api_key_config(client, test_app):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    test_app.state.named_credential_repository = repository

    response = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "ElevenLabs Shared",
            "provider": "elevenlabs",
            "connection_config": {
                "api_key": "elevenlabs-key",
                "api_base": "https://api.elevenlabs.io/v1",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "elevenlabs"
    assert payload["connection_config"]["api_key"] == "***REDACTED***"
    assert payload["connection_config"]["api_base"] == "https://api.elevenlabs.io/v1"
    assert payload["credentials_present"] is True

    list_response = await client.get(
        "/ui/api/named-credentials?provider=elevenlabs",
        headers={"Authorization": "Bearer mk-test"},
    )
    assert list_response.status_code == 200
    listed = list_response.json()["data"]
    assert [item["name"] for item in listed] == ["ElevenLabs Shared"]


@pytest.mark.asyncio
async def test_named_credentials_update_reloads_runtime_when_in_use_and_delete_blocks(
    client, test_app
):
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
    assert update_response.json()["warnings"] == []

    delete_response = await client.delete(
        "/ui/api/named-credentials/cred-1",
        headers={"Authorization": "Bearer mk-test"},
    )

    assert delete_response.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("reload_failure", ["exception", "unavailable"])
async def test_named_credential_update_reports_post_commit_reload_failure_as_warning(
    client,
    test_app,
    reload_failure,
):
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _FakeNamedCredentialRepository()
    record = await repository.create(
        NamedCredentialRecord(
            credential_id="cred-1",
            name="OpenAI Prod",
            provider="openai",
            connection_config={"api_key": "sk-secret"},
        )
    )
    repository.usage_counts[record.credential_id] = 1
    hot_reload = _FailingHotReloadManager()
    test_app.state.named_credential_repository = repository
    test_app.state.model_hot_reload_manager = hot_reload if reload_failure == "exception" else None

    response = await client.put(
        "/ui/api/named-credentials/cred-1",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "OpenAI Production",
            "provider": "openai",
            "connection_config": {"api_key": "sk-rotated"},
        },
    )

    assert response.status_code == 200
    assert response.json()["name"] == "OpenAI Production"
    assert response.json()["warnings"] == [
        "The credential change was committed, but this replica could not refresh its routing "
        "runtime immediately; automatic reconciliation will retry."
    ]
    assert hot_reload.reloads == (1 if reload_failure == "exception" else 0)
    stored = await repository.get_by_id("cred-1")
    assert stored is not None
    assert stored.name == "OpenAI Production"


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

    invalid_azure_custom_auth = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "Azure Prod",
            "provider": "azure_openai",
            "connection_config": {"api_key": "provider-key", "auth_header_name": "X-API-Key"},
        },
    )
    assert invalid_azure_custom_auth.status_code == 400
    assert "unsupported fields" in invalid_azure_custom_auth.text

    invalid_elevenlabs_custom_auth = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "ElevenLabs Prod",
            "provider": "elevenlabs",
            "connection_config": {
                "api_key": "provider-key",
                "api_base": "https://api.elevenlabs.io/v1",
                "auth_header_name": "Authorization",
                "auth_header_format": "Bearer {api_key}",
            },
        },
    )
    assert invalid_elevenlabs_custom_auth.status_code == 400
    assert "unsupported fields" in invalid_elevenlabs_custom_auth.text

    invalid_auth_format = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "vLLM Prod",
            "provider": "vllm",
            "connection_config": {
                "api_key": "provider-key",
                "auth_header_format": "Bearer {token}",
            },
        },
    )
    assert invalid_auth_format.status_code == 400
    assert "only supports the {api_key} placeholder" in invalid_auth_format.text

    escaped_auth_format = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "vLLM Prod",
            "provider": "vllm",
            "connection_config": {
                "api_key": "provider-key",
                "auth_header_format": "Token {{api_key}}",
            },
        },
    )
    assert escaped_auth_format.status_code == 400
    assert "must include the {api_key} placeholder" in escaped_auth_format.text

    invalid_reserved_header_name = await client.post(
        "/ui/api/named-credentials",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "name": "vLLM Prod",
            "provider": "vllm",
            "connection_config": {"api_key": "provider-key", "auth_header_name": "Content-Type"},
        },
    )
    assert invalid_reserved_header_name.status_code == 400
    assert "reserved header name" in invalid_reserved_header_name.text

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
async def test_convert_inline_group_creates_named_credential_and_links_deployments(
    client, test_app
):
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
    assert payload["warnings"] == []
    assert hot_reload.reloads == 1
    assert model_repository.records["dep-1"]["named_credential_id"] is not None
    assert "api_key" not in model_repository.records["dep-1"]["deltallm_params"]


@pytest.mark.asyncio
async def test_convert_inline_group_rolls_back_created_credential_on_non_transaction_failure(
    client, test_app
):
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
