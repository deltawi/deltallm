import pytest

from src.services.access.managed_asset_access import (
    AssetCreator,
    AssetCreatorKind,
    AssetPrincipal,
    visible_creator_email,
)


@pytest.mark.parametrize(
    ("principal", "expected"),
    [
        (
            AssetPrincipal(account_id="account-1", is_platform_admin=True),
            AssetCreator(account_id="account-1", kind=AssetCreatorKind.ACCOUNT),
        ),
        (
            AssetPrincipal(account_id="account-2"),
            AssetCreator(account_id="account-2", kind=AssetCreatorKind.ACCOUNT),
        ),
        (
            AssetPrincipal(account_id=None, is_platform_admin=True),
            AssetCreator(account_id=None, kind=AssetCreatorKind.MASTER_KEY),
        ),
        (
            AssetPrincipal(account_id=None),
            AssetCreator(account_id=None, kind=AssetCreatorKind.UNKNOWN),
        ),
    ],
)
def test_principal_creator_records_how_the_actor_authenticated(
    principal: AssetPrincipal, expected: AssetCreator
) -> None:
    assert principal.creator == expected


@pytest.mark.parametrize(
    ("principal", "expected"),
    [
        (AssetPrincipal(account_id=None, is_platform_admin=True), "creator@example.invalid"),
        (AssetPrincipal(account_id="creator-id"), "creator@example.invalid"),
        (AssetPrincipal(account_id="other-id"), None),
        (AssetPrincipal(account_id=None), None),
        (None, None),
    ],
)
def test_creator_email_is_visible_only_to_platform_admins_and_the_creator(
    principal: AssetPrincipal | None, expected: str | None
) -> None:
    assert (
        visible_creator_email(
            email="creator@example.invalid",
            creator_account_id="creator-id",
            principal=principal,
        )
        == expected
    )
