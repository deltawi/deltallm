import asyncio
from copy import deepcopy
import json

import pytest
import pytest_asyncio

from src.db import route_policy_dependencies
from src.db.repositories import ModelDeploymentRepository
from src.db.route_groups import RouteGroupRepository
from src.db.route_policy_lifecycle import RoutePolicyStateConflictError
from tests.db import test_route_policy_selector_integration as fixtures
from tests.db.test_selector_activation import qualify
from tests.db.tier_migration_helpers import connect_prisma

pytestmark = pytest.mark.postgres
selector_database = fixtures.selector_database


@pytest_asyncio.fixture
async def independent_database(selector_database):
    db, group, policy = selector_database
    info = await qualify(db, policy)
    classifier = f"{group}-external"
    info["chat_capabilities"] = {}
    info["max_tokens"] = 8192
    await db.execute_raw(
        """
        INSERT INTO deltallm_modeldeployment
            (deployment_id, model_name, deltallm_params, model_info, created_at, updated_at)
        VALUES ($1, $1, '{"model":"openai/tiny"}'::jsonb, $2::jsonb, NOW(), NOW())
        """,
        classifier,
        json.dumps(info),
    )
    policy["selector"]["classifier_deployment_id"] = classifier
    try:
        yield db, group, policy
    finally:
        await db.execute_raw("DELETE FROM deltallm_routepolicy WHERE route_group_id=$1", group)
        await db.execute_raw(
            "DELETE FROM deltallm_modeldeployment WHERE deployment_id=$1", classifier
        )


async def test_external_selector_draft_publish_partial_write_and_rollback(independent_database):
    db, group, policy = independent_database
    repository = RouteGroupRepository(db, selector_activation_check=lambda: None)
    classifier = policy["selector"]["classifier_deployment_id"]
    saved = await repository.save_draft_policy(group, policy)
    assert saved.policy.policy_json["selector"]["classifier_deployment_id"] == classifier
    await repository.save_draft_policy(group, {"strategy": "weighted"})
    first = await repository.publish_latest_draft(group)
    second = await repository.publish_policy(group, {"retry": {"max_attempts": 1}})
    assert second.policy.policy_json["selector"] == first.policy_json["selector"]
    assert classifier not in {
        member.deployment_id for member in await repository.list_members(group)
    }
    await repository.publish_policy(group, {"selector": None})
    restored = await repository.rollback_policy(group, target_version=first.version)
    assert restored.policy_json == first.policy_json
    snapshot = await repository.load_runtime_snapshot()
    runtime = next(item for item in snapshot.groups if item["key"] == group)
    assert classifier not in {member["deployment_id"] for member in runtime["members"]}


@pytest.mark.parametrize("mutation", ["delete", "mode", "pricing", "context", "capacity"])
async def test_external_selector_dependencies_protect_active_policy(independent_database, mutation):
    db, group, policy = independent_database
    groups = RouteGroupRepository(db, selector_activation_check=lambda: None)
    models = ModelDeploymentRepository(db)
    await groups.publish_policy(group, policy)
    revision = await groups.get_runtime_revision()
    classifier = policy["selector"]["classifier_deployment_id"]
    record = await models.get_by_deployment_id(classifier)
    info = deepcopy(record.model_info)
    if mutation == "mode":
        info["mode"] = "embedding"
    elif mutation != "delete":
        info.pop(
            {"pricing": "input_cost_per_token", "context": "max_tokens", "capacity": "rpm_limit"}[
                mutation
            ]
        )
    with pytest.raises(RoutePolicyStateConflictError):
        if mutation == "delete":
            await models.delete(classifier)
        else:
            await models.update(
                classifier,
                model_name=record.model_name,
                named_credential_id=record.named_credential_id,
                deltallm_params=record.deltallm_params,
                model_info=info,
            )
    assert (await models.get_by_deployment_id(classifier)).model_info == record.model_info
    assert await groups.get_runtime_revision() == revision


