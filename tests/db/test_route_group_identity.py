from __future__ import annotations

from uuid import uuid4

import pytest

from src.db.route_group_identity import RouteGroupIdentity, RouteGroupIdentityNotFoundError
from src.db.route_groups import RouteGroupRepository
from src.services.route_group_mutations import RouteGroupMutationService
from tests.db.test_route_policy_publication_invariants import _cleanup_group, _seed_group
from tests.db.tier_migration_helpers import connect_prisma

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_id_lookup_preserves_exact_keys_and_separate_fallback_identity() -> None:
    db = await connect_prisma()
    group_id, fallback_id = str(uuid4()), str(uuid4())
    group_key = f"vendor/%2F?model#مجموعة/{group_id}"
    fallback_key = group_id
    try:
        await _seed_group(db, group_id=group_id, group_key=group_key)
        await _seed_group(db, group_id=fallback_id, group_key=fallback_key)
        repository = RouteGroupRepository(db)
        group = await repository.get_group_by_id(group_id)
        assert group is not None
        assert group.group_key == group_key
        keyed = await repository.get_group(fallback_key)
        assert keyed is not None and keyed.route_group_id == fallback_id
        pinned = repository.for_identity(RouteGroupIdentity(group_key, group_id))
        assert (await pinned.get_group(group_key)).route_group_id == group_id
        assert (await pinned.get_group(fallback_key)).route_group_id == fallback_id
        policy = await pinned.publish_policy(group_key, {"strategy": "weighted"})
        assert policy is not None and policy.policy.route_group_id == group_id
        snapshot = await pinned.load_runtime_snapshot()
        assert {group_key, fallback_key} <= {group["key"] for group in snapshot.groups}
        updated = await pinned.update_group(
            group_key,
            name="Display name",
            mode="chat",
            routing_strategy="weighted",
            enabled=True,
            metadata=None,
        )
        assert updated is not None and updated.group_key == group_key
        assert updated.name == "Display name"
        assert (await pinned.list_members(group_key))[0].route_group_id == group_id
    finally:
        await _cleanup_group(db, group_id)
        await _cleanup_group(db, fallback_id)
        await db.disconnect()


@pytest.mark.asyncio
async def test_deleted_id_cannot_read_or_mutate_a_reused_key() -> None:
    db = await connect_prisma()
    writer = await connect_prisma()
    old_id, new_id = str(uuid4()), str(uuid4())
    group_key = f"vendor/reused/{old_id}"
    try:
        await _seed_group(db, group_id=old_id, group_key=group_key)
        repository = RouteGroupRepository(db)
        pinned = repository.for_identity(RouteGroupIdentity(group_key, old_id))
        # Interleave another connection's committed replacement between the
        # requested-ID lookup and the simulation's runtime snapshot.
        original = await pinned.get_group(group_key)
        assert original is not None and original.route_group_id == old_id
        assert await RouteGroupRepository(writer).delete_group(group_key)
        await _seed_group(writer, group_id=new_id, group_key=group_key)
        with pytest.raises(RouteGroupIdentityNotFoundError, match="Route group not found"):
            await pinned.load_runtime_snapshot()
        replacement_snapshot = await repository.for_identity(
            RouteGroupIdentity(group_key, new_id)
        ).load_runtime_snapshot()
        assert group_key in {group["key"] for group in replacement_snapshot.groups}
        await db.execute_raw(
            """UPDATE deltallm_routegroup
               SET metadata = '{"default_prompt":{"template_key":"replacement-prompt"}}'::jsonb
               WHERE route_group_id = $1""",
            new_id,
        )
        assert await repository.get_default_prompt(group_key) == {
            "template_key": "replacement-prompt"
        }
        published = await repository.publish_policy(group_key, {"strategy": "weighted"})
        assert published is not None
        assert await repository.get_group_by_id(old_id) is None
        assert await pinned.get_group(group_key) is None
        assert await pinned.get_default_prompt(group_key) is None
        assert await pinned.list_members(group_key) == []
        assert await pinned.get_published_policy(group_key) is None
        assert await pinned.list_policies(group_key) == []
        assert await pinned.list_bindings(group_key=group_key) == ([], 0)
        assert (
            await pinned.update_group(
                group_key,
                name="Wrong group",
                mode="chat",
                routing_strategy=None,
                enabled=False,
                metadata=None,
            )
            is None
        )
        assert (
            await pinned.upsert_member(
                group_key,
                deployment_id=f"{new_id}-deployment",
                enabled=True,
                weight=None,
                priority=None,
            )
            is None
        )
        assert not await pinned.remove_member(group_key, f"{new_id}-deployment")
        assert await pinned.save_draft_policy(group_key, {"strategy": "least-busy"}) is None
        assert await pinned.publish_policy(group_key, {"strategy": "least-busy"}) is None
        assert await pinned.publish_latest_draft(group_key) is None
        assert await pinned.rollback_policy(group_key, target_version=1) is None
        deletion = await RouteGroupMutationService(
            route_groups=pinned, callable_bindings=None
        ).delete_group(group_key)
        assert not deletion.deleted
        replacement = await repository.get_group_by_id(new_id)
        assert replacement is not None and replacement.enabled and replacement.name is None
        assert len(await repository.list_members(group_key)) == 1
        assert (
            await repository.get_published_policy(group_key)
        ).route_policy_id == published.policy.route_policy_id
    finally:
        await _cleanup_group(db, old_id)
        await _cleanup_group(db, new_id)
        await writer.disconnect()
        await db.disconnect()
