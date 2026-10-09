from copy import deepcopy

import pytest

from src.db.route_groups import RouteGroupRepository, RouteGroupRecord
from src.db.route_policy_lifecycle import RoutePolicyStateConflictError
from src.router.selection.policy import RouteSelectorActivationUnsupportedError
from tests.db.test_route_policy_repository import (
    _RoutePolicyDB,
    _TransactionalRoutePolicyDB,
    _selector_policy,
    _selector_member_rows,
)


def _database(base):
    return _RoutePolicyDB(
        draft_policy=_selector_policy() if base == "draft" else None,
        current_policy=_selector_policy() if base == "published" else None,
        draft_semantics_version=3,
        current_semantics_version=3,
        member_rows=_selector_member_rows(),
    )


def _assert_no_write(database, prisma):
    assert not database.executions
    assert not any("INSERT INTO" in sql or "UPDATE " in sql for sql, _ in database.calls)
    assert prisma.rolled_back == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,base",
    [
        ("save_draft_policy", "draft"),
        ("save_draft_policy", "published"),
        ("publish_policy", "published"),
    ],
)
@pytest.mark.parametrize(
    "mutation",
    [
        {"enabled": "false"},
        {"enabled": 1},
        {"weight": "2"},
        {"priority": "0"},
        {"member_typo": "private-input"},
    ],
)
async def test_inherited_selector_rejects_raw_member_values(operation, base, mutation):
    database = _database(base)
    prisma = _TransactionalRoutePolicyDB(database)
    document = {"members": [{"deployment_id": "dep-a", **mutation}, {"deployment_id": "dep-b"}]}
    original = deepcopy(document)
    with pytest.raises(ValueError) as caught:
        await getattr(RouteGroupRepository(prisma), operation)("support-route", document)
    assert not isinstance(caught.value, RoutePolicyStateConflictError)
    assert "private-input" not in str(caught.value)
    assert document == original
    _assert_no_write(database, prisma)


@pytest.mark.asyncio
@pytest.mark.parametrize("base", ["draft", "published"])
async def test_inherited_selector_rejects_unknown_authored_policy_fields(base):
    database = _database(base)
    prisma = _TransactionalRoutePolicyDB(database)
    with pytest.raises(ValueError, match="unknown fields"):
        await RouteGroupRepository(prisma).save_draft_policy("support-route", {"policy_typo": 1})
    _assert_no_write(database, prisma)


@pytest.mark.asyncio
async def test_explicit_null_lane_does_not_inherit_stored_assignment():
    database = _database("draft")
    prisma = _TransactionalRoutePolicyDB(database)
    with pytest.raises(RoutePolicyStateConflictError, match="exactly one lane"):
        await RouteGroupRepository(prisma).save_draft_policy(
            "support-route",
            {
                "members": [{"deployment_id": "dep-a", "lane": None}, {"deployment_id": "dep-b"}],
            },
        )
    _assert_no_write(database, prisma)


@pytest.mark.asyncio
async def test_new_member_can_supply_explicit_lane_while_inheriting_selector():
    database = _database("draft")
    database.member_rows.append({**database.member_rows[1], "deployment_id": "dep-new"})
    prisma = _TransactionalRoutePolicyDB(database)
    result = await RouteGroupRepository(prisma).save_draft_policy(
        "support-route",
        {
            "members": [
                {"deployment_id": "dep-a"},
                {"deployment_id": "dep-new", "lane": "quality"},
            ],
        },
    )
    assert result.policy.policy_json["members"] == [
        {"deployment_id": "dep-a", "enabled": True, "lane": "economy"},
        {"deployment_id": "dep-new", "enabled": True, "lane": "quality"},
    ]
    assert result.warnings == ()


class _HttpRepository(RouteGroupRepository):
    async def get_group(self, group_key):
        return RouteGroupRecord(route_group_id="group-1", group_key=group_key, mode="chat")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,suffix,base",
    [
        ("post", "/draft", "draft"),
        ("post", "/draft", "published"),
        ("post", "/publish", "published"),
        ("put", "", "published"),
    ],
)
@pytest.mark.parametrize(
    "document",
    [
        {"policy_typo": "private-input"},
        {"members": [{"deployment_id": "dep-a", "enabled": "false"}, {"deployment_id": "dep-b"}]},
        {"members": [{"deployment_id": "dep-a", "member_typo": 1}, {"deployment_id": "dep-b"}]},
    ],
)
async def test_http_retains_untrusted_partial_write_until_locked_validation(
    client,
    test_app,
    method,
    suffix,
    base,
    document,
):
    database = _database(base)
    prisma = _TransactionalRoutePolicyDB(database)
    test_app.state.route_group_repository = _HttpRepository(prisma)
    test_app.state.settings.master_key = "mk-test"
    response = await client.request(
        method,
        f"/ui/api/route-groups/support-route/policy{suffix}",
        headers={"Authorization": "Bearer mk-test"},
        json=document,
    )
    assert response.status_code == 400, response.text
    assert "private-input" not in response.text
    _assert_no_write(database, prisma)


@pytest.mark.asyncio
async def test_inherited_selector_publication_still_reaches_activation_gate():
    database = _database("published")
    prisma = _TransactionalRoutePolicyDB(database)
    with pytest.raises(RouteSelectorActivationUnsupportedError):
        await RouteGroupRepository(prisma).publish_policy("support-route", {"strategy": "weighted"})
    _assert_no_write(database, prisma)
