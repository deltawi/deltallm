from __future__ import annotations

import pytest

from src.services.access.managed_asset_access import (
    AssetAccessPolicy,
    AssetAccessRole,
    AssetGrant,
    AssetKind,
    AssetPrincipal,
    AssetSubjectType,
    AssetVisibility,
    GovernanceSource,
    ManagedAsset,
    ModelCredentialBindingMode,
    authorize_model_credential_binding,
    binding_requires_audience_coverage,
    namespace_creator_callable_key,
    resolve_asset_capabilities,
    revise_asset_access,
    validate_model_credential_audience,
)


def _asset(*, owner_account_id: str | None = "account-owner") -> ManagedAsset:
    return ManagedAsset(
        asset_id="asset-1",
        asset_kind=AssetKind.MODEL,
        governance_source=GovernanceSource.CREATOR,
        owner_account_id=owner_account_id,
    )


def test_creator_is_implicit_owner_of_private_asset() -> None:
    policy = AssetAccessPolicy(asset=_asset())

    capabilities = resolve_asset_capabilities(
        policy,
        AssetPrincipal(account_id="account-owner"),
    )

    assert policy.visibility is AssetVisibility.PRIVATE
    assert capabilities.role is AssetAccessRole.OWNER
    assert capabilities.can_read is True
    assert capabilities.can_write is True
    assert capabilities.can_manage_access is True
    assert capabilities.can_delete is True


def test_credential_owner_delegates_opaque_model_use() -> None:
    policy = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="credential-1",
            asset_kind=AssetKind.NAMED_CREDENTIAL,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="credential-owner",
        )
    )

    mode = authorize_model_credential_binding(
        policy,
        AssetPrincipal(account_id="credential-owner"),
    )

    assert mode is ModelCredentialBindingMode.OWNER_DELEGATED
    assert binding_requires_audience_coverage(mode) is False


def test_shared_credential_binding_keeps_legacy_audience_protection() -> None:
    policy = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="credential-1",
            asset_kind=AssetKind.NAMED_CREDENTIAL,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="credential-owner",
        ),
        grant=AssetGrant(
            managed_asset_id="credential-1",
            subject_type=AssetSubjectType.TEAM,
            subject_id="team-1",
            access_role=AssetAccessRole.READER,
        ),
    )

    mode = authorize_model_credential_binding(
        policy,
        AssetPrincipal(account_id="team-member", team_ids=frozenset({"team-1"})),
    )

    assert mode is ModelCredentialBindingMode.AUDIENCE_SCOPED
    assert binding_requires_audience_coverage(mode) is True


def test_private_asset_denies_another_account() -> None:
    capabilities = resolve_asset_capabilities(
        AssetAccessPolicy(asset=_asset()),
        AssetPrincipal(account_id="account-other"),
    )

    assert capabilities.can_read is False
    assert capabilities.role is None


def test_team_reader_can_read_but_cannot_write() -> None:
    policy = AssetAccessPolicy(
        asset=_asset(),
        grant=AssetGrant(
            managed_asset_id="asset-1",
            subject_type=AssetSubjectType.TEAM,
            subject_id="team-1",
            access_role=AssetAccessRole.READER,
        ),
    )

    capabilities = resolve_asset_capabilities(
        policy,
        AssetPrincipal(account_id="account-member", team_ids=frozenset({"team-1"})),
    )

    assert policy.visibility is AssetVisibility.TEAM
    assert capabilities.role is AssetAccessRole.READER
    assert capabilities.can_read is True
    assert capabilities.can_write is False
    assert capabilities.can_manage_access is False


def test_organization_editor_can_write_but_cannot_manage_access() -> None:
    policy = AssetAccessPolicy(
        asset=_asset(),
        grant=AssetGrant(
            managed_asset_id="asset-1",
            subject_type=AssetSubjectType.ORGANIZATION,
            subject_id="org-1",
            access_role=AssetAccessRole.EDITOR,
        ),
    )

    capabilities = resolve_asset_capabilities(
        policy,
        AssetPrincipal(
            account_id="account-member",
            organization_ids=frozenset({"org-1"}),
        ),
    )

    assert policy.visibility is AssetVisibility.ORGANIZATION
    assert capabilities.role is AssetAccessRole.EDITOR
    assert capabilities.can_read is True
    assert capabilities.can_write is True
    assert capabilities.can_manage_access is False
    assert capabilities.can_delete is False


def test_public_reader_is_available_without_membership() -> None:
    policy = AssetAccessPolicy(
        asset=_asset(),
        grant=AssetGrant(
            managed_asset_id="asset-1",
            subject_type=AssetSubjectType.PUBLIC,
            access_role=AssetAccessRole.READER,
        ),
    )

    capabilities = resolve_asset_capabilities(policy, AssetPrincipal(account_id=None))

    assert policy.visibility is AssetVisibility.PUBLIC
    assert capabilities.role is AssetAccessRole.READER
    assert capabilities.can_read is True
    assert capabilities.can_write is False


