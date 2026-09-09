import pytest

from tests.test_ui_route_groups import (
    _FakeHotReload,
    _FakeRouteGroupRepository,
    _FakeRouteGroupRuntimeCache,
)

pytestmark = pytest.mark.app


async def test_selector_free_policy_validation_keeps_legacy_long_identifiers(client, test_app):
    test_app.state.settings.master_key = "mk-test"
    test_app.state.route_group_repository = _FakeRouteGroupRepository()
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    test_app.state.route_group_runtime_cache = _FakeRouteGroupRuntimeCache()
    headers = {"Authorization": "Bearer mk-test"}
    base = "/ui/api/route-groups"
    created = await client.post(
        base, headers=headers, json={"group_key": "long-id", "mode": "chat"}
    )
    assert created.status_code == 200, created.text
    deployment_id = "d" * 300
    member = await client.post(
        base + "/long-id/members", headers=headers, json={"deployment_id": deployment_id}
    )
    assert member.status_code == 200, member.text
    policy = {"strategy": "weighted", "members": [{"deployment_id": deployment_id}]}
    for operation in ("validate", "draft"):
        response = await client.post(
            base + "/long-id/policy/" + operation, headers=headers, json=policy
        )
        assert response.status_code == 200, response.text
        document = response.json()["policy"]
        assert document.get("policy_json", document)["members"][0]["deployment_id"] == deployment_id
    selector = {
        "kind": "llm-tier",
        "classifier_deployment_id": deployment_id,
        "lanes": [
            {"id": "economy", "rank": 0, "description": "Easy"},
            {"id": "quality", "rank": 1, "description": "Hard"},
        ],
    }
    rejected = await client.post(
        base + "/long-id/policy/validate", headers=headers, json={**policy, "selector": selector}
    )
    assert rejected.status_code == 400, rejected.text
