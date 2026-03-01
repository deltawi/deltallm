from __future__ import annotations

from src.batch.access import can_access_owned_resource
from src.models.responses import UserAPIKeyAuth


def test_access_allows_same_api_key_with_no_team() -> None:
    auth = UserAPIKeyAuth(api_key="key-a", team_id=None)
    assert can_access_owned_resource(owner_api_key="key-a", owner_team_id=None, auth=auth) is True


def test_access_denies_different_keys_when_both_team_none() -> None:
    auth = UserAPIKeyAuth(api_key="key-b", team_id=None)
    assert can_access_owned_resource(owner_api_key="key-a", owner_team_id=None, auth=auth) is False


def test_access_allows_same_non_null_team_for_different_keys() -> None:
    auth = UserAPIKeyAuth(api_key="key-b", team_id="team-1")
    assert can_access_owned_resource(owner_api_key="key-a", owner_team_id="team-1", auth=auth) is True
