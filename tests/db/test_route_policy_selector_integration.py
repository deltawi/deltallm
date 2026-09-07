import asyncio
from copy import deepcopy
import json
from uuid import uuid4

import pytest
import pytest_asyncio

from src.db.route_groups import RouteGroupRepository
from src.db.route_policy_lifecycle import RoutePolicyStateConflictError, parse_policy_json
from src.router.selection.policy import RouteSelectorActivationUnsupportedError
from tests.db.tier_migration_helpers import connect_prisma
from tests.db.test_route_policy_publication_invariants import (
    _require_route_policy_schema,
    _seed_group,
    _cleanup_group,
)


@pytest_asyncio.fixture
async def selector_database():
    db = await connect_prisma()
    group_id = f"selector-{uuid4().hex}"
    quality_id = f"{group_id}-quality"
    try:
        await _require_route_policy_schema(db)
        await _seed_group(db, group_id=group_id, group_key=group_id)
        await db.execute_raw(
            """
            INSERT INTO deltallm_modeldeployment
                (deployment_id, model_name, deltallm_params, model_info, created_at, updated_at)
            VALUES ($1, $1, '{}'::jsonb, '{"mode":"chat"}'::jsonb, NOW(), NOW())
            """,
            quality_id,
        )
        await db.execute_raw(
            """
            INSERT INTO deltallm_routegroupmember
                (membership_id, route_group_id, deployment_id, enabled, created_at, updated_at)
            VALUES ($1, $2, $1, TRUE, NOW(), NOW())
            """,
            quality_id,
            group_id,
        )
        policy = {
            "selector": {
                "kind": "llm-tier",
                "classifier_deployment_id": f"{group_id}-deployment",
                "lanes": [
                    {"id": "economy", "rank": 0, "description": "Routine work"},
                    {"id": "quality", "rank": 1, "description": "Complex work"},
                ],
            },
            "members": [
                {"deployment_id": f"{group_id}-deployment", "lane": "economy"},
                {"deployment_id": quality_id, "lane": "quality"},
            ],
        }
        yield db, group_id, policy
    finally:
        await _cleanup_group(db, group_id)
        await db.execute_raw(
            "DELETE FROM deltallm_modeldeployment WHERE deployment_id = $1", quality_id
        )
        await db.disconnect()