async def test_archived_reference_does_not_block_delete_but_cannot_be_restored(
    independent_database,
):
    db, group, policy = independent_database
    groups = RouteGroupRepository(db, selector_activation_check=lambda: None)
    published = await groups.publish_policy(group, policy)
    await groups.publish_policy(group, {"selector": None})
    assert await ModelDeploymentRepository(db).delete(
        policy["selector"]["classifier_deployment_id"]
    )
    revision = await groups.get_runtime_revision()
    with pytest.raises(RoutePolicyStateConflictError, match="existing concrete deployment"):
        await groups.rollback_policy(group, target_version=published.policy.version)
    assert await groups.get_runtime_revision() == revision
    assert "selector" not in (await groups.get_published_policy(group)).policy_json


async def test_removing_answer_membership_does_not_remove_external_dependency(independent_database):
    db, group, policy = independent_database
    groups = RouteGroupRepository(db, selector_activation_check=lambda: None)
    classifier = policy["selector"]["classifier_deployment_id"]
    await groups.upsert_member(
        group, deployment_id=classifier, enabled=False, weight=None, priority=None
    )
    await groups.publish_policy(group, policy)
    assert await groups.remove_member(group, classifier)
    assert (await groups.get_published_policy(group)).policy_json["selector"][
        "classifier_deployment_id"
    ] == classifier


async def test_publish_waits_for_concurrent_external_deletion_then_revalidates(
    independent_database,
    monkeypatch,
):
    db, group, policy = independent_database
    other = await connect_prisma()
    attempting = asyncio.Event()
    lock = route_policy_dependencies.lock_deployment_dependencies

    async def signal_lock(connection, ids):
        attempting.set()
        await lock(connection, ids)

    monkeypatch.setattr(route_policy_dependencies, "lock_deployment_dependencies", signal_lock)
    publisher = None
    try:
        async with asyncio.timeout(5):
            async with db.tx() as tx:
                assert await ModelDeploymentRepository(tx, use_transactions=False).delete(
                    policy["selector"]["classifier_deployment_id"],
                )
                publisher = asyncio.create_task(
                    RouteGroupRepository(
                        other, selector_activation_check=lambda: None
                    ).publish_policy(group, policy)
                )
                await attempting.wait()
            with pytest.raises(RoutePolicyStateConflictError, match="existing concrete deployment"):
                await publisher
        assert await RouteGroupRepository(db).get_published_policy(group) is None
    finally:
        if publisher is not None and not publisher.done():
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)
        await other.disconnect()


@pytest.mark.parametrize("mutation", ["delete", "metadata"])
async def test_publication_first_fences_concurrent_target_mutation(independent_database, mutation):
    db, group, policy = independent_database
    other = await connect_prisma()
    task = None
    attempting = asyncio.Event()
    classifier = policy["selector"]["classifier_deployment_id"]

    class ObservedModels(ModelDeploymentRepository):
        def with_db(self, connection):
            return ObservedModels(connection, use_transactions=False)

        async def _lock_route_groups_for_deployment(self, deployment_id):
            attempting.set()
            return await super()._lock_route_groups_for_deployment(deployment_id)

    async def mutate():
        models = ObservedModels(other)
        if mutation == "delete":
            return await models.delete(classifier)
        record = await models.get_by_deployment_id(classifier)
        return await models.update(
            classifier,
            model_name=record.model_name,
            named_credential_id=None,
            deltallm_params=record.deltallm_params,
            model_info={"mode": "chat"},
        )

    try:
        async with asyncio.timeout(5):
            async with db.tx() as tx:
                await RouteGroupRepository(
                    tx, use_transactions=False, selector_activation_check=lambda: None
                )._publish_policy_in_tx(group, policy, published_by=None)
                task = asyncio.create_task(mutate())
                await attempting.wait()
            with pytest.raises(RoutePolicyStateConflictError):
                await task
        assert await RouteGroupRepository(db).get_published_policy(group)
        assert await ModelDeploymentRepository(db).get_by_deployment_id(classifier)
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await other.disconnect()


