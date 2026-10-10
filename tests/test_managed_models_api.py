from __future__ import annotations

import pytest

from src.api.admin.endpoints.models import (
    _apply_principal_credential_view,
    _tier_visible_platform_model_names,
)
from src.config_runtime.models import ModelMutationResult
from src.db.catalog.logical_models import LogicalModelRecord
from src.db.catalog.named_credentials import NamedCredentialRecord
from src.db.catalog.model_deployments import ModelDeploymentRecord
from src.models.platform_auth import PlatformAuthContext
from src.services.access.managed_asset_access import (
    AssetAccessPolicy,
    AssetAccessRole,
    AssetGrant,
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    GovernanceSource,
    ManagedAsset,
    resolve_asset_capabilities,
)


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
        team_memberships=[{"team_id": team_id, "role": "team_developer"} for team_id in team_ids],
        organization_memberships=[
            {"organization_id": organization_id, "role": "org_member"}
            for organization_id in organization_ids
        ],
    )


def test_control_plane_platform_catalog_unions_effective_organization_tiers(test_app) -> None:  # noqa: ANN001
    class _TierPolicyService:
        mode = "enforce"
        snapshot_stale = False

        @staticmethod
        def resolve_org_allowed_callable_keys(organization_id: str):  # noqa: ANN205
            return {
                "org-a": frozenset({"platform-a", "shared"}),
                "org-b": frozenset({"platform-b", "shared"}),
            }.get(organization_id)

    test_app.state.tier_policy_service = _TierPolicyService()
    visible = _tier_visible_platform_model_names(
        test_app,
        AssetPrincipal(
            account_id="account-1",
            organization_ids=frozenset({"org-a", "org-b"}),
        ),
        {"platform-a", "platform-b", "shared", "not-in-tier"},
    )

    assert visible == {"platform-a", "platform-b", "shared"}
    assert (
        _tier_visible_platform_model_names(
            test_app,
            AssetPrincipal(account_id="account-2"),
            {"platform-a"},
        )
        == set()
    )


@pytest.mark.asyncio
async def test_model_list_hides_platform_models_outside_account_tiers(client, test_app):  # noqa: ANN001
    class _TierPolicyService:
        mode = "enforce"
        snapshot_stale = False

        @staticmethod
        def resolve_org_allowed_callable_keys(organization_id: str):  # noqa: ANN205
            if organization_id == "org-a":
                return frozenset({"gpt-4o-mini"})
            return None

    test_app.state.tier_policy_service = _TierPolicyService()
    test_app.state.platform_identity_service = _IdentityService(
        _user_context("tier-user", organization_ids=("org-a",))
    )

    response = await client.get(
        "/ui/api/models",
        cookies={"deltallm_session": "asset-session"},
    )

    assert response.status_code == 200
    assert {item["model_name"] for item in response.json()["data"]} == {"gpt-4o-mini"}

    test_app.state.platform_identity_service = _IdentityService(_user_context("no-tier-user"))
    no_tier_response = await client.get(
        "/ui/api/models",
        cookies={"deltallm_session": "asset-session"},
    )
    assert no_tier_response.status_code == 200
    assert no_tier_response.json()["data"] == []

    test_app.state.tier_policy_service.snapshot_stale = True
    assert (
        _tier_visible_platform_model_names(
            test_app,
            AssetPrincipal(
                account_id="account-1",
                organization_ids=frozenset({"org-a"}),
            ),
            {"platform-a"},
        )
        == set()
    )


def test_credential_owner_can_revoke_audience_scoped_binding() -> None:
    entry: dict[str, object] = {
        "named_credential_id": "credential-1",
        "named_credential_name": "Private provider",
        "credential_binding_mode": "audience_scoped",
        "credential_binding_state": "active",
        "credential_bound_by_account_id": "different-account",
    }

    _apply_principal_credential_view(
        entry,
        credential_accessible=True,
        credential_owned=True,
        principal=AssetPrincipal(account_id="credential-owner"),
    )

    assert entry["credential_binding"]["can_revoke"] is True


class _NamedCredentialRepository:
    def __init__(self, record: NamedCredentialRecord) -> None:
        self.records = {record.credential_id: record}

    async def get_by_id(self, credential_id: str) -> NamedCredentialRecord | None:
        return self.records.get(credential_id)


