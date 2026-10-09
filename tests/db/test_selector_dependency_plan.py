import json
from uuid import uuid4

import pytest

from src.db.route_policy_dependencies import DEPENDENT_GROUPS_QUERY
from tests.db.tier_migration_helpers import connect_prisma
from tests.router.selection.independent_fixtures import independent_policy

pytestmark = pytest.mark.postgres


async def test_published_dependency_lookup_uses_index_at_representative_cardinality():
    db = await connect_prisma()
    prefix = "selector-plan-" + uuid4().hex
    try:
        async with db.tx() as tx:
            await tx.execute_raw(
                """
                INSERT INTO deltallm_routegroup(route_group_id, group_key, updated_at)
                SELECT $1 || '-' || n, $1 || '-' || n, NOW()
                FROM generate_series(1, 5000) n
                """,
                prefix,
            )
            await tx.execute_raw(
                """
                INSERT INTO deltallm_routepolicy
                    (route_policy_id, route_group_id, version, semantics_version, status, policy_json, updated_at)
                SELECT $1 || '-' || n || '-' || v, $1 || '-' || n, v, 3,
                       CASE WHEN v=4 THEN 'published' ELSE 'archived' END,
                       jsonb_set($2::jsonb, '{selector,classifier_deployment_id}', to_jsonb($1 || '-target-' || n)), NOW()
                FROM generate_series(1, 5000) n CROSS JOIN generate_series(1, 4) v
                """,
                prefix,
                json.dumps(independent_policy()),
            )
        await db.execute_raw("ANALYZE deltallm_routepolicy")
        await db.execute_raw("ANALYZE deltallm_routegroup")
        rows = await db.query_raw(
            "EXPLAIN (ANALYZE, FORMAT JSON) " + DEPENDENT_GROUPS_QUERY, prefix + "-target-1"
        )
        plan = rows[0]["QUERY PLAN"]
        if isinstance(plan, str):
            plan = json.loads(plan)
        assert "deltallm_routepolicy_selector_dependency_idx" in json.dumps(plan)
        assert plan[0]["Plan"]["Actual Rows"] == 1
        print(json.dumps(plan, sort_keys=True))
    finally:
        await db.execute_raw(
            "DELETE FROM deltallm_routegroup WHERE route_group_id LIKE $1", prefix + "-%"
        )
        await db.disconnect()
