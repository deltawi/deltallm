from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from src.db.repositories import ModelDeploymentRepository
from src.db.callable_targets import CallableTargetBindingRepository
from src.db.route_groups import RouteGroupRepository
from src.db.route_policy_lifecycle import RoutePolicyStateConflictError
from src.services.route_group_mutations import RouteGroupMutationService
from tests.db.tier_migration_helpers import connect_prisma, seed_organization


async def _require_route_policy_schema(db) -> None:  # noqa: ANN001
    rows = await db.query_raw(
        "SELECT to_regclass('public.deltallm_routepolicy')::text AS relation_name"
    )
    if not rows or rows[0].get("relation_name") is None:
        pytest.skip("Route policy tables are missing; run Prisma migrations before this test")
    indexes = await db.query_raw(
        """
        SELECT 1
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND indexname = 'deltallm_routepolicy_one_published_per_group'
        """
    )
    if not indexes:
        pytest.skip("Route policy publication invariant migration is not applied")


async def _seed_group(db, *, group_id: str, group_key: str) -> None:  # noqa: ANN001
    deployment_id = f"{group_id}-deployment"
    await db.execute_raw(
        """
        INSERT INTO deltallm_modeldeployment (
            deployment_id, model_name, deltallm_params, model_info, created_at, updated_at
        )
        VALUES ($1, $2, '{}'::jsonb, '{"mode":"chat"}'::jsonb,
                NOW(), NOW())
        """,
        deployment_id,
        f"route-policy-test-model-{group_id}",
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_routegroup (
            route_group_id, group_key, mode, enabled, created_at, updated_at
        )
        VALUES ($1, $2, 'chat', TRUE, NOW(), NOW())
        """,
        group_id,
        group_key,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_routegroupmember (
            membership_id, route_group_id, deployment_id, enabled, created_at, updated_at
        )
        VALUES ($1, $2, $3, TRUE, NOW(), NOW())
        """,
        f"{group_id}-membership",
        group_id,
        deployment_id,
    )


