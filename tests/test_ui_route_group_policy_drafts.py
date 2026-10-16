from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI
import httpx
import pytest

from src.db.routing.route_groups import RoutePolicyRecord
from src.db.routing.route_policy_lifecycle import RoutePolicyWriteResult
from src.services.access.creator_route_group_access import CreatorRouteGroupAccessService
from tests.test_ui_route_group_addresses import BASE, HEADERS, AddressableGroups
from tests.test_ui_route_groups import (
    _FakeHotReload,
    _FakeLogicalModelRepository,
    _FakeManagedAssetAccessRepository,
    _FakeRouteGroupRuntimeCache,
    _IdentityService,
    _user_context,
)


class DraftAwareGroups(AddressableGroups):
    """Keep one draft beside the published policy, as the database does."""

    def __init__(self) -> None:
        super().__init__()
        self.drafts: dict[str, RoutePolicyRecord] = {}

    async def save_draft_policy(self, group_key: str, policy_json: dict):  # noqa: ANN201
        published = self.policies.get(group_key)
        result = await super().save_draft_policy(group_key, policy_json)
        if result is not None:
            self.drafts[group_key] = result.policy
            if published is not None:
                self.policies[group_key] = published
            else:
                self.policies.pop(group_key, None)
        return result

    async def publish_policy(self, group_key: str, policy_json: dict, *, published_by=None):  # noqa: ANN001, ANN201
        result = await super().publish_policy(group_key, policy_json, published_by=published_by)
        if result is None:
            return None
        # The repository joins the publisher account; the fake gives each account an email.
        email = None if published_by == "admin_api" else f"{published_by}@example.com"
        policy = replace(result.policy, published_by_email=email)
        self.policies[group_key] = policy
        return RoutePolicyWriteResult(policy, result.warnings)

    async def list_policies(self, group_key: str):  # noqa: ANN201
        records = [self.drafts.get(group_key), self.policies.get(group_key)]
        return [record for record in records if record is not None]

    async def discard_draft_policy(self, group_key: str):  # noqa: ANN201
        return self.drafts.pop(group_key, None)


@pytest.fixture
def draft_groups(test_app: FastAPI) -> DraftAwareGroups:
    test_app.state.settings.master_key = "mk-test"
    repository = DraftAwareGroups()
    test_app.state.route_group_repository = repository
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    return repository


@pytest.mark.asyncio
async def test_discard_draft_by_id_keeps_the_published_policy(
    client: httpx.AsyncClient, draft_groups: DraftAwareGroups
) -> None:
    created = await client.post(BASE, headers=HEADERS, json={"group_key": "support"})
    path = f"{BASE}/by-id/{created.json()['route_group_id']}"
    await client.post(path + "/members", headers=HEADERS, json={"deployment_id": "dep-a"})
    published = await client.post(
        path + "/policy/publish", headers=HEADERS, json={"strategy": "weighted"}
    )
    assert published.status_code == 200, published.text
    assert published.json()["policy"]["published_by"] == "admin_api"
    draft = await client.post(
        path + "/policy/draft", headers=HEADERS, json={"strategy": "least-busy"}
    )
    draft_version = draft.json()["policy"]["version"]

    discarded = await client.delete(path + "/policy/draft", headers=HEADERS)

    assert discarded.status_code == 200, discarded.text
    assert discarded.json() == {
        "group_key": "support",
        "discarded": True,
        "discarded_version": draft_version,
    }
    history = await client.get(path + "/policies", headers=HEADERS)
    assert [policy["status"] for policy in history.json()["policies"]] == ["published"]
    current = await client.get(path + "/policy", headers=HEADERS)
    assert current.json()["policy"]["policy_json"]["strategy"] == "weighted"
    again = await client.delete(path + "/policy/draft", headers=HEADERS)
    assert again.status_code == 404
    assert again.json()["detail"] == "No draft policy to discard"


@pytest.mark.asyncio
async def test_policy_records_the_publishing_account_and_hides_its_email(
    client: httpx.AsyncClient, test_app: FastAPI, draft_groups: DraftAwareGroups
) -> None:
    access_repository = _FakeManagedAssetAccessRepository(test_app)
    test_app.state.managed_asset_access_repository = access_repository
    test_app.state.creator_route_group_access_service = CreatorRouteGroupAccessService(
        access_repository, draft_groups
    )
    test_app.state.logical_model_repository = _FakeLogicalModelRepository()
    test_app.state.route_group_mutation_service = None
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    identity = _IdentityService(_user_context("group-owner", team_ids=("team-1",)))
    test_app.state.platform_identity_service = identity
    cookies = {"deltallm_session": "route-group-session"}
    created = await client.post(
        BASE,
        cookies=cookies,
        json={
            "group_key": "publisher-route",
            "name": "Publisher Route",
            "mode": "chat",
            "access": {"visibility": "team", "subject_id": "team-1", "access_role": "reader"},
        },
    )
    path = f"{BASE}/by-id/{created.json()['route_group_id']}"
    await client.post(path + "/members", cookies=cookies, json={"deployment_id": "gpt-4o-mini-0"})

    published = await client.post(
        path + "/policy/publish", cookies=cookies, json={"strategy": "weighted"}
    )

    assert published.status_code == 200, published.text
    assert published.json()["policy"]["published_by"] == "group-owner"
    assert published.json()["policy"]["published_by_email"] == "group-owner@example.com"
    identity.context = _user_context("group-reader", team_ids=("team-1",))
    history = await client.get(path + "/policies", cookies=cookies)
    assert history.status_code == 200, history.text
    [policy] = history.json()["policies"]
    assert policy["published_by"] == "group-owner"
    assert policy["published_by_email"] is None
    reader_discard = await client.delete(path + "/policy/draft", cookies=cookies)
    assert reader_discard.status_code == 404