class _ManagedAssetRepository:
    prisma = None

    def __init__(self, named_credential: NamedCredentialRecord) -> None:
        self.named_credentials = {named_credential.credential_id: named_credential}
        self.policies: dict[str, AssetAccessPolicy] = {
            str(named_credential.managed_asset_id): AssetAccessPolicy(
                asset=ManagedAsset(
                    asset_id=str(named_credential.managed_asset_id),
                    asset_kind=AssetKind.NAMED_CREDENTIAL,
                    governance_source=GovernanceSource.CREATOR,
                    owner_account_id="model-owner",
                ),
            )
        }

    async def create_policy(
        self,
        policy: AssetAccessPolicy,
        *,
        created_by_account_id: str | None,
    ) -> AssetAccessPolicy:
        del created_by_account_id
        self.policies[policy.asset.asset_id] = policy
        return policy

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

    async def get_policy_for_resource(
        self,
        asset_kind: AssetKind,
        resource_id: str,
        *,
        principal: AssetPrincipal | None = None,
    ) -> AssetAccessPolicy | None:
        if asset_kind is AssetKind.NAMED_CREDENTIAL and resource_id in self.named_credentials:
            credential = self.named_credentials[resource_id]
            policy = self.policies.get(str(credential.managed_asset_id))
            if policy is None or principal is None:
                return policy
            return policy if resolve_asset_capabilities(policy, principal).can_read else None
        return None

    def add_named_credential(
        self,
        credential: NamedCredentialRecord,
        *,
        owner_account_id: str,
    ) -> None:
        self.named_credentials[credential.credential_id] = credential
        self.policies[str(credential.managed_asset_id)] = AssetAccessPolicy(
            asset=ManagedAsset(
                asset_id=str(credential.managed_asset_id),
                asset_kind=AssetKind.NAMED_CREDENTIAL,
                governance_source=GovernanceSource.CREATOR,
                owner_account_id=owner_account_id,
            )
        )

    async def list_accessible_policies(
        self,
        asset_kind: AssetKind,
        principal: AssetPrincipal,
    ) -> list[AssetAccessPolicy]:
        return [
            policy
            for policy in self.policies.values()
            if policy.asset.asset_kind is asset_kind
            and resolve_asset_capabilities(policy, principal).can_read
        ]

    async def model_access_index(
        self,
        model_names: set[str],
        principal: AssetPrincipal,
    ) -> tuple[dict[str, AssetAccessPolicy], set[str]]:
        matching = [
            policy
            for policy in self.policies.values()
            if policy.asset.asset_kind is AssetKind.MODEL
        ]
        names_by_asset_id = {
            record.managed_asset_id: record.model_name
            for record in self.logical_repository.records.values()
            if record.model_name in model_names
        }
        creator_names = {
            names_by_asset_id[policy.asset.asset_id]
            for policy in matching
            if policy.asset.asset_id in names_by_asset_id
            and policy.asset.governance_source is GovernanceSource.CREATOR
        }
        visible = {
            names_by_asset_id[policy.asset.asset_id]: policy
            for policy in matching
            if policy.asset.asset_id in names_by_asset_id
            and (
                policy.asset.governance_source is GovernanceSource.PLATFORM
                or resolve_asset_capabilities(policy, principal).can_read
            )
        }
        return visible, creator_names

    async def principal_for_account(self, account_id: str) -> AssetPrincipal:
        return AssetPrincipal(account_id=account_id, team_ids=frozenset({"team-1"}))

    async def organization_id_for_team(self, team_id: str) -> str | None:
        return "org-1" if team_id == "team-1" else None

    async def delete_policy(self, asset_id: str) -> bool:
        return self.policies.pop(asset_id, None) is not None


