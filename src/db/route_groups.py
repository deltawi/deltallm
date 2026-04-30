from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.services.asset_ownership import owner_scope_from_metadata


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


def _merge_policy_members(
    base_members: list[dict[str, Any]],
    policy_members: Any,
) -> list[dict[str, Any]]:
    if not isinstance(policy_members, list) or not policy_members:
        return base_members

    by_id: dict[str, dict[str, Any]] = {
        str(member.get("deployment_id") or ""): dict(member)
        for member in base_members
        if isinstance(member, dict) and str(member.get("deployment_id") or "")
    }
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in policy_members:
        if not isinstance(item, dict):
            continue
        deployment_id = str(item.get("deployment_id") or "").strip()
        if not deployment_id:
            continue
        base = by_id.get(deployment_id)
        if base is None:
            continue
        merged = dict(base)
        if "enabled" in item:
            merged["enabled"] = bool(item.get("enabled", True))
        if item.get("weight") is not None:
            try:
                merged["weight"] = int(item["weight"])
            except (TypeError, ValueError):
                pass
        if item.get("priority") is not None:
            try:
                merged["priority"] = int(item["priority"])
            except (TypeError, ValueError):
                pass
        ordered.append(merged)
        seen.add(deployment_id)

    for member in base_members:
        deployment_id = str(member.get("deployment_id") or "")
        if deployment_id in seen:
            continue
        ordered.append(member)

    return ordered


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
class RoutePolicyRecord:
    route_policy_id: str
    route_group_id: str
    version: int
    status: str
    policy_json: dict[str, Any]
    published_at: datetime | None = None
    published_by: str | None = None
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


class RouteGroupRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_groups(self, *, search: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[RouteGroupRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if search:
            params.append(f"%{search}%")
            clauses.append(f"(group_key ILIKE ${len(params)} OR COALESCE(name, '') ILIKE ${len(params)})")

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
        metadata = _parse_json_object(rows[0].get("metadata")) if rows[0].get("metadata") is not None else None
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
        return self._to_group_record(rows[0])

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
        return self._to_group_record(rows[0])

    async def delete_group(self, group_key: str) -> bool:
        if self.prisma is None:
            return False

        rows = await self.prisma.query_raw(
            """
            DELETE FROM deltallm_routegroup
            WHERE group_key = $1
            RETURNING route_group_id
            """,
            group_key,
        )
        return bool(rows)

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

        group_id = await self._resolve_group_id(group_key)
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
        return self._to_member_record(rows[0])

    async def remove_member(self, group_key: str, deployment_id: str) -> bool:
        if self.prisma is None:
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
        return bool(rows)

    async def get_published_policy(self, group_key: str) -> RoutePolicyRecord | None:
        if self.prisma is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT p.route_policy_id, p.route_group_id, p.version, p.status, p.policy_json, p.published_at, p.published_by, p.created_at, p.updated_at
            FROM deltallm_routepolicy p
            JOIN deltallm_routegroup g ON g.route_group_id = p.route_group_id
            WHERE g.group_key = $1
              AND p.status = 'published'
            ORDER BY p.version DESC
            LIMIT 1
            """,
            group_key,
        )
        if not rows:
            return None
        return self._to_policy_record(rows[0])

    async def publish_policy(self, group_key: str, policy_json: dict[str, Any], *, published_by: str | None = None) -> RoutePolicyRecord | None:
        if self.prisma is None:
            return None

        group_id = await self._resolve_group_id(group_key)
        if group_id is None:
            return None

        version_rows = await self.prisma.query_raw(
            """
            SELECT COALESCE(MAX(version), 0)::int AS max_version
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
            """,
            group_id,
        )
        next_version = int((version_rows[0] if version_rows else {}).get("max_version") or 0) + 1

        await self.prisma.execute_raw(
            """
            UPDATE deltallm_routepolicy
            SET status = 'archived', updated_at = NOW()
            WHERE route_group_id = $1
              AND status = 'published'
            """,
            group_id,
        )

        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_routepolicy (
                route_policy_id, route_group_id, version, status, policy_json, published_at, published_by, created_at, updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, 'published', $3::jsonb, NOW(), $4, NOW(), NOW())
            RETURNING route_policy_id, route_group_id, version, status, policy_json, published_at, published_by, created_at, updated_at
            """,
            group_id,
            next_version,
            json.dumps(policy_json),
            published_by,
        )
        if not rows:
            return None
        return self._to_policy_record(rows[0])

    async def save_draft_policy(self, group_key: str, policy_json: dict[str, Any]) -> RoutePolicyRecord | None:
        if self.prisma is None:
            return None

        group_id = await self._resolve_group_id(group_key)
        if group_id is None:
            return None

        draft_rows = await self.prisma.query_raw(
            """
            SELECT route_policy_id
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
              AND status = 'draft'
            ORDER BY version DESC
            LIMIT 1
            """,
            group_id,
        )
        if draft_rows:
            draft_id = str(draft_rows[0].get("route_policy_id") or "")
            rows = await self.prisma.query_raw(
                """
                UPDATE deltallm_routepolicy
                SET policy_json = $2::jsonb,
                    updated_at = NOW()
                WHERE route_policy_id = $1
                RETURNING route_policy_id, route_group_id, version, status, policy_json, published_at, published_by, created_at, updated_at
                """,
                draft_id,
                json.dumps(policy_json),
            )
            return self._to_policy_record(rows[0]) if rows else None

        version_rows = await self.prisma.query_raw(
            """
            SELECT COALESCE(MAX(version), 0)::int AS max_version
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
            """,
            group_id,
        )
        next_version = int((version_rows[0] if version_rows else {}).get("max_version") or 0) + 1
        rows = await self.prisma.query_raw(
            """
            INSERT INTO deltallm_routepolicy (
                route_policy_id, route_group_id, version, status, policy_json, created_at, updated_at
            )
            VALUES (gen_random_uuid()::text, $1, $2, 'draft', $3::jsonb, NOW(), NOW())
            RETURNING route_policy_id, route_group_id, version, status, policy_json, published_at, published_by, created_at, updated_at
            """,
            group_id,
            next_version,
            json.dumps(policy_json),
        )
        return self._to_policy_record(rows[0]) if rows else None

    async def publish_latest_draft(self, group_key: str, *, published_by: str | None = None) -> RoutePolicyRecord | None:
        if self.prisma is None:
            return None

        group_id = await self._resolve_group_id(group_key)
        if group_id is None:
            return None

        rows = await self.prisma.query_raw(
            """
            SELECT route_policy_id
            FROM deltallm_routepolicy
            WHERE route_group_id = $1
              AND status = 'draft'
            ORDER BY version DESC
            LIMIT 1
            """,
            group_id,
        )
        if not rows:
            return None
        draft_id = str(rows[0].get("route_policy_id") or "")

        await self.prisma.execute_raw(
            """
            UPDATE deltallm_routepolicy
            SET status = 'archived', updated_at = NOW()
            WHERE route_group_id = $1
              AND status = 'published'
            """,
            group_id,
        )
        updated_rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_routepolicy
            SET status = 'published',
                published_at = NOW(),
                published_by = $2,
                updated_at = NOW()
            WHERE route_policy_id = $1
            RETURNING route_policy_id, route_group_id, version, status, policy_json, published_at, published_by, created_at, updated_at
            """,
            draft_id,
            published_by,
        )
        return self._to_policy_record(updated_rows[0]) if updated_rows else None

    async def rollback_policy(
        self,
        group_key: str,
        *,
        target_version: int,
        published_by: str | None = None,
    ) -> RoutePolicyRecord | None:
        if self.prisma is None:
            return None

        source_rows = await self.prisma.query_raw(
            """
            SELECT p.policy_json
            FROM deltallm_routepolicy p
            JOIN deltallm_routegroup g ON g.route_group_id = p.route_group_id
            WHERE g.group_key = $1
              AND p.version = $2
            LIMIT 1
            """,
            group_key,
            target_version,
        )
        if not source_rows:
            return None
        policy_json = _parse_json_object(source_rows[0].get("policy_json"))
        if not policy_json:
            return None
        return await self.publish_policy(group_key, policy_json, published_by=published_by)

    async def list_policies(self, group_key: str) -> list[RoutePolicyRecord]:
        if self.prisma is None:
            return []

        rows = await self.prisma.query_raw(
            """
            SELECT p.route_policy_id, p.route_group_id, p.version, p.status, p.policy_json, p.published_at, p.published_by, p.created_at, p.updated_at
            FROM deltallm_routepolicy p
            JOIN deltallm_routegroup g ON g.route_group_id = p.route_group_id
            WHERE g.group_key = $1
            ORDER BY p.version DESC
            """,
            group_key,
        )
        return [self._to_policy_record(row) for row in rows]

    async def list_runtime_groups(self) -> list[dict[str, Any]]:
        if self.prisma is None:
            return []

        groups = await self.prisma.query_raw(
            """
            SELECT
                g.route_group_id,
                g.group_key,
                g.mode,
                g.enabled,
                g.routing_strategy,
                g.metadata,
                p.version AS policy_version,
                p.policy_json
            FROM deltallm_routegroup g
            LEFT JOIN LATERAL (
                SELECT policy_json, version
                FROM deltallm_routepolicy
                WHERE route_group_id = g.route_group_id
                  AND status = 'published'
                ORDER BY version DESC
                LIMIT 1
            ) p ON TRUE
            ORDER BY g.group_key ASC
            """
        )
        if not groups:
            return []

        members = await self.prisma.query_raw(
            """
            SELECT route_group_id, deployment_id, enabled, weight, priority
            FROM deltallm_routegroupmember
            ORDER BY created_at ASC, deployment_id ASC
            """
        )

        member_map: dict[str, list[dict[str, Any]]] = {}
        for row in members:
            group_id = str(row.get("route_group_id") or "")
            if not group_id:
                continue
            member_map.setdefault(group_id, []).append(
                {
                    "deployment_id": str(row.get("deployment_id") or ""),
                    "enabled": bool(row.get("enabled", True)),
                    "weight": row.get("weight"),
                    "priority": row.get("priority"),
                }
            )

        runtime_groups: list[dict[str, Any]] = []
        for row in groups:
            group_id = str(row.get("route_group_id") or "")
            policy_json = _parse_json_object(row.get("policy_json"))
            metadata = _parse_json_object(row.get("metadata")) if row.get("metadata") is not None else None
            strategy = row.get("routing_strategy")
            if isinstance(policy_json.get("strategy"), str):
                strategy = policy_json["strategy"]
            timeouts = policy_json.get("timeouts")
            retry = policy_json.get("retry")
            merged_members = _merge_policy_members(
                [m for m in member_map.get(group_id, []) if m.get("deployment_id")],
                policy_json.get("members"),
            )

            runtime_groups.append(
                {
                    "key": str(row.get("group_key") or ""),
                    "mode": str(row.get("mode") or "chat"),
                    "enabled": bool(row.get("enabled", True)),
                    "strategy": strategy if isinstance(strategy, str) and strategy else None,
                    "policy_version": int(row["policy_version"]) if row.get("policy_version") is not None else None,
                    "timeouts": timeouts if isinstance(timeouts, dict) else None,
                    "retry": retry if isinstance(retry, dict) else None,
                    "default_prompt": _extract_default_prompt(
                        metadata
                    ),
                    "access_groups": metadata.get("access_groups") if isinstance(metadata, dict) else None,
                    "members": merged_members,
                }
            )

        return runtime_groups

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
    def _to_policy_record(row: dict[str, Any]) -> RoutePolicyRecord:
        return RoutePolicyRecord(
            route_policy_id=str(row.get("route_policy_id") or ""),
            route_group_id=str(row.get("route_group_id") or ""),
            version=int(row.get("version") or 0),
            status=str(row.get("status") or "draft"),
            policy_json=_parse_json_object(row.get("policy_json")),
            published_at=_parse_datetime(row.get("published_at")),
            published_by=row.get("published_by"),
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
            metadata=_parse_json_object(row.get("metadata")) if row.get("metadata") is not None else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
        )
