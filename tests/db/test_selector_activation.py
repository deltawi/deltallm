import json
from copy import deepcopy

import pytest

from src.db.route_groups import RouteGroupRepository
from src.db.route_policy_lifecycle import RoutePolicyStateConflictError
from src.router.selection.policy import RouteSelectorActivationUnsupportedError
from tests.db import test_route_policy_selector_integration as fixtures

selector_database = fixtures.selector_database
pytestmark = pytest.mark.postgres


async def qualify(db, policy):
    policy["context"] = {"mode": "eligible-only", "unknown_capacity": "exclude"}
    info = {
        "mode": "chat",
        "chat_capabilities": {"streaming": True},
        "max_tokens": 32768,
        "input_cost_per_token": 0.000001,
        "output_cost_per_token": 0.000002,
        "rpm_limit": 100,
        "tpm_limit": 1000000,
    }
    await db.execute_raw(
        "UPDATE deltallm_modeldeployment SET model_info=$2::jsonb,deltallm_params=jsonb_build_object('model','openai/mock') WHERE deployment_id=ANY($1::text[])",
        [member["deployment_id"] for member in policy["members"]],
        json.dumps(info),
    )
    return info


async def test_selector_publish_draft_remove_and_rollback_are_versioned_atomic_mutations(
    selector_database,
):
    db, group, policy = selector_database
    await qualify(db, policy)
    repository = RouteGroupRepository(db, selector_activation_check=lambda: None)
    revision = await repository.get_runtime_revision()
    first = await repository.publish_policy(group, policy)
    assert first.policy.version == 1
    snapshot = await repository.load_runtime_snapshot()
    active = next(item for item in snapshot.groups if item["key"] == group)
    assert active["selector"]["kind"] == "llm-tier"
    assert {member["lane"] for member in active["members"]} == {"economy", "quality"}
    await repository.save_draft_policy(group, {"strategy": "weighted"})
    second = await repository.publish_latest_draft(group)
    assert (
        second.version == 2
        and second.policy_json["selector"] == first.policy.policy_json["selector"]
    )
    third = await repository.publish_policy(group, {"selector": None})
    assert third.policy.version == 3 and "selector" not in third.policy.policy_json
    fourth = await repository.rollback_policy(group, target_version=1)
    assert fourth.version == 4 and fourth.policy_json == first.policy.policy_json
    assert await repository.get_runtime_revision() == revision + 4
    rows = await fixtures._rows(db, group)
    assert [row["status"] for row in rows] == ["archived", "archived", "archived", "published"]


@pytest.mark.parametrize(
    "missing",
    [
        "readiness",
        "chat_capabilities",
        "max_tokens",
        "input_cost_per_token",
        "output_cost_per_token",
        "rpm_limit",
        "tpm_limit",
    ],
)
async def test_unqualified_selector_cannot_archive_a_working_policy(selector_database, missing):
    db, group, policy = selector_database
    info = await qualify(db, policy)
    repository = RouteGroupRepository(
        db, selector_activation_check=(None if missing == "readiness" else lambda: None)
    )
    await repository.publish_policy(group, {"strategy": "weighted"})
    revision = await repository.get_runtime_revision()
    if missing != "readiness":
        info.pop(missing)
        await db.execute_raw(
            "UPDATE deltallm_modeldeployment SET model_info=$2::jsonb WHERE deployment_id=$1",
            policy["selector"]["classifier_deployment_id"],
            json.dumps(info),
        )
    error = RouteSelectorActivationUnsupportedError if missing == "readiness" else ValueError
    with pytest.raises(error):
        await repository.publish_policy(group, policy)
    assert await repository.get_runtime_revision() == revision
    current = await repository.get_published_policy(group)
    assert current.version == 1 and current.policy_json == {"strategy": "weighted"}


async def test_active_selector_model_metadata_edit_rolls_back_atomically(selector_database):
    from src.db.repositories import ModelDeploymentRepository
    from src.db.route_policy_lifecycle import RoutePolicyStateConflictError

    db, group, policy = selector_database
    info = await qualify(db, policy)
    repository = RouteGroupRepository(db, selector_activation_check=lambda: None)
    await repository.publish_policy(group, policy)
    revision = await repository.get_runtime_revision()
    models = ModelDeploymentRepository(db)
    classifier = policy["selector"]["classifier_deployment_id"]
    record = await models.get_by_deployment_id(classifier)
    invalid = dict(info)
    invalid.pop("chat_capabilities")
    with pytest.raises(RoutePolicyStateConflictError, match="chat_capabilities"):
        await models.update(
            classifier,
            model_name=record.model_name,
            named_credential_id=None,
            deltallm_params=record.deltallm_params,
            model_info=invalid,
        )
    assert (await models.get_by_deployment_id(classifier)).model_info == info
    assert await repository.get_runtime_revision() == revision


