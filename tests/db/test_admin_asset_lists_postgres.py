from collections.abc import AsyncIterator
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.db.catalog.admin_asset_lists import GroupSortKey, PromptSortKey, list_asset_rows
from src.db.catalog.logical_models import LogicalModelRepository
from src.db.catalog.prompt_registry import PromptRegistryRepository
from src.db.catalog.model_deployments import ModelDeploymentRepository
from src.db.routing.route_groups import RouteGroupRepository
from src.services.reporting.admin_list_health import ListHealthSnapshot

pytestmark = pytest.mark.postgres


@pytest.fixture
async def list_database() -> AsyncIterator[tuple[Prisma, str]]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    prefix = "list-test-" + uuid4().hex
    try:
        async with db.tx(timeout=timedelta(seconds=30)) as tx:
            await _seed_lists(tx, prefix)
            yield tx, prefix
            # Roll back all fixture rows, including when a test fails.
            raise _RollbackFixture()
    except _RollbackFixture:
        pass
    finally:
        await db.disconnect()


class _RollbackFixture(Exception):
    pass


async def _seed_lists(db: Prisma, prefix: str) -> None:
    await db.execute_raw(
        "INSERT INTO deltallm_platformaccount(account_id,email,role,updated_at) VALUES ($1,$2,'platform_admin',NOW())",
        prefix,
        prefix + "@example.invalid",
    )
    await db.execute_raw(
        "INSERT INTO deltallm_teamtable(team_id) VALUES ($1),($2)",
        prefix + "-team1",
        prefix + "-team2",
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_managedasset(asset_id,asset_kind,created_by_account_id,updated_at)
        SELECT $1 || '-' || kind || '-' || n, kind, $1, NOW()
        FROM unnest(ARRAY['route_group','prompt_template','model']) kind CROSS JOIN generate_series(1,6) n
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_routegroup(route_group_id,group_key,name,managed_asset_id,enabled,updated_at)
        SELECT $1 || '-g' || n, $1 || '-group' || n, 'Group ' || n, $1 || '-route_group-' || n,
               n <> 6, TIMESTAMP '2026-10-01' + make_interval(days => n)
        FROM generate_series(1,6) n
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_model(model_id,model_name,managed_asset_id,updated_at)
        VALUES ($1 || '-model', $1 || '-callable', $1 || '-model-1', TIMESTAMP '2026-10-04')
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_modeldeployment(deployment_id,model_name,model_id,deltallm_params,updated_at)
        SELECT $1 || '-' || state, $1 || '-callable', $1 || '-model', '{"model":"openai/gpt"}'::jsonb, TIMESTAMP '2026-10-05'
        FROM unnest(ARRAY['good','bad','unknown']) state
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_routegroupmember(membership_id,route_group_id,deployment_id,enabled,updated_at)
        SELECT $1 || '-member' || n, $1 || '-g' || n, $1 || '-' || CASE n WHEN 3 THEN 'bad' WHEN 4 THEN 'unknown' ELSE 'good' END,
               n <> 5, NOW() FROM generate_series(1,6) n
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_routegroupmember(membership_id,route_group_id,deployment_id,updated_at)
        VALUES ($1 || '-extra', $1 || '-g2', $1 || '-bad', NOW())
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_prompttemplate(prompt_template_id,template_key,name,managed_asset_id,updated_at)
        SELECT $1 || '-p' || n, $1 || '-prompt' || n, 'Prompt ' || n, $1 || '-prompt_template-' || n,
               TIMESTAMP '2026-10-01' + make_interval(days => n) FROM generate_series(1,6) n
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_promptversion(prompt_version_id,prompt_template_id,version,template_body,updated_at)
        SELECT $1 || '-v' || n, $1 || '-p' || n, 1, '{}'::jsonb, NOW() FROM generate_series(1,6) n
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_promptlabel(prompt_label_id,prompt_template_id,prompt_version_id,label,updated_at)
        SELECT $1 || '-label' || n, $1 || '-p' || n, $1 || '-v' || n, 'production', NOW() FROM generate_series(1,3) n
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_promptbinding(prompt_binding_id,prompt_template_id,scope_type,scope_id,label,updated_at)
        SELECT $1 || '-binding' || n, $1 || '-p2', 'team', $1 || '-team' || n, 'production', NOW() FROM generate_series(1,2) n
    """,
        prefix,
    )
    await db.execute_raw(
        """
        INSERT INTO deltallm_assetgrant(grant_id,managed_asset_id,subject_type,team_id,access_role,updated_at)
        SELECT $1 || '-' || kind || '-grant' || n, $1 || '-' || kind || '-2', 'team', $1 || '-team' || n, 'reader', NOW()
        FROM unnest(ARRAY['route_group','prompt_template']) kind CROSS JOIN generate_series(1,2) n
    """,
        prefix,
    )


def _health(prefix: str) -> ListHealthSnapshot:
    return ListHealthSnapshot([prefix + "-good"], [prefix + "-unknown"])


@pytest.mark.asyncio
async def test_group_health_sort_counts_and_scope(list_database: tuple[Prisma, str]) -> None:
    db, prefix = list_database
    repo = RouteGroupRepository(db)
    groups, total = await repo.list_groups(
        search=prefix,
        limit=3,
        offset=0,
        sort_by="health",
        sort_direction="asc",
        health=_health(prefix),
    )
    assert total == 6
    assert [row.health_status for row in groups] == ["unhealthy", "degraded", "unknown"]
    assert groups[1].member_count == 2
    assert groups[1].active_member_count == 2
    assert groups[1].healthy_member_count == 1
    assert (
        groups[1].visibility == "team"
    )  # Two team grants must not multiply rows or change visibility.
    assert groups[1].created_by_user_id == prefix
    assert groups[2].healthy_member_count is None
    later, _ = await repo.list_groups(
        search=prefix,
        limit=3,
        offset=3,
        sort_by="health",
        sort_direction="asc",
        health=_health(prefix),
    )
    assert [row.health_status for row in later] == ["empty", "paused", "healthy"]
    scoped, count = await repo.list_groups(
        search=prefix,
        managed_asset_ids=[prefix + "-route_group-2"],
        sort_by="updated_at",
        health=_health(prefix),
    )
    assert count == 1
    assert [row.route_group_id for row in scoped] == [prefix + "-g2"]
    unknown, _ = await repo.list_groups(search=prefix, health=None)
    assert all(
        row.health_status == "unknown" for row in unknown if row.enabled and row.active_member_count
    )


@pytest.mark.asyncio
async def test_prompt_counts_sort_before_paging_and_creator_id(
    list_database: tuple[Prisma, str],
) -> None:
    db, prefix = list_database
    repo = PromptRegistryRepository(db)
    rows, total = await repo.list_templates(
        search=prefix, sort_by="bindings", sort_direction="desc", limit=1
    )
    assert total == 6
    assert rows[0].template_key == prefix + "-prompt2"
    assert rows[0].version_count == 1
    assert rows[0].label_count == 1
    assert rows[0].binding_count == 2
    assert rows[0].created_by_user_id == prefix
    assert "@" not in rows[0].created_by_user_id
    newest, _ = await repo.list_templates(
        search=prefix, sort_by="updated_at", sort_direction="desc", limit=1, offset=1
    )
    assert newest[0].template_key == prefix + "-prompt5"
    scoped, count = await repo.list_templates(
        search=prefix, managed_asset_ids=[prefix + "-prompt_template-3"], sort_by="name"
    )
    assert count == 1
    assert scoped[0].template_key == prefix + "-prompt3"


@pytest.mark.asyncio
async def test_all_allowed_sql_sorts_execute_and_keep_stable_pages(
    list_database: tuple[Prisma, str],
) -> None:
    db, prefix = list_database
    group_keys: list[GroupSortKey] = [
        "name",
        "routing",
        "members",
        "health",
        "created_by",
        "updated_at",
        "visibility",
    ]
    prompt_keys: list[PromptSortKey] = [
        "name",
        "versions",
        "labels",
        "bindings",
        "created_by",
        "updated_at",
        "visibility",
    ]
    for kind, keys in [("group", group_keys), ("prompt", prompt_keys)]:
        for key in keys:
            first = await list_asset_rows(
                db,
                kind=kind,
                search=prefix,
                limit=3,
                offset=0,
                managed_asset_ids=None,
                sort_by=key,
                sort_direction="desc",
                health=_health(prefix),
            )
            second = await list_asset_rows(
                db,
                kind=kind,
                search=prefix,
                limit=3,
                offset=3,
                managed_asset_ids=None,
                sort_by=key,
                sort_direction="desc",
                health=_health(prefix),
            )
            id_key = "route_group_id" if kind == "group" else "prompt_template_id"
            assert first.total == second.total == 6
            assert len({row[id_key] for row in first.rows + second.rows}) == 6


@pytest.mark.asyncio
async def test_model_metadata_uses_managed_creator_and_deployment_dates(
    list_database: tuple[Prisma, str],
) -> None:
    db, prefix = list_database
    models = await LogicalModelRepository(db).list_by_names([prefix + "-callable"])
    assert len(models) == 1
    assert models[0].created_by_user_id == prefix
    assert models[0].updated_at is not None
    deployments = [
        row
        for row in await ModelDeploymentRepository(db).list_all()
        if row.model_name == prefix + "-callable"
    ]
    assert len(deployments) == 3
    assert all(row.updated_at is not None and row.updated_at.day == 5 for row in deployments)
