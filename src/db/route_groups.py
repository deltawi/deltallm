from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.db.route_policy_lifecycle import (
    RoutePolicyLifecycleMixin,
    RoutePolicyRecord,
    RoutePolicyStateConflictError,
    parse_policy_json,
)
from src.db.routing_runtime import ROUTING_RUNTIME_STATE_KEY, RoutingRuntimeRevisionRepository
from src.router.policy_validation import merge_policy_members
from src.router.route_group_validation import (
    deployment_modes_by_id,
    validate_route_group_member_modes,
)
from src.services.asset_ownership import owner_scope_from_metadata

__all__ = [
    "RouteGroupBindingRecord",
    "RouteGroupMemberRecord",
    "RouteGroupRecord",
    "RouteGroupRepository",
    "RouteGroupRuntimeSnapshot",
    "RoutePolicyRecord",
]


def _parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def _extract_default_prompt(metadata: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("default_prompt")
    if not isinstance(raw, dict):
        return None
    template_key = str(raw.get("template_key") or "").strip()
    if not template_key:
        return None
    label = str(raw.get("label") or "").strip()
    payload: dict[str, str] = {"template_key": template_key}
    if label:
        payload["label"] = label
    return payload


@dataclass
class RouteGroupRecord:
    route_group_id: str
    group_key: str
    name: str | None = None
    mode: str = "chat"
    routing_strategy: str | None = None
    enabled: bool = True
    member_count: int = 0
    metadata: dict[str, Any] | None = None
    default_prompt: dict[str, str] | None = None
    owner_scope_type: str = "global"
    owner_scope_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class RouteGroupMemberRecord:
    membership_id: str
    route_group_id: str
    deployment_id: str
    enabled: bool = True
    weight: int | None = None
    priority: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class RouteGroupBindingRecord:
    route_group_binding_id: str
    route_group_id: str
    group_key: str
    scope_type: str
    scope_id: str
    enabled: bool = True
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RouteGroupRuntimeSnapshot:
    revision: int
    groups: list[dict[str, Any]]
    database_initialized: bool | None = None

    def __post_init__(self) -> None:
        if self.database_initialized is None:
            object.__setattr__(self, "database_initialized", bool(self.groups))


class RouteGroupRepository(RoutePolicyLifecycleMixin):
    def __init__(self, prisma_client: Any | None = None, *, use_transactions: bool = True) -> None:
        self.prisma = prisma_client
        self._use_transactions = use_transactions

    def with_db(self, prisma_client: Any) -> RouteGroupRepository:
        return RouteGroupRepository(prisma_client, use_transactions=False)

    def supports_transactions(self) -> bool:
        return bool(
            self._use_transactions and self.prisma is not None and hasattr(self.prisma, "tx")
        )

    def require_transactions(self, operation: str) -> None:
        if not self.supports_transactions():
            raise RuntimeError(f"{operation} requires transaction support")

    async def list_groups(
        self, *, search: str | None = None, limit: int = 100, offset: int = 0
    ) -> tuple[list[RouteGroupRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if search:
            params.append(f"%{search}%")
            clauses.append(
                f"(group_key ILIKE ${len(params)} OR COALESCE(name, '') ILIKE ${len(params)})"
            )

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_routegroup {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        params.extend([limit, offset])
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                g.route_group_id,
                g.group_key,
                g.name,
                g.mode,
                g.routing_strategy,
                g.enabled,
                g.metadata,
                g.created_at,
                g.updated_at,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_routegroupmember m
                    WHERE m.route_group_id = g.route_group_id
                ) AS member_count
            FROM deltallm_routegroup g
            {where_sql}
            ORDER BY g.created_at DESC, g.group_key ASC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
        return [self._to_group_record(row) for row in rows], total

    async def get_group(self, group_key: str) -> RouteGroupRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT
                g.route_group_id,
                g.group_key,
                g.name,
                g.mode,
                g.routing_strategy,
                g.enabled,
                g.metadata,
                g.created_at,
                g.updated_at,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_routegroupmember m
                    WHERE m.route_group_id = g.route_group_id
                ) AS member_count
            FROM deltallm_routegroup g
            WHERE g.group_key = $1
            LIMIT 1
            """,
            group_key,
        )
        if not rows:
            return None
        return self._to_group_record(rows[0])

    async def get_default_prompt(self, group_key: str) -> dict[str, str] | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            """
            SELECT metadata
            FROM deltallm_routegroup
            WHERE group_key = $1
            LIMIT 1
            """,
            group_key,
        )
        if not rows:
            return None
        metadata = (
            _parse_json_object(rows[0].get("metadata"))
            if rows[0].get("metadata") is not None
            else None
        )
        return _extract_default_prompt(metadata)

    async def create_group(
        self,
        *,
        group_key: str,
        name: str | None,
        mode: str,
        routing_strategy: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> RouteGroupRecord:
        if self.prisma is None:
            return RouteGroupRecord(
                route_group_id="",
                group_key=group_key,
                name=name,
                mode=mode,
                routing_strategy=routing_strategy,
                enabled=enabled,
                metadata=metadata,
            )
        if self._use_transactions:
            self.require_transactions("create_group")
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).create_group(
                    group_key=group_key,
                    name=name,
                    mode=mode,
                    routing_strategy=routing_strategy,
                    enabled=enabled,
                    metadata=metadata,
                )

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_routegroup (route_group_id, group_key, name, mode, routing_strategy, enabled, metadata, created_at, updated_at)
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5, $6::jsonb, NOW(), NOW())
            RETURNING route_group_id, group_key, name, mode, routing_strategy, enabled, metadata, created_at, updated_at, 0::int AS member_count
            """,
            group_key,
            name,
            mode,
            routing_strategy,
            enabled,
            json.dumps(metadata) if metadata is not None else None,
        )
        record = self._to_group_record(rows[0])
        await self._bump_runtime_revision()
        return record

    async def update_group(
        self,
        group_key: str,
        *,
        name: str | None,
        mode: str,
        routing_strategy: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> RouteGroupRecord | None:
        if self.prisma is None:
            return None
        if self._use_transactions:
            self.require_transactions("update_group")
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).update_group(
                    group_key,
                    name=name,
                    mode=mode,
                    routing_strategy=routing_strategy,
                    enabled=enabled,
                    metadata=metadata,
                )

        group_id = await self._lock_group_id(group_key)
        if group_id is None:
            return None

        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_routegroup
            SET name = $2,
                mode = $3,
                routing_strategy = $4,
                enabled = $5,
                metadata = $6::jsonb,
                updated_at = NOW()
            WHERE group_key = $1
            RETURNING route_group_id, group_key, name, mode, routing_strategy, enabled, metadata, created_at, updated_at,
                (
                    SELECT COUNT(*)::int
                    FROM deltallm_routegroupmember m
                    WHERE m.route_group_id = deltallm_routegroup.route_group_id
                ) AS member_count
            """,
            group_key,
            name,
            mode,
            routing_strategy,
            enabled,
            json.dumps(metadata) if metadata is not None else None,
        )
        if not rows:
            return None
        await self._validate_runtime_invariants_after_group_change(
            group_id,
            group_key=group_key,
            group_mode=mode,
        )
        await self._bump_runtime_revision()
        return self._to_group_record(rows[0])

    async def delete_group(self, group_key: str) -> bool:
        if self.prisma is None:
            return False
        if self._use_transactions:
            self.require_transactions("delete_group")
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).delete_group(group_key)

        group_id = await self._lock_group_id(group_key)
        if group_id is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_routegroup
            WHERE group_key = $1
            RETURNING route_group_id
            """,
            group_key,
        )
        if not rows:
            return False
        await self._bump_runtime_revision()
        return True

    async def list_bindings(
        self,
        *,
        group_key: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[RouteGroupBindingRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if group_key:
            params.append(group_key)
            clauses.append(f"g.group_key = ${len(params)}")
        if scope_type:
            params.append(scope_type)
            clauses.append(f"b.scope_type = ${len(params)}")
        if scope_id:
            params.append(scope_id)
            clauses.append(f"b.scope_id = ${len(params)}")

        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"""
            SELECT COUNT(*)::int AS total
            FROM deltallm_routegroupbinding b
            JOIN deltallm_routegroup g ON g.route_group_id = b.route_group_id
            {where_sql}
            """,
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT
                b.route_group_binding_id,
                b.route_group_id,
                g.group_key,
                b.scope_type,
                b.scope_id,
                b.enabled,
                b.metadata,
                b.created_at,
                b.updated_at
            FROM deltallm_routegroupbinding b
            JOIN deltallm_routegroup g ON g.route_group_id = b.route_group_id
            {where_sql}
            ORDER BY b.created_at DESC, g.group_key ASC, b.scope_type ASC, b.scope_id ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_binding_record(row) for row in rows], total

    async def upsert_binding(
        self,
        group_key: str,
        *,
        scope_type: str,
        scope_id: str,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> RouteGroupBindingRecord | None:
        if self.prisma is None:
            return None

        group_id = await self._resolve_group_id(group_key)
        if group_id is None:
            return None

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_routegroupbinding (
                route_group_binding_id,
                route_group_id,
                scope_type,
                scope_id,
                enabled,
                metadata,
                created_at,
                updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5::jsonb, NOW(), NOW())
            ON CONFLICT (route_group_id, scope_type, scope_id)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING route_group_binding_id
            """,
            group_id,
            scope_type,
            scope_id,
            enabled,
            json.dumps(metadata) if metadata is not None else None,
        )
        if not rows:
            return None
        binding_id = str(rows[0].get("route_group_binding_id") or "")
        return await self.get_binding(binding_id)

    async def get_binding(self, binding_id: str) -> RouteGroupBindingRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT
                b.route_group_binding_id,
                b.route_group_id,
                g.group_key,
                b.scope_type,
                b.scope_id,
                b.enabled,
                b.metadata,
                b.created_at,
                b.updated_at
            FROM deltallm_routegroupbinding b
            JOIN deltallm_routegroup g ON g.route_group_id = b.route_group_id
            WHERE b.route_group_binding_id = $1
            LIMIT 1
            """,
            binding_id,
        )
        return self._to_binding_record(rows[0]) if rows else None

    async def delete_binding(self, binding_id: str) -> bool:
        if self.prisma is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_routegroupbinding
            WHERE route_group_binding_id = $1
            RETURNING route_group_binding_id
            """,
            binding_id,
        )
        return bool(rows)

    async def list_members(self, group_key: str) -> list[RouteGroupMemberRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            SELECT m.membership_id, m.route_group_id, m.deployment_id, m.enabled, m.weight, m.priority, m.created_at, m.updated_at
            FROM deltallm_routegroupmember m
            JOIN deltallm_routegroup g ON g.route_group_id = m.route_group_id
            WHERE g.group_key = $1
            ORDER BY m.created_at ASC, m.deployment_id ASC
            """,
            group_key,
        )
        return [self._to_member_record(row) for row in rows]

    async def upsert_member(
        self,
        group_key: str,
        *,
        deployment_id: str,
        enabled: bool,
        weight: int | None,
        priority: int | None,
    ) -> RouteGroupMemberRecord | None:
        if self.prisma is None:
            return None
        if self._use_transactions:
            self.require_transactions("upsert_member")
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).upsert_member(
                    group_key,
                    deployment_id=deployment_id,
                    enabled=enabled,
                    weight=weight,
                    priority=priority,
                )

        group_id = await self._lock_group_id(group_key)
        if group_id is None:
            return None

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_routegroupmember (
                membership_id, route_group_id, deployment_id, enabled, weight, priority, created_at, updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5, NOW(), NOW())
            ON CONFLICT (route_group_id, deployment_id)
            DO UPDATE SET
                enabled = EXCLUDED.enabled,
                weight = EXCLUDED.weight,
                priority = EXCLUDED.priority,
                updated_at = NOW()
            RETURNING membership_id, route_group_id, deployment_id, enabled, weight, priority, created_at, updated_at
            """,
            group_id,
            deployment_id,
            enabled,
            weight,
            priority,
        )
        if not rows:
            return None
        await self._validate_runtime_invariants_after_group_change(
            group_id,
            group_key=group_key,
        )
        await self._bump_runtime_revision()
        return self._to_member_record(rows[0])

    async def remove_member(self, group_key: str, deployment_id: str) -> bool:
        if self.prisma is None:
            return False
        if self._use_transactions:
            self.require_transactions("remove_member")
            async with self.prisma.tx() as tx:
                return await self.with_db(tx).remove_member(group_key, deployment_id)

        group_id = await self._lock_group_id(group_key)
        if group_id is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_routegroupmember m
            USING deltallm_routegroup g
            WHERE g.route_group_id = m.route_group_id
              AND g.group_key = $1
              AND m.deployment_id = $2
            RETURNING m.membership_id
            """,
            group_key,
            deployment_id,
        )
        if not rows:
            return False
        await self._validate_runtime_invariants_after_group_change(
            group_id,
            group_key=group_key,
        )
        await self._bump_runtime_revision()
        return True

    async def list_runtime_groups(self) -> list[dict[str, Any]]:
        return (await self.load_runtime_snapshot()).groups

    async def get_runtime_revision(self) -> int:
        return await RoutingRuntimeRevisionRepository(self.prisma).get_revision()

    async def load_runtime_snapshot(self) -> RouteGroupRuntimeSnapshot:
        if self.prisma is None:
            return RouteGroupRuntimeSnapshot(revision=0, groups=[])

        groups = await self.prisma.query_raw(
            """
            SELECT
                runtime.revision AS runtime_revision,
                runtime.route_groups_initialized,
                g.route_group_id,
                g.group_key,
                g.mode,
                g.enabled,
                g.routing_strategy,
                g.metadata,
                p.version AS policy_version,
                p.semantics_version AS policy_semantics_version,
                p.policy_json,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            jsonb_build_object(
                                'deployment_id', m.deployment_id,
                                'enabled', m.enabled,
                                'weight', m.weight,
                                'priority', m.priority
                            ) ORDER BY m.created_at ASC, m.deployment_id ASC
                        )
                        FROM deltallm_routegroupmember m
                        WHERE m.route_group_id = g.route_group_id
                    ),
                    '[]'::jsonb
                ) AS members
            FROM deltallm_routeruntimestate runtime
            LEFT JOIN deltallm_routegroup g ON TRUE
            LEFT JOIN LATERAL (
                SELECT policy_json, version, semantics_version
                FROM deltallm_routepolicy
                WHERE route_group_id = g.route_group_id
                  AND status = 'published'
                ORDER BY version DESC
                LIMIT 1
            ) p ON TRUE
            WHERE runtime.state_key = $1
            ORDER BY g.group_key ASC
            """,
            ROUTING_RUNTIME_STATE_KEY,
        )
        if not groups:
            return RouteGroupRuntimeSnapshot(revision=0, groups=[])

        revision = int(groups[0].get("runtime_revision") or 0)
        database_initialized = bool(groups[0].get("route_groups_initialized", False))
        runtime_groups: list[dict[str, Any]] = []
        for row in groups:
            group_id = str(row.get("route_group_id") or "")
            if not group_id:
                continue
            policy_json = parse_policy_json(row.get("policy_json"))
            metadata = (
                _parse_json_object(row.get("metadata")) if row.get("metadata") is not None else None
            )
            strategy = row.get("routing_strategy")
            if isinstance(policy_json.get("strategy"), str):
                strategy = policy_json["strategy"]
            timeouts = policy_json.get("timeouts")
            retry = policy_json.get("retry")
            raw_members = row.get("members")
            if isinstance(raw_members, str):
                try:
                    raw_members = json.loads(raw_members)
                except json.JSONDecodeError:
                    raw_members = []
            base_members = [
                {
                    "deployment_id": str(member.get("deployment_id") or ""),
                    "enabled": bool(member.get("enabled", True)),
                    "weight": member.get("weight"),
                    "priority": member.get("priority"),
                }
                for member in (raw_members if isinstance(raw_members, list) else [])
                if isinstance(member, dict) and str(member.get("deployment_id") or "")
            ]
            semantics_version = int(row.get("policy_semantics_version") or 1)
            merged_members = merge_policy_members(
                base_members,
                policy_json.get("members"),
                semantics_version=semantics_version,
            )

            runtime_groups.append(
                {
                    "key": str(row.get("group_key") or ""),
                    "mode": str(row.get("mode") or "chat"),
                    "enabled": bool(row.get("enabled", True)),
                    "strategy": strategy if isinstance(strategy, str) and strategy else None,
                    "policy_version": int(row["policy_version"])
                    if row.get("policy_version") is not None
                    else None,
                    "policy_semantics_version": semantics_version
                    if row.get("policy_version") is not None
                    else None,
                    "timeouts": timeouts if isinstance(timeouts, dict) else None,
                    "retry": retry if isinstance(retry, dict) else None,
                    "default_prompt": _extract_default_prompt(metadata),
                    "access_groups": metadata.get("access_groups")
                    if isinstance(metadata, dict)
                    else None,
                    "members": merged_members,
                }
            )

        return RouteGroupRuntimeSnapshot(
            revision=revision,
            groups=runtime_groups,
            database_initialized=database_initialized,
        )

    async def _bump_runtime_revision(self) -> int:
        return await RoutingRuntimeRevisionRepository(self.prisma).bump_revision(
            route_groups_initialized=True
        )

    async def _validate_group_member_modes(
        self,
        group_id: str,
        *,
        group_key: str,
        group_mode: str | None = None,
    ) -> None:
        rows = await self.prisma.query_raw(
            """
            SELECT
                g.mode AS group_mode,
                m.deployment_id,
                d.model_info
            FROM deltallm_routegroup g
            LEFT JOIN deltallm_routegroupmember m ON m.route_group_id = g.route_group_id
            LEFT JOIN deltallm_modeldeployment d ON d.deployment_id = m.deployment_id
            WHERE g.route_group_id = $1
              AND (m.enabled = TRUE OR m.membership_id IS NULL)
            ORDER BY m.created_at ASC, m.deployment_id ASC
            """,
            group_id,
        )
        if not rows:
            raise ValueError("route group no longer exists")
        deployment_entries = [
            {
                "deployment_id": row.get("deployment_id"),
                "model_info": row.get("model_info"),
            }
            for row in rows
            if row.get("deployment_id") is not None
        ]
        validate_route_group_member_modes(
            group_key=group_key,
            group_mode=group_mode if group_mode is not None else rows[0].get("group_mode"),
            member_ids=[str(row["deployment_id"]) for row in deployment_entries],
            deployment_modes=deployment_modes_by_id(deployment_entries),
        )

    async def _validate_runtime_invariants_after_group_change(
        self,
        group_id: str,
        *,
        group_key: str,
        group_mode: str | None = None,
    ) -> None:
        try:
            await self._validate_group_member_modes(
                group_id,
                group_key=group_key,
                group_mode=group_mode,
            )
            await self._validate_published_policy_after_group_change(group_id)
        except RoutePolicyStateConflictError:
            raise
        except ValueError as exc:
            raise RoutePolicyStateConflictError(
                f"route-group change is incompatible with current deployments: {exc}"
            ) from exc

    async def _resolve_group_id(self, group_key: str) -> str | None:
        rows = await self.prisma.query_raw(
            """
            SELECT route_group_id
            FROM deltallm_routegroup
            WHERE group_key = $1
            LIMIT 1
            """,
            group_key,
        )
        if not rows:
            return None
        return str(rows[0].get("route_group_id") or "")

    @staticmethod
    def _to_group_record(row: dict[str, Any]) -> RouteGroupRecord:
        metadata = _parse_json_object(row.get("metadata")) or None
        owner_scope = owner_scope_from_metadata(metadata)
        return RouteGroupRecord(
            route_group_id=str(row.get("route_group_id") or ""),
            group_key=str(row.get("group_key") or ""),
            name=row.get("name"),
            mode=str(row.get("mode") or "chat"),
            routing_strategy=row.get("routing_strategy"),
            enabled=bool(row.get("enabled", True)),
            member_count=int(row.get("member_count") or 0),
            metadata=metadata,
            default_prompt=_extract_default_prompt(metadata),
            owner_scope_type=owner_scope.scope_type,
            owner_scope_id=owner_scope.scope_id,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _to_member_record(row: dict[str, Any]) -> RouteGroupMemberRecord:
        return RouteGroupMemberRecord(
            membership_id=str(row.get("membership_id") or ""),
            route_group_id=str(row.get("route_group_id") or ""),
            deployment_id=str(row.get("deployment_id") or ""),
            enabled=bool(row.get("enabled", True)),
            weight=int(row["weight"]) if row.get("weight") is not None else None,
            priority=int(row["priority"]) if row.get("priority") is not None else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _to_binding_record(row: dict[str, Any]) -> RouteGroupBindingRecord:
        return RouteGroupBindingRecord(
            route_group_binding_id=str(row.get("route_group_binding_id") or ""),
            route_group_id=str(row.get("route_group_id") or ""),
            group_key=str(row.get("group_key") or ""),
            scope_type=str(row.get("scope_type") or ""),
            scope_id=str(row.get("scope_id") or ""),
            enabled=bool(row.get("enabled", True)),
            metadata=_parse_json_object(row.get("metadata"))
            if row.get("metadata") is not None
            else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