def test_multiple_matching_grants_resolve_to_the_strongest_role() -> None:
    policy = AssetAccessPolicy(
        asset=_asset(),
        grants=(
            AssetGrant(
                managed_asset_id="asset-1",
                subject_type=AssetSubjectType.ORGANIZATION,
                subject_id="org-1",
                access_role=AssetAccessRole.READER,
            ),
            AssetGrant(
                managed_asset_id="asset-1",
                subject_type=AssetSubjectType.TEAM,
                subject_id="team-1",
                access_role=AssetAccessRole.EDITOR,
            ),
        ),
    )

    capabilities = resolve_asset_capabilities(
        policy,
        AssetPrincipal(
            account_id="account-member",
            team_ids=frozenset({"team-1"}),
            organization_ids=frozenset({"org-1"}),
        ),
    )

    assert policy.visibility is AssetVisibility.SHARED
    assert capabilities.role is AssetAccessRole.EDITOR
    assert capabilities.can_write is True


def test_duplicate_audience_grants_are_rejected() -> None:
    with pytest.raises(ValueError, match="only be granted access once"):
        AssetAccessPolicy(
            asset=_asset(),
            grants=(
                AssetGrant(
                    managed_asset_id="asset-1",
                    subject_type=AssetSubjectType.TEAM,
                    subject_id="team-1",
                    access_role=AssetAccessRole.READER,
                ),
                AssetGrant(
                    managed_asset_id="asset-1",
                    subject_type=AssetSubjectType.TEAM,
                    subject_id="team-1",
                    access_role=AssetAccessRole.EDITOR,
                ),
            ),
        )


def test_platform_admin_override_does_not_claim_owner_role() -> None:
    capabilities = resolve_asset_capabilities(
        AssetAccessPolicy(asset=_asset()),
        AssetPrincipal(account_id="platform-admin", is_platform_admin=True),
    )

    assert capabilities.role is None
    assert capabilities.is_platform_admin_override is True
    assert capabilities.can_manage_access is True


@pytest.mark.parametrize(
    ("subject_type", "subject_id"),
    [
        (AssetSubjectType.TEAM, None),
        (AssetSubjectType.ORGANIZATION, None),
        (AssetSubjectType.PUBLIC, "unexpected"),
    ],
)
def test_grant_subject_shape_is_validated(
    subject_type: AssetSubjectType,
    subject_id: str | None,
) -> None:
    with pytest.raises(ValueError):
        AssetGrant(
            managed_asset_id="asset-1",
            subject_type=subject_type,
            subject_id=subject_id,
            access_role=AssetAccessRole.READER,
        )


def test_owner_role_cannot_be_assigned_as_a_grant() -> None:
    with pytest.raises(ValueError, match="implicit"):
        AssetGrant(
            managed_asset_id="asset-1",
            subject_type=AssetSubjectType.TEAM,
            subject_id="team-1",
            access_role=AssetAccessRole.OWNER,
        )


def test_public_visibility_is_reader_only() -> None:
    with pytest.raises(ValueError, match="reader-only"):
        AssetGrant(
            managed_asset_id="asset-1",
            subject_type=AssetSubjectType.PUBLIC,
            access_role=AssetAccessRole.EDITOR,
        )


def test_creator_governance_requires_an_owner() -> None:
    with pytest.raises(ValueError, match="owner account"):
        _asset(owner_account_id=None)


def test_policy_rejects_grant_for_another_asset() -> None:
    with pytest.raises(ValueError, match="belong"):
        AssetAccessPolicy(
            asset=_asset(),
            grant=AssetGrant(
                managed_asset_id="asset-2",
                subject_type=AssetSubjectType.PUBLIC,
                access_role=AssetAccessRole.READER,
            ),
        )


def test_owner_can_change_private_asset_to_team_editor() -> None:
    revised = revise_asset_access(
        AssetAccessPolicy(asset=_asset()),
        AssetPrincipal(account_id="account-owner"),
        visibility=AssetVisibility.TEAM,
        subject_id="team-1",
        access_role=AssetAccessRole.EDITOR,
    )

    assert revised.visibility is AssetVisibility.TEAM
    assert revised.grant is not None
    assert revised.grant.subject_id == "team-1"
    assert revised.grant.access_role is AssetAccessRole.EDITOR


def test_owner_cannot_set_public_visibility() -> None:
    with pytest.raises(PermissionError, match="platform administrator"):
        revise_asset_access(
            AssetAccessPolicy(asset=_asset()),
            AssetPrincipal(account_id="account-owner"),
            visibility=AssetVisibility.PUBLIC,
        )


def test_platform_admin_can_set_public_reader_visibility() -> None:
    revised = revise_asset_access(
        AssetAccessPolicy(asset=_asset()),
        AssetPrincipal(account_id="admin", is_platform_admin=True),
        visibility=AssetVisibility.PUBLIC,
    )

    assert revised.grant is not None
    assert revised.grant.access_role is AssetAccessRole.READER
    assert revised.visibility is AssetVisibility.PUBLIC