async def _cleanup_group(db, group_id: str) -> None:  # noqa: ANN001
    await db.execute_raw(
        "DELETE FROM deltallm_routegroup WHERE route_group_id = $1",
        group_id,
    )
    await db.execute_raw(
        "DELETE FROM deltallm_modeldeployment WHERE deployment_id = $1",
        f"{group_id}-deployment",
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_database_rejects_two_published_policies_for_one_group() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    group_id = f"route-policy-group-{suffix}"
    group_key = f"route-policy-{suffix}"
    group_seeded = False

    try:
        await _require_route_policy_schema(db)
        await _seed_group(db, group_id=group_id, group_key=group_key)
        group_seeded = True
        await db.execute_raw(
            """
            INSERT INTO deltallm_routepolicy (
                route_policy_id, route_group_id, version, status, policy_json,
                published_at, created_at, updated_at
            )
            VALUES ($1, $2, 1, 'published', '{}'::jsonb, NOW(), NOW(), NOW())
            """,
            f"route-policy-1-{suffix}",
            group_id,
        )

        with pytest.raises(Exception):
            await db.execute_raw(
                """
                INSERT INTO deltallm_routepolicy (
                    route_policy_id, route_group_id, version, status, policy_json,
                    published_at, created_at, updated_at
                )
                VALUES ($1, $2, 2, 'published', '{}'::jsonb, NOW(), NOW(), NOW())
                """,
                f"route-policy-2-{suffix}",
                group_id,
            )
    finally:
        if group_seeded:
            await _cleanup_group(db, group_id)
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_concurrent_publications_serialize_versions_and_preserve_one_published() -> None:
    first_db = await connect_prisma()
    second_db = await connect_prisma()
    suffix = uuid4().hex
    group_id = f"route-policy-concurrent-group-{suffix}"
    group_key = f"route-policy-concurrent-{suffix}"
    group_seeded = False

    try:
        await _require_route_policy_schema(first_db)
        await _seed_group(first_db, group_id=group_id, group_key=group_key)
        group_seeded = True
        first_repo = RouteGroupRepository(first_db)
        second_repo = RouteGroupRepository(second_db)

        first, second = await asyncio.gather(
            first_repo.publish_policy(
                group_key,
                {"strategy": "weighted"},
                published_by="test-first",
            ),
            second_repo.publish_policy(
                group_key,
                {"strategy": "least-busy"},
                published_by="test-second",
            ),
        )

        assert first is not None
        assert second is not None
        assert {first.version, second.version} == {1, 2}
        rows = await first_db.query_raw(
            """
            SELECT version, status, policy_json
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
            ORDER BY version
            """,
            group_id,
        )
        assert [row["status"] for row in rows] == ["archived", "published"]
        assert [int(row["version"]) for row in rows] == [1, 2]
        assert all(isinstance(row["policy_json"], (dict, str)) for row in rows)
        assert {
            json.loads(row["policy_json"])["strategy"]
            if isinstance(row["policy_json"], str)
            else row["policy_json"]["strategy"]
            for row in rows
        } == {"weighted", "least-busy"}
    finally:
        if group_seeded:
            await _cleanup_group(first_db, group_id)
        await second_db.disconnect()
        await first_db.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_member_removal_and_publication_share_group_lock_and_preserve_valid_policy() -> None:
    first_db = await connect_prisma()
    second_db = await connect_prisma()
    suffix = uuid4().hex
    group_id = f"route-policy-member-race-{suffix}"
    group_key = f"route-policy-member-race-{suffix}"
    deployment_id = f"{group_id}-deployment"
    group_seeded = False

    try:
        await _require_route_policy_schema(first_db)
        await _seed_group(first_db, group_id=group_id, group_key=group_key)
        group_seeded = True
        first_repo = RouteGroupRepository(first_db)
        second_repo = RouteGroupRepository(second_db)
        await first_repo.publish_policy(
            group_key,
            {"members": [{"deployment_id": deployment_id}]},
            published_by="test-initial",
        )

        removal, publication = await asyncio.gather(
            first_repo.remove_member(group_key, deployment_id),
            second_repo.publish_policy(
                group_key,
                {"members": [{"deployment_id": deployment_id}], "strategy": "weighted"},
                published_by="test-concurrent",
            ),
            return_exceptions=True,
        )

        assert isinstance(removal, RoutePolicyStateConflictError)
        assert not isinstance(publication, Exception)
        members = await first_repo.list_members(group_key)
        assert [member.deployment_id for member in members] == [deployment_id]
        published_rows = await first_db.query_raw(
            """
            SELECT count(*)::int AS count
            FROM deltallm_routepolicy
            WHERE route_group_id = $1 AND status = 'published'
            """,
            group_id,
        )
        assert int(published_rows[0]["count"]) == 1
    finally:
        if group_seeded:
            await _cleanup_group(first_db, group_id)
        await second_db.disconnect()
        await first_db.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_deployment_delete_cannot_cascade_into_an_invalid_published_policy() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    group_id = f"route-policy-model-delete-{suffix}"
    group_key = f"route-policy-model-delete-{suffix}"
    deployment_id = f"{group_id}-deployment"
    group_seeded = False

    try:
        await _require_route_policy_schema(db)
        await _seed_group(db, group_id=group_id, group_key=group_key)
        group_seeded = True
        await RouteGroupRepository(db).publish_policy(
            group_key,
            {"members": [{"deployment_id": deployment_id}]},
            published_by="test-initial",
        )

        with pytest.raises(RoutePolicyStateConflictError, match="would invalidate route group"):
            await ModelDeploymentRepository(db).delete(deployment_id)

        deployments = await db.query_raw(
            "SELECT deployment_id FROM deltallm_modeldeployment WHERE deployment_id = $1",
            deployment_id,
        )
        members = await RouteGroupRepository(db).list_members(group_key)
        assert [str(row["deployment_id"]) for row in deployments] == [deployment_id]
        assert [member.deployment_id for member in members] == [deployment_id]
    finally:
        if group_seeded:
            await _cleanup_group(db, group_id)
        await db.disconnect()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_group_delete_preserves_callable_binding_for_revealed_model() -> None:
    db = await connect_prisma()
    suffix = uuid4().hex
    group_id = f"route-policy-collision-group-{suffix}"
    group_key = f"route-policy-collision-{suffix}"
    organization_id = f"organization-{suffix}"
    group_seeded = False
    organization_seeded = False

    try:
        await _require_route_policy_schema(db)
        await seed_organization(db, organization_id=organization_id)
        organization_seeded = True
        await _seed_group(db, group_id=group_id, group_key=group_key)
        group_seeded = True
        await db.execute_raw(
            """
            UPDATE deltallm_modeldeployment
            SET model_name = $1, updated_at = NOW()
            WHERE deployment_id = $2
            """,
            group_key,
            f"{group_id}-deployment",
        )
        bindings = CallableTargetBindingRepository(db)
        await bindings.upsert_binding(
            callable_key=group_key,
            scope_type="organization",
            scope_id=organization_id,
            enabled=True,
            metadata=None,
        )
        service = RouteGroupMutationService(
            route_groups=RouteGroupRepository(db),
            callable_bindings=bindings,
            model_deployments=ModelDeploymentRepository(db),
        )

        result = await service.delete_group(group_key)

        assert result.deleted is True
        assert result.callable_bindings_deleted == 0
        remaining_bindings, total = await bindings.list_bindings(callable_key=group_key)
        assert total == 1
        assert remaining_bindings[0].scope_id == organization_id
    finally:
        if group_seeded:
            await db.execute_raw(
                "DELETE FROM deltallm_callabletargetbinding WHERE callable_key = $1",
                group_key,
            )
            await _cleanup_group(db, group_id)
        if organization_seeded:
            await db.execute_raw(
                "DELETE FROM deltallm_organizationtable WHERE organization_id = $1",
                organization_id,
            )
        await db.disconnect()
