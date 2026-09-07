from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from fastapi import FastAPI
import httpx
import pytest

from src.db.route_group_identity import RouteGroupIdentity
from src.db.route_groups import RouteGroupRecord
from src.models.platform_auth import PlatformAuthContext
from tests.test_ui_route_groups import _FakeHotReload, _FakeRouteGroupRepository


class AddressableGroups(_FakeRouteGroupRepository):
    async def create_group(self, **payload: object) -> RouteGroupRecord:
        group = await super().create_group(**payload)
        group = replace(group, route_group_id=str(UUID(int=self._group_counter)))
        self.groups[group.group_key] = group
        return group

    async def get_group_by_id(self, route_group_id: str) -> RouteGroupRecord | None:
        return next(
            (group for group in self.groups.values() if group.route_group_id == route_group_id),
            None,
        )

    def for_identity(self, identity: RouteGroupIdentity) -> AddressableGroups:
        # These HTTP tests exercise addressing. SQL identity fencing has real-DB coverage.
        assert self.groups[identity.group_key].route_group_id == identity.route_group_id
        return self


@pytest.fixture
def addressable_groups(test_app: FastAPI) -> AddressableGroups:
    test_app.state.settings.master_key = "mk-test"
    repository = AddressableGroups()
    test_app.state.route_group_repository = repository
    test_app.state.model_hot_reload_manager = _FakeHotReload()
    return repository