async def test_shared_selector_protects_disabled_published_groups(independent_database):
    db, group, policy = independent_database
    groups = RouteGroupRepository(db, selector_activation_check=lambda: None)
    sibling = await groups.create_group(
        group_key=group + "-sibling",
        name=None,
        mode="chat",
        routing_strategy=None,
        enabled=True,
        metadata=None,
    )
    try:
        for member in policy["members"]:
            await groups.upsert_member(
                sibling.group_key,
                deployment_id=member["deployment_id"],
                enabled=True,
                weight=None,
                priority=None,
            )
        await groups.publish_policy(group, policy)
        await groups.publish_policy(sibling.group_key, policy)
        await groups.update_group(
            sibling.group_key,
            name=None,
            mode="chat",
            routing_strategy=None,
            enabled=False,
            metadata=None,
        )
        await groups.publish_policy(group, {"selector": None})
        with pytest.raises(RoutePolicyStateConflictError):
            await ModelDeploymentRepository(db).delete(
                policy["selector"]["classifier_deployment_id"]
            )
        await groups.publish_policy(sibling.group_key, {"selector": None})
        assert await ModelDeploymentRepository(db).delete(
            policy["selector"]["classifier_deployment_id"]
        )
    finally:
        await groups.delete_group(sibling.group_key)


async def test_dependency_lock_contention_returns_bounded_safe_conflict(independent_database):
    db, group, policy = independent_database
    other = await connect_prisma()
    try:
        async with db.tx() as tx:
            await route_policy_dependencies.lock_deployment_dependencies(
                tx, {policy["selector"]["classifier_deployment_id"]}
            )
            with pytest.raises(RoutePolicyStateConflictError, match="being changed; retry"):
                await asyncio.wait_for(
                    RouteGroupRepository(
                        other, selector_activation_check=lambda: None
                    ).publish_policy(group, policy),
                    3,
                )
        assert await RouteGroupRepository(db).get_published_policy(group) is None
    finally:
        await other.disconnect()


async def test_inherited_selector_switch_race_requires_retry_without_reversed_locks(
    independent_database, monkeypatch
):
    db, group, policy = independent_database
    groups = RouteGroupRepository(db, selector_activation_check=lambda: None)
    await groups.publish_policy(group, policy)
    other = await connect_prisma()
    attempting = asyncio.Event()
    observed = []
    task = None
    old_id = policy["selector"]["classifier_deployment_id"]
    new_id = policy["members"][1]["deployment_id"]
    lock = route_policy_dependencies.lock_deployment_dependencies

    async def observe(connection, ids):
        observed.append(ids)
        attempting.set()
        await lock(connection, ids)

    try:
        async with asyncio.timeout(5):
            async with db.tx() as tx:
                changed = deepcopy(policy)
                changed["selector"]["classifier_deployment_id"] = new_id
                await RouteGroupRepository(
                    tx, use_transactions=False, selector_activation_check=lambda: None
                )._publish_policy_in_tx(group, changed, published_by=None)
                monkeypatch.setattr(
                    route_policy_dependencies, "lock_deployment_dependencies", observe
                )
                task = asyncio.create_task(
                    RouteGroupRepository(
                        other, selector_activation_check=lambda: None
                    ).publish_policy(group, {"strategy": "weighted"})
                )
                await attempting.wait()
            with pytest.raises(RoutePolicyStateConflictError, match="dependencies changed"):
                await task
        assert observed == [{old_id}]
        active = await groups.get_published_policy(group)
        assert active.policy_json["selector"]["classifier_deployment_id"] == new_id
        retry = await groups.publish_policy(group, {"strategy": "weighted"})
        assert retry.policy.policy_json["selector"]["classifier_deployment_id"] == new_id
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await other.disconnect()