class _LogicalModelRepository:
    prisma = None

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app
        self.records: dict[str, LogicalModelRecord] = {}
        self.namespaces: dict[str, str] = {}

    async def get_by_name(self, model_name: str) -> LogicalModelRecord | None:
        return self.records.get(model_name)

    async def get_by_deployment_id(self, deployment_id: str) -> LogicalModelRecord | None:
        for model_name, deployments in self.app.state.model_registry.items():
            if any(item.get("deployment_id") == deployment_id for item in deployments):
                return self.records.get(model_name)
        return None

    async def list_by_managed_asset_ids(
        self,
        managed_asset_ids: list[str],
    ) -> list[LogicalModelRecord]:
        return [
            record
            for record in self.records.values()
            if record.managed_asset_id in managed_asset_ids
        ]

    async def list_by_names(self, model_names: list[str]) -> list[LogicalModelRecord]:
        return [record for name, record in self.records.items() if name in model_names]

    async def get_creator_namespace(self, account_id: str) -> tuple[str, str | None]:
        return f"{account_id}@example.com", self.namespaces.get(account_id)

    async def claim_creator_namespace(self, account_id: str, namespace: str) -> bool:
        if any(
            owner != account_id and existing.lower() == namespace.lower()
            for owner, existing in self.namespaces.items()
        ):
            return False
        existing = self.namespaces.get(account_id)
        if existing is not None and existing.lower() != namespace.lower():
            return False
        self.namespaces[account_id] = namespace
        return True

    async def create(self, record: LogicalModelRecord) -> LogicalModelRecord:
        self.records[record.model_name] = record
        return record

    async def update_display_name(
        self, model_id: str, display_name: str
    ) -> LogicalModelRecord | None:
        for model_name, record in self.records.items():
            if record.model_id == model_id:
                updated = LogicalModelRecord(
                    model_id=record.model_id,
                    model_name=record.model_name,
                    managed_asset_id=record.managed_asset_id,
                    display_name=display_name,
                )
                self.records[model_name] = updated
                return updated
        return None

    async def count_deployments(self, model_id: str) -> int:
        model = next(
            (record for record in self.records.values() if record.model_id == model_id),
            None,
        )
        return len(self.app.state.model_registry.get(model.model_name, [])) if model else 0

    async def delete(self, model_id: str) -> bool:
        for model_name, record in list(self.records.items()):
            if record.model_id == model_id:
                self.records.pop(model_name)
                return True
        return False


class _ModelDeploymentRepository:
    prisma = None

    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def get_by_deployment_id(
        self,
        deployment_id: str,
    ) -> ModelDeploymentRecord | None:
        for model_name, deployments in self.app.state.model_registry.items():
            for deployment in deployments:
                if deployment.get("deployment_id") != deployment_id:
                    continue
                return ModelDeploymentRecord(
                    deployment_id=deployment_id,
                    model_name=model_name,
                    model_id=str(deployment.get("model_id") or "") or None,
                    named_credential_id=(str(deployment.get("named_credential_id") or "") or None),
                    credential_binding_mode=(
                        str(deployment.get("credential_binding_mode") or "") or None
                    ),
                    credential_binding_state=(
                        str(deployment.get("credential_binding_state") or "") or None
                    ),
                    credential_bound_by_account_id=(
                        str(deployment.get("credential_bound_by_account_id") or "") or None
                    ),
                    deltallm_params=dict(deployment.get("deltallm_params") or {}),
                    model_info=dict(deployment.get("model_info") or {}),
                )
        return None

    async def revoke_credential_binding(
        self,
        deployment_id: str,
        *,
        expected_credential_id: str,
    ) -> ModelDeploymentRecord | None:
        for deployments in self.app.state.model_registry.values():
            for deployment in deployments:
                if (
                    deployment.get("deployment_id") != deployment_id
                    or deployment.get("named_credential_id") != expected_credential_id
                    or deployment.get("credential_binding_state") != "active"
                ):
                    continue
                deployment["named_credential_id"] = None
                deployment["named_credential_name"] = None
                deployment["credential_binding_state"] = "revoked"
                return await self.get_by_deployment_id(deployment_id)
        return None


class _HotReload:
    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def add_model(
        self,
        model_config: dict[str, object],
        *,
        updated_by: str,
    ) -> ModelMutationResult[str]:
        del updated_by
        deployment_id = str(model_config["deployment_id"])
        model_name = str(model_config["model_name"])
        self.app.state.model_registry.setdefault(model_name, []).append(dict(model_config))
        return ModelMutationResult(value=deployment_id)

    async def update_model(
        self,
        deployment_id: str,
        model_config: dict[str, object],
        *,
        updated_by: str,
    ) -> ModelMutationResult[bool]:
        del updated_by
        for deployments in self.app.state.model_registry.values():
            for index, deployment in enumerate(deployments):
                if deployment.get("deployment_id") == deployment_id:
                    deployments[index] = dict(model_config)
                    return ModelMutationResult(value=True)
        return ModelMutationResult(value=False)

    async def remove_model(
        self,
        deployment_id: str,
        *,
        updated_by: str,
    ) -> ModelMutationResult[bool]:
        del updated_by
        for model_name, deployments in list(self.app.state.model_registry.items()):
            remaining = [
                deployment
                for deployment in deployments
                if deployment.get("deployment_id") != deployment_id
            ]
            if len(remaining) == len(deployments):
                continue
            if remaining:
                self.app.state.model_registry[model_name] = remaining
            else:
                self.app.state.model_registry.pop(model_name)
            return ModelMutationResult(value=True)
        return ModelMutationResult(value=False)