HEADERS = {"Authorization": "Bearer mk-test"}
BASE = "/ui/api/route-groups"
KEYS = [
    "support",
    "vendor/model",
    "vendor/model/v2",
    "vendor%2Fmodel",
    "support / eu",
    "مجموعة/نموذج",
    "model?variant#1",
    "team/members",
    "team/policy",
    "by-id",
    "00000000-0000-0000-0000-000000000001",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("group_key", KEYS)
async def test_group_id_addresses_all_admin_operations(
    client: httpx.AsyncClient,
    addressable_groups: AddressableGroups,
    group_key: str,
) -> None:
    created = await client.post(
        BASE, headers=HEADERS, json={"group_key": group_key, "mode": "chat"}
    )
    assert created.status_code == 200, created.text
    group_id = created.json()["route_group_id"]
    path = f"{BASE}/by-id/{group_id}"
    resolved = await client.get(
        f"{BASE}/resolve/by-key", headers=HEADERS, params={"group_key": group_key}
    )
    assert resolved.json() == {"route_group_id": group_id, "group_key": group_key}

    # A group whose name looks like a subresource must not select another group.
    if group_key.startswith("team/"):
        await client.post(BASE, headers=HEADERS, json={"group_key": "team", "mode": "chat"})

    detail = await client.get(path, headers=HEADERS)
    assert detail.status_code == 200, detail.text
    assert detail.json()["group"]["group_key"] == group_key
    assert detail.json()["group"]["route_group_id"] == group_id
    assert detail.json()["members"] == []
    updated = await client.put(path, headers=HEADERS, json={"name": "Renamed display"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["group_key"] == group_key
    assert updated.json()["name"] == "Renamed display"

    member = await client.post(
        path + "/members", headers=HEADERS, json={"deployment_id": "dep-a", "weight": 5}
    )
    assert member.status_code == 200, member.text
    members = await client.get(path + "/members", headers=HEADERS)
    assert members.status_code == 200
    assert members.json()[0]["route_group_id"] == group_id
    for suffix in ["validate", "draft"]:
        response = await client.post(
            path + "/policy/" + suffix, headers=HEADERS, json={"strategy": "weighted"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["group_key"] == group_key
    published = await client.post(path + "/policy/publish", headers=HEADERS, json={})
    assert published.status_code == 200, published.text
    for suffix in ["/policy", "/policies"]:
        response = await client.get(path + suffix, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["group_key"] == group_key
    simulation = await client.post(
        path + "/policy/simulate", headers=HEADERS, json={"iterations": 1}
    )
    assert simulation.status_code == 200, simulation.text
    assert simulation.json()["group_key"] == group_key
    rollback = await client.post(path + "/policy/rollback", headers=HEADERS, json={"version": 1})
    assert rollback.status_code == 200, rollback.text
    removed = await client.delete(path + "/members/dep-a", headers=HEADERS)
    assert removed.status_code == 200, removed.text
    deleted = await client.delete(path, headers=HEADERS)
    assert deleted.status_code == 200, deleted.text
    assert group_key not in addressable_groups.groups
    assert (await client.get(path, headers=HEADERS)).status_code == 404


@pytest.mark.asyncio
async def test_legacy_keys_and_ids_do_not_alias(
    client: httpx.AsyncClient, addressable_groups: AddressableGroups
) -> None:
    first = await client.post(BASE, headers=HEADERS, json={"group_key": "vendor/model"})
    first_id = first.json()["route_group_id"]
    second = await client.post(BASE, headers=HEADERS, json={"group_key": first_id})
    third = await client.post(BASE, headers=HEADERS, json={"group_key": "vendor%2Fmodel"})
    legacy = await client.get(f"{BASE}/{first_id}", headers=HEADERS)
    assert legacy.json()["group"]["route_group_id"] == second.json()["route_group_id"]
    canonical = await client.get(f"{BASE}/by-id/{first_id}", headers=HEADERS)
    assert canonical.json()["group"]["group_key"] == "vendor/model"
    literal = await client.get(
        f"{BASE}/resolve/by-key", headers=HEADERS, params={"group_key": "vendor%2Fmodel"}
    )
    assert literal.json()["route_group_id"] == third.json()["route_group_id"]
    await client.post(BASE, headers=HEADERS, json={"group_key": "by-id"})
    legacy_members = await client.get(f"{BASE}/by-id/members", headers=HEADERS)
    assert legacy_members.status_code == 200
    assert legacy_members.json() == []
    legacy_policy = await client.get(f"{BASE}/by-id/policy", headers=HEADERS)
    assert legacy_policy.json()["group_key"] == "by-id"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix,payload",
    [
        ("GET", "", None),
        ("PUT", "", {}),
        ("DELETE", "", None),
        ("GET", "/members", None),
        ("POST", "/members", {"deployment_id": "dep-a"}),
        ("DELETE", "/members/dep-a", None),
        ("GET", "/policy", None),
        ("GET", "/policies", None),
        ("POST", "/policy/validate", {}),
        ("POST", "/policy/draft", {}),
        ("POST", "/policy/publish", {}),
        ("POST", "/policy/rollback", {"version": 1}),
        ("POST", "/policy/simulate", {}),
    ],
)
async def test_id_routes_authenticate_before_repository_lookup(
    client: httpx.AsyncClient,
    test_app: FastAPI,
    method: str,
    suffix: str,
    payload: dict[str, object] | None,
) -> None:
    test_app.state.route_group_repository = None
    response = await client.request(method, f"{BASE}/by-id/{UUID(int=1)}{suffix}", json=payload)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_scoped_user_cannot_resolve_or_mutate_groups(
    client: httpx.AsyncClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = PlatformAuthContext(
        account_id="reader",
        email="reader@example.test",
        role="org_user",
        organization_memberships=[{"organization_id": "org-a", "role": "org_owner"}],
    )
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)
    test_app.state.route_group_repository = None
    for method, path in [
        ("GET", f"{BASE}/resolve/by-key?group_key=vendor%2Fmodel"),
        ("GET", f"{BASE}/by-id/{UUID(int=1)}"),
        ("DELETE", f"{BASE}/by-id/{UUID(int=1)}"),
        ("POST", f"{BASE}/by-id/{UUID(int=1)}/policy/publish"),
    ]:
        response = await client.request(method, path)
        assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_id_errors_preserve_failure_meaning(
    client: httpx.AsyncClient, test_app: FastAPI, addressable_groups: AddressableGroups
) -> None:
    assert (await client.get(f"{BASE}/by-id/{UUID(int=99)}", headers=HEADERS)).status_code == 404
    assert (await client.get(f"{BASE}/by-id/not-a-uuid", headers=HEADERS)).status_code == 404
    created = await client.post(BASE, headers=HEADERS, json={"group_key": "test/key"})
    path = f"{BASE}/by-id/{created.json()['route_group_id']}"
    invalid = await client.post(path + "/policy/simulate", headers=HEADERS, json={"iterations": 0})
    assert invalid.status_code == 400
    invalid = await client.put(path, headers=HEADERS, json={"enabled": "false"})
    assert invalid.status_code == 400
    test_app.state.route_group_repository = None
    assert (await client.get(path, headers=HEADERS)).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["weight", "priority"])
@pytest.mark.parametrize("value", [True, False])
async def test_member_numbers_reject_booleans_without_mutating(
    client: httpx.AsyncClient, addressable_groups: AddressableGroups, field: str, value: bool
) -> None:
    created = await client.post(BASE, headers=HEADERS, json={"group_key": "numeric-validation"})
    group_id = created.json()["route_group_id"]
    payload = {"deployment_id": "dep-a", "weight": 7, "priority": 3}
    path = f"{BASE}/by-id/{group_id}/members"
    seeded = await client.post(path, headers=HEADERS, json=payload)
    assert seeded.status_code == 200, seeded.text
    for target in [f"{BASE}/numeric-validation/members", path]:
        response = await client.post(target, headers=HEADERS, json={**payload, field: value})
        assert response.status_code == 400, response.text
    members = await client.get(path, headers=HEADERS)
    assert members.json()[0]["weight"] == 7
    assert members.json()[0]["priority"] == 3


@pytest.mark.asyncio
async def test_member_numbers_keep_accepted_numeric_and_null_inputs(
    client: httpx.AsyncClient, addressable_groups: AddressableGroups
) -> None:
    created = await client.post(BASE, headers=HEADERS, json={"group_key": "numeric-compatibility"})
    path = f"{BASE}/by-id/{created.json()['route_group_id']}/members"
    for value, expected in [(0, 0), (7, 7), ("7", 7), (None, None)]:
        response = await client.post(
            path,
            headers=HEADERS,
            json={"deployment_id": "dep-a", "weight": value, "priority": value},
        )
        assert response.status_code == 200, response.text
        assert response.json()["weight"] == expected
        assert response.json()["priority"] == expected
    omitted = await client.post(path, headers=HEADERS, json={"deployment_id": "dep-a"})
    assert omitted.status_code == 200
    assert omitted.json()["weight"] is None and omitted.json()["priority"] is None


def test_id_route_openapi_contract(test_app: FastAPI) -> None:
    schema = test_app.openapi()
    operation = schema["paths"][f"{BASE}/by-id/{{route_group_id}}"]["get"]
    path_parameters = [param for param in operation["parameters"] if param["in"] == "path"]
    assert len(path_parameters) == 1
    assert path_parameters[0]["name"] == "route_group_id"
    assert path_parameters[0]["schema"]["format"] == "uuid"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/RouteGroupDetailResponse"
    )
    assert "404" in operation["responses"]
    query = schema["paths"][f"{BASE}/resolve/by-key"]["get"]
    assert any(
        param["name"] == "group_key" and param["in"] == "query" for param in query["parameters"]
    )
