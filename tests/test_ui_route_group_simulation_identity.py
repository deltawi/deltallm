from __future__ import annotations

from fastapi import FastAPI
import httpx
from pydantic import JsonValue
import pytest

from src.db.route_groups import RouteGroupRepository

GROUP_KEY = "vendor/model"
OLD_ID = "00000000-0000-0000-0000-000000000001"
NEW_ID = "00000000-0000-0000-0000-000000000002"
MEMBER = {
    "deployment_id": "gpt-4o-mini-0",
    "enabled": True,
    "weight": 1,
    "priority": None,
}


class InterleavedSnapshotDB:
    """Delete/recreate after lookup, as the simulation reads its SQL snapshot."""

    def __init__(self, *, replacement: bool, group_key: str = GROUP_KEY) -> None:
        self.replacement = replacement
        self.group_key = group_key
        self.deleted = False
        self.snapshot_reads = 0

    async def query_raw(self, query: str, *args: object) -> list[dict[str, JsonValue]]:
        if "runtime.revision AS runtime_revision" in query:
            self.deleted = True
            self.snapshot_reads += 1
            return [
                {
                    "runtime_revision": 2,
                    "route_groups_initialized": True,
                    "route_group_id": NEW_ID if self.replacement else None,
                    "group_key": self.group_key,
                    "mode": "chat",
                    "enabled": True,
                    "routing_strategy": "weighted",
                    "metadata": None,
                    "policy_version": None,
                    "policy_semantics_version": None,
                    "policy_json": None,
                    "members": [MEMBER] if self.replacement else [],
                }
            ]
        if "JOIN deltallm_routegroup g" in query:
            assert self.deleted
            if args == (OLD_ID,) or not self.replacement:
                return []
            assert args == (self.group_key,)
            return [
                {
                    **MEMBER,
                    "route_group_id": NEW_ID,
                    "membership_id": "replacement-member",
                }
            ]
        if "FROM deltallm_routegroup g" in query:
            assert args in [(OLD_ID,), (self.group_key,)]
            if self.deleted:
                return []
            return [
                {
                    "route_group_id": OLD_ID,
                    "group_key": self.group_key,
                    "name": "Original",
                    "mode": "chat",
                    "enabled": True,
                    "routing_strategy": "weighted",
                    "metadata": None,
                    "member_count": 1,
                }
            ]
        raise AssertionError(f"Unexpected database operation: {query}")


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [True, False])
@pytest.mark.parametrize("policy", [None, {"strategy": "weighted"}])
async def test_simulation_rejects_id_deleted_before_runtime_snapshot(
    client: httpx.AsyncClient,
    test_app: FastAPI,
    replacement: bool,
    policy: dict[str, str] | None,
) -> None:
    db = InterleavedSnapshotDB(replacement=replacement)
    test_app.state.settings.master_key = "mk-test"
    test_app.state.route_group_repository = RouteGroupRepository(db)
    response = await client.post(
        f"/ui/api/route-groups/by-id/{OLD_ID}/policy/simulate",
        headers={"Authorization": "Bearer mk-test"},
        json={"iterations": 1, "policy": policy},
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "Route group not found"}
    assert db.snapshot_reads == 1


@pytest.mark.asyncio
async def test_legacy_simulation_retains_key_addressing(
    client: httpx.AsyncClient, test_app: FastAPI
) -> None:
    # Use a single-segment key for the legacy route's existing URL contract.
    group_key = "legacy-group"
    test_app.state.settings.master_key = "mk-test"
    test_app.state.route_group_repository = RouteGroupRepository(
        InterleavedSnapshotDB(replacement=True, group_key=group_key)
    )
    response = await client.post(
        f"/ui/api/route-groups/{group_key}/policy/simulate",
        headers={"Authorization": "Bearer mk-test"},
        json={"iterations": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["selections"][0]["deployment_id"] == MEMBER["deployment_id"]