@pytest.mark.asyncio
async def test_creator_model_team_editor_and_owner_capabilities(client, test_app):  # noqa: ANN001
    credential = NamedCredentialRecord(
        credential_id="cred-team",
        name="Team provider",
        provider="openai",
        connection_config={
            "api_key": "provider-key",
            "api_base": "https://api.openai.com/v1",
        },
        managed_asset_id="asset-credential",
    )
    access_repository = _ManagedAssetRepository(credential)
    logical_repository = _LogicalModelRepository(test_app)
    access_repository.logical_repository = logical_repository
    test_app.state.named_credential_repository = _NamedCredentialRepository(credential)
    test_app.state.managed_asset_access_repository = access_repository
    test_app.state.logical_model_repository = logical_repository
    test_app.state.model_deployment_repository = _ModelDeploymentRepository(test_app)
    test_app.state.model_hot_reload_manager = _HotReload(test_app)
    identity = _IdentityService(_user_context("model-owner", team_ids=("team-1",)))
    test_app.state.platform_identity_service = identity

    response = await client.post(
        "/ui/api/models",
        cookies={"deltallm_session": "asset-session"},
        json={
            "deployment_id": "creator-dep",
            "model_name": "mod-own-k7m4q/support-model",
            "api_model_id": "mod-own-k7m4q/support-model",
            "api_namespace": "mod-own-k7m4q",
            "api_model_slug": "support-model",
            "display_name": "Customer Support Model",
            "named_credential_id": "cred-team",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
            },
            "model_info": {"mode": "chat"},
            "access": {
                "visibility": "team",
                "subject_id": "team-1",
                "access_role": "editor",
            },
        },
    )

    assert response.status_code == 200
    created = response.json()
    assert created["access"]["effective_role"] == "owner"
    assert created["access"]["visibility"] == "team"
    assert created["display_name"] == "Customer Support Model"
    assert created["api_model_id"] == "mod-own-k7m4q/support-model"
    identity_response = await client.get(
        "/ui/api/models/identity",
        cookies={"deltallm_session": "asset-session"},
    )
    assert identity_response.status_code == 200
    assert identity_response.json()["api_namespace"] == "mod-own-k7m4q"
    assert identity_response.json()["namespace_locked"] is True
    model_asset_id = created["access"]["managed_asset_id"]
    model_name = created["model_name"]

    setattr(test_app.state.settings, "master_key", "mk-test")
    admin_collision = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "deployment_id": "platform-collision",
            "model_name": model_name,
            "named_credential_id": "cred-team",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
            },
            "model_info": {"mode": "chat"},
        },
    )
    assert admin_collision.status_code == 409

    identity.context = _user_context("team-editor", team_ids=("team-1",))
    listed = await client.get(
        "/ui/api/models",
        cookies={"deltallm_session": "asset-session"},
    )
    visible = next(item for item in listed.json()["data"] if item["model_name"] == model_name)
    assert visible["access"]["effective_role"] == "editor"
    assert visible["credential_binding"]["credential_access"] == "opaque"
    assert visible["credential_binding"]["can_revoke"] is False
    assert visible["named_credential_id"] is None
    assert visible["named_credential_name"] is None
    assert visible["connection_summary"] == {}

    updated = await client.put(
        "/ui/api/models/creator-dep",
        cookies={"deltallm_session": "asset-session"},
        json={
            "model_name": model_name,
            "display_name": "Customer Support Pro",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4.1-mini",
            },
            "model_info": {"mode": "chat"},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["access"]["effective_role"] == "editor"
    assert updated.json()["display_name"] == "Customer Support Pro"
    assert updated.json()["api_model_id"] == model_name
    assert updated.json()["credential_binding"]["credential_access"] == "opaque"
    assert updated.json()["named_credential_id"] is None

    guessed_replacement = await client.put(
        "/ui/api/models/creator-dep",
        cookies={"deltallm_session": "asset-session"},
        json={
            "model_name": model_name,
            "named_credential_id": "guessed-private-credential",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4.1-mini",
            },
            "model_info": {"mode": "chat"},
        },
    )
    assert guessed_replacement.status_code == 400

    forbidden_revoke = await client.post(
        "/ui/api/models/creator-dep/credential-binding/revoke",
        cookies={"deltallm_session": "asset-session"},
    )
    assert forbidden_revoke.status_code == 404

    editor_credential = NamedCredentialRecord(
        credential_id="editor-credential",
        name="Editor provider",
        provider="openai",
        connection_config={
            "api_key": "editor-provider-key",
            "api_base": "https://api.openai.com/v1",
        },
        managed_asset_id="asset-editor-credential",
    )
    test_app.state.named_credential_repository.records[editor_credential.credential_id] = (
        editor_credential
    )
    access_repository.add_named_credential(
        editor_credential,
        owner_account_id="team-editor",
    )
    replaced = await client.put(
        "/ui/api/models/creator-dep",
        cookies={"deltallm_session": "asset-session"},
        json={
            "model_name": model_name,
            "named_credential_id": editor_credential.credential_id,
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4.1-mini",
            },
            "model_info": {"mode": "chat"},
        },
    )
    assert replaced.status_code == 200
    assert replaced.json()["named_credential_id"] == editor_credential.credential_id
    assert replaced.json()["credential_binding"]["credential_access"] == "accessible"
    assert replaced.json()["credential_binding"]["can_revoke"] is True

    revoked = await client.post(
        "/ui/api/models/creator-dep/credential-binding/revoke",
        cookies={"deltallm_session": "asset-session"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True

    revoked_detail = await client.get(
        "/ui/api/models/creator-dep",
        cookies={"deltallm_session": "asset-session"},
    )
    assert revoked_detail.status_code == 200
    assert revoked_detail.json()["credential_source"] == "named"
    assert revoked_detail.json()["credential_binding"]["state"] == "revoked"
    assert revoked_detail.json()["credential_binding"]["can_replace"] is True

    missing_replacement = await client.put(
        "/ui/api/models/creator-dep",
        cookies={"deltallm_session": "asset-session"},
        json={
            "model_name": model_name,
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4.1-mini",
            },
            "model_info": {"mode": "chat"},
        },
    )
    assert missing_replacement.status_code == 409

    repaired = await client.put(
        "/ui/api/models/creator-dep",
        cookies={"deltallm_session": "asset-session"},
        json={
            "model_name": model_name,
            "named_credential_id": editor_credential.credential_id,
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4.1-mini",
            },
            "model_info": {"mode": "chat"},
        },
    )
    assert repaired.status_code == 200
    assert repaired.json()["credential_binding"]["state"] == "active"

    forbidden_delete = await client.delete(
        "/ui/api/models/creator-dep",
        cookies={"deltallm_session": "asset-session"},
    )
    assert forbidden_delete.status_code == 404

    identity.context = _user_context("outsider")
    outsider_list = await client.get(
        "/ui/api/models",
        cookies={"deltallm_session": "asset-session"},
    )
    assert model_name not in {item["model_name"] for item in outsider_list.json()["data"]}

    identity.context = _user_context("model-owner", team_ids=("team-1",))
    deleted = await client.delete(
        "/ui/api/models/creator-dep",
        cookies={"deltallm_session": "asset-session"},
    )
    assert deleted.status_code == 200
    assert model_asset_id not in access_repository.policies
    assert await logical_repository.get_by_name(model_name) is None


