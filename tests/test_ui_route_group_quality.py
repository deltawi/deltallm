from __future__ import annotations

from dataclasses import replace

import pytest

from src.router import RouterConfig, build_route_group_policies
from src.router.quality import DeploymentQualityTracker, QualityOutcome
from src.router.runtime_generation import require_routing_runtime_generation
from tests.test_ui_route_groups import _FakeHotReload, _FakeRouteGroupRepository

HEADERS = {"Authorization": "Bearer mk-test"}
GROUP_ID = "6f1c2c1e-6a0e-4b0f-9d55-1b2f3c4d5e6f"


class _ByIdRepository(_FakeRouteGroupRepository):
    async def get_group_by_id(self, route_group_id: str):  # noqa: ANN201
        return next(
            (group for group in self.groups.values() if group.route_group_id == route_group_id),
            None,
        )

    def for_identity(self, identity: object) -> _ByIdRepository:
        del identity
        return self


async def _create_group(client, test_app) -> str:  # noqa: ANN001
    setattr(test_app.state.settings, "master_key", "mk-test")
    repository = _ByIdRepository()
    test_app.state.route_group_repository = repository
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    response = await client.post(
        "/ui/api/route-groups",
        headers=HEADERS,
        json={"group_key": "support-route", "mode": "chat"},
    )
    assert response.status_code == 200
    repository.groups["support-route"] = replace(
        repository.groups["support-route"], route_group_id=GROUP_ID
    )
    return GROUP_ID


def _publish_runtime(test_app, tracker: DeploymentQualityTracker | None) -> None:  # noqa: ANN001
    group = {
        "key": "support-route",
        "mode": "chat",
        "quality": {"deprioritize": True, "sensitivity": "balanced"},
        "members": [
            {"deployment_id": "dep-bad", "enabled": True},
            {"deployment_id": "dep-good", "enabled": True},
            {"deployment_id": "dep-idle", "enabled": True},
        ],
    }
    store = test_app.state.routing_runtime_generation_store
    generation = require_routing_runtime_generation(test_app.state)
    generation.router.quality = tracker
    store.replace(
        replace(
            generation,
            route_groups=(group,),
            router_config=RouterConfig(route_group_policies=build_route_group_policies([group])),
        )
    )


@pytest.mark.asyncio
async def test_quality_endpoint_reports_members_and_sent_last(client, test_app):
    route_group_id = await _create_group(client, test_app)
    tracker = DeploymentQualityTracker()
    for _ in range(12):
        tracker.record("dep-bad", QualityOutcome.OK)
        tracker.record("dep-good", QualityOutcome.OK, first_token_ms=250)
    for _ in range(8):
        tracker.record("dep-bad", QualityOutcome.BAD_TOOL_CALL)
        tracker.record("dep-good", QualityOutcome.OK)
    tracker.publish(tracker.local_snapshot())
    _publish_runtime(test_app, tracker)

    response = await client.get(
        f"/ui/api/route-groups/by-id/{route_group_id}/quality", headers=HEADERS
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["source"], body["deprioritize"], body["sensitivity"]) == (
        "local",
        True,
        "balanced",
    )
    members = {member["deployment_id"]: member for member in body["members"]}
    assert set(members) == {"dep-bad", "dep-good"}
    assert members["dep-bad"]["failure_rate"] == pytest.approx(0.4)
    assert members["dep-bad"]["bad_tool_calls"] == 8
    assert members["dep-bad"]["sent_last_reason"] == "failed_calls"
    assert members["dep-good"]["sent_last_reason"] is None
    assert members["dep-good"]["avg_first_token_ms"] == 250


@pytest.mark.asyncio
async def test_quality_endpoint_without_tracker_is_unavailable(client, test_app):
    route_group_id = await _create_group(client, test_app)
    _publish_runtime(test_app, None)

    response = await client.get(
        f"/ui/api/route-groups/by-id/{route_group_id}/quality", headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json()["source"] == "unavailable"
    assert response.json()["members"] == []