async def test_rollback_to_now_unqualified_selector_is_a_safe_policy_error(selector_database):
    db, group, policy = selector_database
    await qualify(db, policy)
    repository = RouteGroupRepository(db, selector_activation_check=lambda: None)
    await repository.publish_policy(group, policy)
    await repository.publish_policy(group, {"selector": None})
    revision = await repository.get_runtime_revision()
    await db.execute_raw(
        "UPDATE deltallm_modeldeployment SET model_info=model_info-'chat_capabilities' WHERE deployment_id=$1",
        policy["selector"]["classifier_deployment_id"],
    )
    with pytest.raises(RouteSelectorActivationUnsupportedError, match="chat_capabilities"):
        await repository.rollback_policy(group, target_version=1)
    assert (await repository.get_published_policy(group)).version == 2
    assert await repository.get_runtime_revision() == revision


async def test_selector_lifecycle_qualifies_projection_and_preserves_opaque_member_fields(
    selector_database,
):
    db, group, policy = selector_database
    await qualify(db, policy)
    repository = RouteGroupRepository(db, selector_activation_check=lambda: None)
    first = (await repository.publish_policy(group, policy)).policy
    stored = deepcopy(first.policy_json)
    extension = {"nested": ["retained", {"revision": 7}]}
    stored["members"][0]["server_extension"] = extension
    await db.execute_raw(
        "UPDATE deltallm_routepolicy SET policy_json=$2::jsonb WHERE route_policy_id=$1",
        first.route_policy_id,
        json.dumps(stored),
    )
    before = await fixtures._rows(db, group)
    revision = await repository.get_runtime_revision()
    with pytest.raises(ValueError):
        await repository.publish_policy(group, {"members": stored["members"]})
    assert await fixtures._rows(db, group) == before
    assert await repository.get_runtime_revision() == revision

    direct = (await repository.publish_policy(group, {"strategy": "weighted"})).policy
    assert direct.policy_json["members"][0]["server_extension"] == extension
    draft = await repository.save_draft_policy(group, {"strategy": "priority-based-routing"})
    assert draft.policy.policy_json["members"][0]["server_extension"] == extension
    published = await repository.publish_latest_draft(group)
    removed = (await repository.publish_policy(group, {"selector": None})).policy
    assert "selector" not in removed.policy_json
    assert "lane" not in removed.policy_json["members"][0]
    assert removed.policy_json["members"][0]["server_extension"] == extension
    rolled = await repository.rollback_policy(group, target_version=first.version)
    assert rolled.policy_json == stored
    assert [direct.version, published.version, removed.version, rolled.version] == [2, 3, 4, 5]
    assert await repository.get_runtime_revision() == revision + 4
    assert [row["status"] for row in await fixtures._rows(db, group)] == [
        "archived",
        "archived",
        "archived",
        "archived",
        "published",
    ]


@pytest.mark.parametrize("disabled_by", ["group", "policy"])
@pytest.mark.parametrize("missing", ["chat_capabilities", "max_tokens"])
async def test_activation_only_qualifies_effectively_enabled_members(
    selector_database, disabled_by, missing
):
    db, group, policy = selector_database
    info = await qualify(db, policy)
    extra_id = f"{group}-disabled"
    info.pop(missing)
    repository = RouteGroupRepository(db, selector_activation_check=lambda: None)
    try:
        await db.execute_raw(
            """
            INSERT INTO deltallm_modeldeployment
                (deployment_id, model_name, deltallm_params, model_info, created_at, updated_at)
            VALUES ($1, $1, '{"model":"openai/mock"}'::jsonb, $2::jsonb, NOW(), NOW())
            """,
            extra_id,
            json.dumps(info),
        )
        await repository.upsert_member(
            group,
            deployment_id=extra_id,
            enabled=disabled_by != "group",
            weight=None,
            priority=None,
        )
        member = {"deployment_id": extra_id, "lane": "quality"}
        if disabled_by == "policy":
            member["enabled"] = False
        policy["members"].append(member)
        await repository.publish_policy(group, policy)
        # All publication paths must use the same effective-membership rule.
        await repository.save_draft_policy(group, {"strategy": "weighted"})
        await repository.publish_latest_draft(group)
        await repository.rollback_policy(group, target_version=1)
        # An unrelated active-group edit must not requalify disabled inventory.
        await repository.upsert_member(
            group,
            deployment_id=policy["members"][1]["deployment_id"],
            enabled=True,
            weight=2,
            priority=None,
        )
        before = await fixtures._rows(db, group)
        revision = await repository.get_runtime_revision()
        if disabled_by == "group":
            with pytest.raises(RoutePolicyStateConflictError):
                await repository.upsert_member(
                    group,
                    deployment_id=extra_id,
                    enabled=True,
                    weight=None,
                    priority=None,
                )
            assert not next(
                m for m in await repository.list_members(group) if m.deployment_id == extra_id
            ).enabled
        else:
            member["enabled"] = True
            with pytest.raises(RouteSelectorActivationUnsupportedError):
                await repository.publish_policy(group, policy)
        assert await fixtures._rows(db, group) == before
        assert await repository.get_runtime_revision() == revision
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_modeldeployment WHERE deployment_id=$1", extra_id
        )
