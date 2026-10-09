"""Transaction-scoped coordination for concrete selector dependencies.

Lock order: callable keys (when needed), sorted physical deployment IDs, group
rows, then mutations. A changed optimistic dependency set aborts the transaction;
we never acquire a newly discovered deployment lock while holding a group lock.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol

from prisma.errors import RawQueryError

from src.db.route_group_identity import RouteGroupLookup

_DEPENDENCY_LOCK_NAMESPACE = 0x53454C44

DEPENDENT_GROUPS_QUERY = """
    SELECT g.route_group_id, g.group_key
    FROM deltallm_routegroup g
    WHERE g.route_group_id IN (
        SELECT m.route_group_id FROM deltallm_routegroupmember m
        WHERE m.deployment_id = $1
        UNION
        SELECT p.route_group_id FROM deltallm_routepolicy p
        WHERE p.status = 'published' AND p.semantics_version >= 3
          AND p.policy_json->'selector'->>'classifier_deployment_id' = $1
    )
    ORDER BY g.group_key ASC
    FOR UPDATE OF g
"""


class RoutePolicyStateConflictError(ValueError):
    """A stored policy no longer matches current route-group dependencies."""


class DependencyConnection(Protocol):
    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]: ...


def selector_deployment_id(document: Mapping[str, object], semantics: int = 3) -> str | None:
    if not isinstance(document, Mapping):
        return None
    selector = document.get("selector") if semantics >= 3 else None
    if not isinstance(selector, Mapping):
        return None
    value = selector.get("classifier_deployment_id")
    # Invalid authored IDs are rejected by contract validation, not used as lock keys.
    return value.strip() if isinstance(value, str) and 0 < len(value.strip()) <= 256 else None


@contextmanager
def dependency_lock_errors() -> Iterator[None]:
    """Map only PostgreSQL lock contention, never other persistence failures."""
    try:
        yield
    except RawQueryError as exc:
        if isinstance(exc.meta, Mapping) and exc.meta.get("code") == "55P03":
            raise RoutePolicyStateConflictError(
                "selector dependency is being changed; retry the mutation"
            ) from exc
        raise


async def lock_deployment_dependencies(db: DependencyConnection, ids: set[str]) -> None:
    if not ids:
        return
    await db.query_raw("SELECT set_config('lock_timeout', '1000ms', true)")
    # A sorted list and one statement keep shared-selector lock acquisition bounded.
    with dependency_lock_errors():
        await db.query_raw(
            """
            SELECT pg_advisory_xact_lock($1::integer, hashtext(deployment_id)::integer)::text
            FROM (SELECT unnest($2::text[]) AS deployment_id ORDER BY deployment_id) dependencies
            """,
            _DEPENDENCY_LOCK_NAMESPACE,
            sorted(ids),
        )


async def _policy_dependencies(
    db: DependencyConnection,
    lookup: RouteGroupLookup,
    target_version: int | None,
) -> set[str]:
    rows = await db.query_raw(
        f"""
        SELECT DISTINCT ON (p.status)
            p.policy_json->'selector'->>'classifier_deployment_id' AS classifier_id
        FROM deltallm_routepolicy p
        JOIN deltallm_routegroup g ON g.route_group_id = p.route_group_id
        WHERE g.{lookup.column} = $1 AND p.semantics_version >= 3
          AND (p.status IN ('published', 'draft') OR p.version = $2::integer)
        ORDER BY p.status, p.version DESC
        """,
        lookup.value,
        target_version,
    )
    return {str(row["classifier_id"]) for row in rows if row.get("classifier_id")}


async def lock_policy_group(
    db: DependencyConnection,
    lookup: RouteGroupLookup,
    *,
    classifier_id: str | None = None,
    target_version: int | None = None,
) -> str | None:
    before = await _policy_dependencies(db, lookup, target_version)
    locked = before | ({classifier_id} if classifier_id is not None else set())
    await lock_deployment_dependencies(db, locked)
    with dependency_lock_errors():
        rows = await db.query_raw(
            f"""
            SELECT route_group_id FROM deltallm_routegroup
            WHERE {lookup.column} = $1 FOR UPDATE
            """,
            lookup.value,
        )
    if not rows:
        return None
    # Use the locked identity: a key-addressed request must not follow key reuse.
    group_id = str(rows[0]["route_group_id"])
    after = await _policy_dependencies(
        db,
        RouteGroupLookup("route_group_id", group_id),
        target_version,
    )
    if not after.issubset(locked):
        raise RoutePolicyStateConflictError("selector dependencies changed; retry the mutation")
    return group_id
