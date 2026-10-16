from __future__ import annotations

from uuid import uuid4

import pytest

from src.db.routing.route_groups import RouteGroupRepository
from tests.db.test_route_policy_publication_invariants import (
    _cleanup_group,
    _require_route_policy_schema,
    _seed_group,
)
from tests.db.tier_migration_helpers import connect_prisma

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
@pytest.mark.integration
async def test_discard_draft_keeps_published_policy_and_reads_publisher_email() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    group_id = f"route-policy-draft-{suffix}"
    group_key = f"route-policy-draft-{suffix}"
    account_id = f"route-policy-publisher-{suffix}"
    group_seeded = False
    account_seeded = False

    try:
        await _require_route_policy_schema(db)
        await _seed_group(db, group_id=group_id, group_key=group_key)
        group_seeded = True
        await db.execute_raw(
            "INSERT INTO deltallm_platformaccount (account_id, email, updated_at)"
            " VALUES ($1, $1 || '@example.com', NOW())",
            account_id,
        )
        account_seeded = True
        repository = RouteGroupRepository(db)
        await repository.publish_policy(
            group_key, {"strategy": "weighted"}, published_by=account_id
        )
        draft = await repository.save_draft_policy(group_key, {"strategy": "least-busy"})
        assert draft is not None

        discarded = await repository.discard_draft_policy(group_key)

        assert discarded is not None
        assert (discarded.version, discarded.status) == (draft.policy.version, "draft")
        assert await repository.discard_draft_policy(group_key) is None
        policies = await repository.list_policies(group_key)
        assert [(policy.version, policy.status) for policy in policies] == [(1, "published")]
        assert policies[0].published_by == account_id
        assert policies[0].published_by_email == f"{account_id}@example.com"
        published = await repository.get_published_policy(group_key)
        assert published is not None
        assert published.policy_json["strategy"] == "weighted"
        assert published.published_by_email == f"{account_id}@example.com"

        # The master key has no account, so no email joins to its publications.
        await repository.publish_policy(
            group_key, {"strategy": "latency-based-routing"}, published_by="admin_api"
        )
        latest = await repository.get_published_policy(group_key)
        assert latest is not None
        assert (latest.published_by, latest.published_by_email) == ("admin_api", None)
    finally:
        if group_seeded:
            await _cleanup_group(db, group_id)
        if account_seeded:
            await db.execute_raw(
                "DELETE FROM deltallm_platformaccount WHERE account_id = $1", account_id
            )
        await db.disconnect()