async def _rows(db, group_id):
    return await db.query_raw(
        """
        SELECT version, status, semantics_version, policy_json FROM deltallm_routepolicy
        WHERE route_group_id = $1 ORDER BY version
        """,
        group_id,
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_selector_lifecycle_uses_durable_truth_without_activation(selector_database):
    db, group_id, policy = selector_database
    repository = RouteGroupRepository(db)
    revision = await repository.get_runtime_revision()
    saved = await repository.save_draft_policy(group_id, policy)
    assert saved.policy.semantics_version == 3
    assert saved.policy.policy_json["selector"]["default_lane"] == "quality"
    saved_rows = await _rows(db, group_id)
    for payload in [
        {"policy_typo": 1},
        {
            "members": [
                {"deployment_id": policy["members"][0]["deployment_id"], "enabled": "false"},
                {"deployment_id": policy["members"][1]["deployment_id"]},
            ]
        },
        {"members": None},
    ]:
        with pytest.raises(ValueError):
            await repository.save_draft_policy(group_id, payload)
        assert await _rows(db, group_id) == saved_rows
        assert await repository.get_runtime_revision() == revision

    updated = await repository.save_draft_policy(group_id, {"strategy": "weighted"})
    assert updated.policy.policy_json["members"] == saved.policy.policy_json["members"]
    assert updated.policy.policy_json["selector"] == saved.policy.policy_json["selector"]
    for publish in [
        repository.publish_latest_draft(group_id),
        repository.publish_policy(group_id, policy),
    ]:
        with pytest.raises(RouteSelectorActivationUnsupportedError):
            await publish
    assert await repository.get_runtime_revision() == revision
    assert [row["status"] for row in await _rows(db, group_id)] == ["draft"]

    removed = await repository.save_draft_policy(group_id, {"selector": None})
    assert "selector" not in removed.policy.policy_json
    assert all("lane" not in member for member in removed.policy.policy_json["members"])
    published = await repository.publish_latest_draft(group_id)
    assert published.policy_json == removed.policy.policy_json
    assert await repository.get_runtime_revision() == revision + 1


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("semantics", [1, 2, 3])
async def test_selector_shaped_history_and_rollback_retain_original_semantics(
    selector_database, semantics
):
    db, group_id, policy = selector_database
    repository = RouteGroupRepository(db)
    await repository.publish_policy(group_id, {"strategy": "weighted"})
    historical = deepcopy(policy)
    historical["server_extension"] = {"revision": 7}
    await db.execute_raw(
        """
        INSERT INTO deltallm_routepolicy
            (route_policy_id, route_group_id, version, status, policy_json, semantics_version, created_at, updated_at)
        VALUES ($1, $2, 2, 'archived', $3::jsonb, $4, NOW(), NOW())
        """,
        f"{group_id}-history",
        group_id,
        json.dumps(historical),
        semantics,
    )
    before = await _rows(db, group_id)
    revision = await repository.get_runtime_revision()
    if semantics == 3:
        with pytest.raises(RouteSelectorActivationUnsupportedError):
            await repository.rollback_policy(group_id, target_version=2)
        assert await _rows(db, group_id) == before
        assert await repository.get_runtime_revision() == revision
        # Simulate a durable selector written by a future binary or an operator.
        # PR 1 must reject this source even though corrupt Redis entries are misses.
        async with db.tx() as tx:
            await tx.execute_raw(
                "UPDATE deltallm_routepolicy SET status = 'archived' WHERE route_group_id = $1",
                group_id,
            )
            await tx.execute_raw(
                "UPDATE deltallm_routepolicy SET status = 'published' WHERE route_group_id = $1 AND version = 2",
                group_id,
            )
            with pytest.raises(RouteSelectorActivationUnsupportedError):
                await RouteGroupRepository(tx).load_runtime_snapshot()
    else:
        rolled = await repository.rollback_policy(group_id, target_version=2)
        assert rolled.semantics_version == semantics
        assert rolled.policy_json == historical
        snapshot = await repository.load_runtime_snapshot()
        group = next(group for group in snapshot.groups if group["key"] == group_id)
        assert "selector" not in group
        assert all("lane" not in member for member in group["members"])


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("change", ["selector", "membership"])
async def test_waiting_draft_write_validates_after_concurrent_group_locked_change(
    selector_database, change
):
    db, group_id, policy = selector_database
    second_db = await connect_prisma()
    entered = asyncio.Event()

    class ObservedRepository(RouteGroupRepository):
        def with_db(self, database):
            return ObservedRepository(database)

        async def _lock_group_id(self, key):
            entered.set()
            return await super()._lock_group_id(key)

    task = None
    try:
        repository = RouteGroupRepository(db)
        await repository.save_draft_policy(
            group_id, {"strategy": "weighted"} if change == "selector" else policy
        )
        async with db.tx() as tx:
            locked = RouteGroupRepository(tx)
            await locked._lock_group_id(group_id)
            if change == "selector":
                await locked._save_draft_policy_in_tx(group_id, policy)
                payload = {
                    "members": [
                        {"deployment_id": member["deployment_id"], "enabled": "false"}
                        for member in policy["members"]
                    ]
                }
            else:
                await tx.execute_raw(
                    "DELETE FROM deltallm_routegroupmember WHERE route_group_id = $1 AND deployment_id = $2",
                    group_id,
                    policy["members"][1]["deployment_id"],
                )
                payload = {"strategy": "least-busy"}
            task = asyncio.create_task(
                ObservedRepository(second_db).save_draft_policy(group_id, payload)
            )
            await asyncio.wait_for(entered.wait(), timeout=2)
        before = await _rows(db, group_id)
        error = ValueError if change == "selector" else RoutePolicyStateConflictError
        with pytest.raises(error):
            await asyncio.wait_for(task, timeout=5)
        assert await _rows(db, group_id) == before
        assert parse_policy_json(before[0]["policy_json"])["selector"]["kind"] == "llm-tier"
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second_db.disconnect()