@pytest.mark.asyncio
async def test_creator_model_rejects_inline_credentials(client, test_app):  # noqa: ANN001
    test_app.state.platform_identity_service = _IdentityService(_user_context("model-owner"))

    response = await client.post(
        "/ui/api/models",
        cookies={"deltallm_session": "asset-session"},
        json={
            "model_name": "unsafe-inline-model",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
                "api_key": "must-not-be-accepted",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Creator models require a named_credential_id"


@pytest.mark.asyncio
async def test_creator_model_rejects_short_namespace_at_api_boundary(client, test_app):  # noqa: ANN001
    test_app.state.platform_identity_service = _IdentityService(_user_context("model-owner"))
    test_app.state.logical_model_repository = _LogicalModelRepository(test_app)

    response = await client.post(
        "/ui/api/models",
        cookies={"deltallm_session": "asset-session"},
        json={
            "model_name": "Friendly model",
            "api_namespace": "a",
            "api_model_slug": "friendly-model",
            "deltallm_params": {
                "provider": "openai",
                "model": "openai/gpt-4o-mini",
            },
            "model_info": {"mode": "chat"},
        },
    )

    assert response.status_code == 400
    assert "3-32 characters" in response.json()["detail"]


@pytest.mark.asyncio
async def test_platform_model_access_is_tier_managed_not_acl_managed(client, test_app):  # noqa: ANN001
    placeholder_credential = NamedCredentialRecord(
        credential_id="unused-credential",
        name="Unused",
        provider="openai",
        connection_config={},
        managed_asset_id="asset-unused-credential",
    )
    access_repository = _ManagedAssetRepository(placeholder_credential)
    logical_repository = _LogicalModelRepository(test_app)
    access_repository.logical_repository = logical_repository
    test_app.state.managed_asset_access_repository = access_repository
    test_app.state.logical_model_repository = logical_repository
    test_app.state.model_deployment_repository = _ModelDeploymentRepository(test_app)
    test_app.state.model_hot_reload_manager = _HotReload(test_app)
    setattr(test_app.state.settings, "master_key", "mk-test")
    payload = {
        "deployment_id": "platform-tier-deployment",
        "model_name": "platform-tier-model",
        "deltallm_params": {
            "provider": "openai",
            "model": "openai/gpt-4o-mini",
            "api_base": "https://api.openai.com/v1",
            "api_key": "provider-key",
        },
        "model_info": {"mode": "chat"},
    }

    rejected = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            **payload,
            "access": {
                "grants": [
                    {
                        "subject_type": "team",
                        "subject_id": "team-1",
                        "access_role": "reader",
                    }
                ]
            },
        },
    )

    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "Platform model availability is controlled by tiers"

    created = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json=payload,
    )

    assert created.status_code == 200
    access = created.json()["access"]
    assert access["governance_source"] == "platform"
    assert access["visibility"] == "private"

    rejected_additional_deployment = await client.post(
        "/ui/api/models",
        headers={"Authorization": "Bearer mk-test"},
        json={
            **payload,
            "deployment_id": "platform-tier-deployment-2",
            "access": {
                "grants": [
                    {
                        "subject_type": "team",
                        "subject_id": "team-1",
                        "access_role": "reader",
                    }
                ]
            },
        },
    )
    assert rejected_additional_deployment.status_code == 400

    rejected_update = await client.put(
        f"/ui/api/assets/{access['managed_asset_id']}/access",
        headers={"Authorization": "Bearer mk-test"},
        json={
            "expected_policy_version": access["policy_version"],
            "grants": [
                {
                    "subject_type": "organization",
                    "subject_id": "org-1",
                    "access_role": "reader",
                }
            ],
        },
    )

    assert rejected_update.status_code == 400
    assert rejected_update.json()["detail"] == (
        "Platform model availability is controlled by tiers"
    )

    # Historical rows can still contain grants created before platform sharing
    # was rejected. Those rows must never authorize platform operations.
    platform_policy = access_repository.policies[access["managed_asset_id"]]
    access_repository.policies[access["managed_asset_id"]] = AssetAccessPolicy(
        asset=platform_policy.asset,
        grants=(
            AssetGrant(
                managed_asset_id=platform_policy.asset.asset_id,
                subject_type=AssetSubjectType.TEAM,
                subject_id="team-1",
                access_role=AssetAccessRole.EDITOR,
            ),
        ),
    )
    test_app.state.platform_identity_service = _IdentityService(
        _user_context("stale-editor", team_ids=("team-1",))
    )

    stale_update = await client.put(
        "/ui/api/models/platform-tier-deployment",
        cookies={"deltallm_session": "asset-session"},
        json={},
    )
    stale_health = await client.post(
        "/ui/api/models/platform-tier-deployment/health-check",
        cookies={"deltallm_session": "asset-session"},
    )
    stale_delete = await client.delete(
        "/ui/api/models/platform-tier-deployment",
        cookies={"deltallm_session": "asset-session"},
    )

    assert stale_update.status_code == 404
    assert stale_health.status_code == 404
    assert stale_delete.status_code == 404