def test_editor_cannot_change_asset_visibility() -> None:
    policy = AssetAccessPolicy(
        asset=_asset(),
        grant=AssetGrant(
            managed_asset_id="asset-1",
            subject_type=AssetSubjectType.ORGANIZATION,
            subject_id="org-1",
            access_role=AssetAccessRole.EDITOR,
        ),
    )

    with pytest.raises(PermissionError, match="owner"):
        revise_asset_access(
            policy,
            AssetPrincipal(
                account_id="account-editor",
                organization_ids=frozenset({"org-1"}),
            ),
            visibility=AssetVisibility.PRIVATE,
        )


def test_private_visibility_rejects_a_stale_role_or_subject() -> None:
    with pytest.raises(ValueError, match="cannot have"):
        revise_asset_access(
            AssetAccessPolicy(asset=_asset()),
            AssetPrincipal(account_id="account-owner"),
            visibility=AssetVisibility.PRIVATE,
            subject_id="team-1",
            access_role=AssetAccessRole.READER,
        )


def test_model_audience_cannot_exceed_named_credential_audience() -> None:
    model_policy = AssetAccessPolicy(
        asset=_asset(),
        grant=AssetGrant(
            managed_asset_id="asset-1",
            subject_type=AssetSubjectType.TEAM,
            subject_id="team-model",
            access_role=AssetAccessRole.READER,
        ),
    )
    credential_policy = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="credential-1",
            asset_kind=AssetKind.NAMED_CREDENTIAL,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="credential-owner",
        ),
        grant=AssetGrant(
            managed_asset_id="credential-1",
            subject_type=AssetSubjectType.TEAM,
            subject_id="team-credential",
            access_role=AssetAccessRole.READER,
        ),
    )

    with pytest.raises(ValueError, match="does not cover the model team"):
        validate_model_credential_audience(
            model_policy,
            credential_policy,
            model_owner_principal=AssetPrincipal(
                account_id="account-owner",
                team_ids=frozenset({"team-credential"}),
            ),
        )


def test_organization_credential_can_cover_a_team_model() -> None:
    model_policy = AssetAccessPolicy(
        asset=_asset(),
        grant=AssetGrant(
            managed_asset_id="asset-1",
            subject_type=AssetSubjectType.TEAM,
            subject_id="team-1",
            access_role=AssetAccessRole.EDITOR,
        ),
    )
    credential_policy = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="credential-1",
            asset_kind=AssetKind.NAMED_CREDENTIAL,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="credential-owner",
        ),
        grant=AssetGrant(
            managed_asset_id="credential-1",
            subject_type=AssetSubjectType.ORGANIZATION,
            subject_id="org-1",
            access_role=AssetAccessRole.READER,
        ),
    )

    validate_model_credential_audience(
        model_policy,
        credential_policy,
        model_owner_principal=AssetPrincipal(
            account_id="account-owner",
            organization_ids=frozenset({"org-1"}),
        ),
        model_team_organization_id="org-1",
    )


def test_credential_must_cover_every_model_audience() -> None:
    model_policy = AssetAccessPolicy(
        asset=_asset(),
        grants=(
            AssetGrant(
                managed_asset_id="asset-1",
                subject_type=AssetSubjectType.TEAM,
                subject_id="team-1",
                access_role=AssetAccessRole.READER,
            ),
            AssetGrant(
                managed_asset_id="asset-1",
                subject_type=AssetSubjectType.TEAM,
                subject_id="team-2",
                access_role=AssetAccessRole.EDITOR,
            ),
        ),
    )
    credential_policy = AssetAccessPolicy(
        asset=ManagedAsset(
            asset_id="credential-1",
            asset_kind=AssetKind.NAMED_CREDENTIAL,
            governance_source=GovernanceSource.CREATOR,
            owner_account_id="account-owner",
        ),
        grants=(
            AssetGrant(
                managed_asset_id="credential-1",
                subject_type=AssetSubjectType.TEAM,
                subject_id="team-1",
                access_role=AssetAccessRole.READER,
            ),
        ),
    )

    with pytest.raises(ValueError, match="does not cover the model team"):
        validate_model_credential_audience(
            model_policy,
            credential_policy,
            model_owner_principal=AssetPrincipal(account_id="account-owner"),
        )


def test_creator_callable_keys_have_distinct_account_namespaces() -> None:
    first = namespace_creator_callable_key("shared-name", AssetPrincipal(account_id="one"))
    second = namespace_creator_callable_key("shared-name", AssetPrincipal(account_id="two"))

    assert first != second
    assert namespace_creator_callable_key(first, AssetPrincipal(account_id="one")) == first
    with pytest.raises(PermissionError, match="different account"):
        namespace_creator_callable_key(first, AssetPrincipal(account_id="two"))
    with pytest.raises(PermissionError, match="reserved"):
        namespace_creator_callable_key(
            first,
            AssetPrincipal(account_id="admin", is_platform_admin=True),
        )
