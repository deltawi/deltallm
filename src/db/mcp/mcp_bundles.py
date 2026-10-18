"""Persistence for MCP bundles and their member servers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.db.mcp.mcp import _parse_datetime, _parse_json_object, _parse_text_list

if TYPE_CHECKING:
    from src.mcp.bundles import MCPBundle, MCPBundleMember

_BUNDLE_COLUMNS = """
    b.mcp_bundle_id, b.bundle_key, b.name, b.description, b.owner_scope_type,
    b.owner_scope_id, b.enabled, b.metadata, b.created_by_account_id,
    b.created_at, b.updated_at
"""

# One row per bundle. Members come as one ordered JSON array, with a summary of
# their server, so a page of bundles is one query.
_MEMBERS_JSON = """
    COALESCE((
        SELECT jsonb_agg(
            jsonb_build_object(
                'mcp_server_id', m.mcp_server_id,
                'position', m.position,
                'all_tools', m.all_tools,
                'tool_allowlist', to_jsonb(COALESCE(m.tool_allowlist, ARRAY[]::text[])),
                'name_prefix', m.name_prefix,
                'server_key', s.server_key,
                'server_name', s.name,
                'server_owner_scope_type', s.owner_scope_type,
                'server_owner_scope_id', s.owner_scope_id,
                'server_enabled', s.enabled,
                'server_tool_names', COALESCE((
                    SELECT jsonb_agg(tool->>'name')
                    FROM jsonb_array_elements(
                        CASE
                            WHEN jsonb_typeof(s.capabilities_json->'tools') = 'array'
                            THEN s.capabilities_json->'tools'
                            ELSE '[]'::jsonb
                        END
                    ) AS tool
                    WHERE COALESCE(tool->>'name', '') <> ''
                ), '[]'::jsonb)
            )
            ORDER BY m.position ASC, m.created_at ASC
        )
        FROM deltallm_mcpbundlemember m
        JOIN deltallm_mcpserver s ON s.mcp_server_id = m.mcp_server_id
        WHERE m.mcp_bundle_id = b.mcp_bundle_id
    ), '[]'::jsonb) AS members
"""

_INSERT_MEMBERS_SQL = """
    INSERT INTO deltallm_mcpbundlemember (
        mcp_bundle_member_id, mcp_bundle_id, mcp_server_id, position, all_tools,
        tool_allowlist, name_prefix, created_at, updated_at
    )
    SELECT
        gen_random_uuid()::text, $1, m.mcp_server_id, m.position, m.all_tools,
        COALESCE(m.tool_allowlist, ARRAY[]::text[]), m.name_prefix, NOW(), NOW()
    FROM jsonb_to_recordset($2::jsonb) AS m(
        mcp_server_id text, position int, all_tools boolean, tool_allowlist text[], name_prefix text
    )
"""


@dataclass
class MCPBundleMemberRecord:
    mcp_server_id: str
    position: int = 0
    all_tools: bool = False
    tool_allowlist: list[str] = field(default_factory=list)
    name_prefix: str | None = None
    server_key: str = ""
    server_name: str = ""
    server_owner_scope_type: str = "global"
    server_owner_scope_id: str | None = None
    server_enabled: bool = True
    server_tool_names: list[str] = field(default_factory=list)

    def to_member(self) -> MCPBundleMember:
        # Import here: src.mcp imports the gateway, and src.db must import alone.
        from src.mcp.bundles import MCPBundleMember

        return MCPBundleMember(
            server_id=self.mcp_server_id,
            tool_names=None if self.all_tools else tuple(self.tool_allowlist),
            name_prefix=self.name_prefix,
        )


@dataclass
class MCPBundleRecord:
    mcp_bundle_id: str
    bundle_key: str
    name: str
    description: str | None = None
    owner_scope_type: str = "global"
    owner_scope_id: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] | None = None
    created_by_account_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    members: list[MCPBundleMemberRecord] = field(default_factory=list)

    def to_bundle(self) -> MCPBundle:
        from src.mcp.bundles import MCPBundle

        return MCPBundle(
            bundle_id=self.mcp_bundle_id,
            bundle_key=self.bundle_key,
            name=self.name,
            description=self.description,
            owner_scope_type=self.owner_scope_type,
            owner_scope_id=self.owner_scope_id,
            enabled=self.enabled,
            members=tuple(member.to_member() for member in self.members),
        )


@dataclass(frozen=True, slots=True)
class MCPBundleOwnerFilter:
    """Owner scopes an admin can read. ``None`` on the repository call means all."""

    organization_ids: tuple[str, ...] = ()
    team_ids: tuple[str, ...] = ()


def members_payload(members: tuple[MCPBundleMember, ...]) -> str:
    return json.dumps(
        [
            {
                "mcp_server_id": member.server_id,
                "position": position,
                "all_tools": member.tool_names is None,
                "tool_allowlist": list(member.tool_names or ()),
                "name_prefix": member.name_prefix,
            }
            for position, member in enumerate(members)
        ]
    )


class MCPBundleRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    async def list_bundles(
        self,
        *,
        search: str | None = None,
        owner_filter: MCPBundleOwnerFilter | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MCPBundleRecord], int]:
        if self.prisma is None:
            return [], 0
        clauses: list[str] = []
        params: list[Any] = []
        if search:
            params.append(f"%{search.strip()}%")
            placeholder = f"${len(params)}"
            clauses.append(
                f"(b.bundle_key ILIKE {placeholder} OR b.name ILIKE {placeholder} "
                f"OR COALESCE(b.description, '') ILIKE {placeholder})"
            )
        if owner_filter is not None:
            clauses.append(_owner_filter_clause(owner_filter, params))
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_mcpbundle b {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)
        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT {_BUNDLE_COLUMNS}, {_MEMBERS_JSON}
            FROM deltallm_mcpbundle b
            {where_sql}
            ORDER BY b.bundle_key ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [self._to_record(row) for row in rows], total

    async def get_bundle(self, bundle_id: str) -> MCPBundleRecord | None:
        return await self._get_one("b.mcp_bundle_id = $1", bundle_id)

    async def get_bundle_by_key(self, bundle_key: str) -> MCPBundleRecord | None:
        return await self._get_one("b.bundle_key = $1", bundle_key)

    async def create_bundle(
        self,
        *,
        bundle_key: str,
        name: str,
        description: str | None,
        owner_scope_type: str,
        owner_scope_id: str | None,
        enabled: bool,
        members: tuple[MCPBundleMember, ...],
        metadata: dict[str, Any] | None,
        created_by_account_id: str | None,
    ) -> MCPBundleRecord | None:
        if self.prisma is None:
            return None
        async with self._transaction() as db:
            rows = await db.query_raw(
                """
                INSERT INTO deltallm_mcpbundle (
                    mcp_bundle_id, bundle_key, name, description, owner_scope_type, owner_scope_id,
                    enabled, metadata, created_by_account_id, created_at, updated_at
                )
                VALUES (gen_random_uuid()::text, $1, $2, $3, $4, $5, $6, $7::jsonb, $8, NOW(), NOW())
                RETURNING mcp_bundle_id
                """,
                bundle_key,
                name,
                description,
                owner_scope_type,
                owner_scope_id,
                enabled,
                json.dumps(metadata) if metadata is not None else None,
                created_by_account_id,
            )
            bundle_id = str(rows[0]["mcp_bundle_id"])
            if members:
                await db.execute_raw(_INSERT_MEMBERS_SQL, bundle_id, members_payload(members))
        return await self.get_bundle(bundle_id)

    async def update_bundle(
        self,
        bundle_id: str,
        *,
        name: str,
        description: str | None,
        enabled: bool,
        members: tuple[MCPBundleMember, ...],
        metadata: dict[str, Any] | None,
    ) -> MCPBundleRecord | None:
        if self.prisma is None:
            return None
        async with self._transaction() as db:
            rows = await db.query_raw(
                """
                UPDATE deltallm_mcpbundle
                SET name = $2, description = $3, enabled = $4, metadata = $5::jsonb, updated_at = NOW()
                WHERE mcp_bundle_id = $1
                RETURNING mcp_bundle_id
                """,
                bundle_id,
                name,
                description,
                enabled,
                json.dumps(metadata) if metadata is not None else None,
            )
            if not rows:
                return None
            await db.execute_raw(
                "DELETE FROM deltallm_mcpbundlemember WHERE mcp_bundle_id = $1", bundle_id
            )
            if members:
                await db.execute_raw(_INSERT_MEMBERS_SQL, bundle_id, members_payload(members))
        return await self.get_bundle(bundle_id)

    async def delete_bundle(self, bundle_id: str) -> bool:
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            "DELETE FROM deltallm_mcpbundle WHERE mcp_bundle_id = $1 RETURNING mcp_bundle_id",
            bundle_id,
        )
        return bool(rows)

    async def _get_one(self, predicate: str, value: str) -> MCPBundleRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            SELECT {_BUNDLE_COLUMNS}, {_MEMBERS_JSON}
            FROM deltallm_mcpbundle b
            WHERE {predicate}
            LIMIT 1
            """,
            value,
        )
        return self._to_record(rows[0]) if rows else None

    def _transaction(self) -> Any:
        tx = getattr(self.prisma, "tx", None)
        if callable(tx):
            return tx()
        return _NoTransaction(self.prisma)

    @staticmethod
    def _to_record(row: dict[str, Any]) -> MCPBundleRecord:
        raw_members = row.get("members")
        if isinstance(raw_members, str):
            try:
                raw_members = json.loads(raw_members)
            except (TypeError, ValueError):
                raw_members = []
        members = [
            MCPBundleMemberRecord(
                mcp_server_id=str(item.get("mcp_server_id") or ""),
                position=int(item.get("position") or 0),
                all_tools=bool(item.get("all_tools")),
                tool_allowlist=_parse_text_list(item.get("tool_allowlist")),
                name_prefix=str(item["name_prefix"]) if item.get("name_prefix") else None,
                server_key=str(item.get("server_key") or ""),
                server_name=str(item.get("server_name") or ""),
                server_owner_scope_type=str(item.get("server_owner_scope_type") or "global"),
                server_owner_scope_id=str(item["server_owner_scope_id"])
                if item.get("server_owner_scope_id")
                else None,
                server_enabled=bool(item.get("server_enabled", True)),
                server_tool_names=_parse_text_list(item.get("server_tool_names")),
            )
            for item in (raw_members if isinstance(raw_members, list) else [])
            if isinstance(item, dict)
        ]
        return MCPBundleRecord(
            mcp_bundle_id=str(row.get("mcp_bundle_id") or ""),
            bundle_key=str(row.get("bundle_key") or ""),
            name=str(row.get("name") or ""),
            description=str(row["description"]) if row.get("description") is not None else None,
            owner_scope_type=str(row.get("owner_scope_type") or "global"),
            owner_scope_id=str(row["owner_scope_id"]) if row.get("owner_scope_id") else None,
            enabled=bool(row.get("enabled", True)),
            metadata=_parse_json_object(row.get("metadata")) or None,
            created_by_account_id=str(row["created_by_account_id"])
            if row.get("created_by_account_id")
            else None,
            created_at=_parse_datetime(row.get("created_at")),
            updated_at=_parse_datetime(row.get("updated_at")),
            members=members,
        )


def _owner_filter_clause(owner_filter: MCPBundleOwnerFilter, params: list[Any]) -> str:
    clauses: list[str] = []
    if owner_filter.organization_ids:
        params.append(list(owner_filter.organization_ids))
        org_param = f"${len(params)}::text[]"
        clauses.append(
            f"(b.owner_scope_type = 'organization' AND b.owner_scope_id = ANY({org_param}))"
        )
        clauses.append(
            f"""(b.owner_scope_type = 'team' AND EXISTS (
                SELECT 1 FROM deltallm_teamtable t
                WHERE t.team_id = b.owner_scope_id AND t.organization_id = ANY({org_param})
            ))"""
        )
    if owner_filter.team_ids:
        params.append(list(owner_filter.team_ids))
        clauses.append(
            f"(b.owner_scope_type = 'team' AND b.owner_scope_id = ANY(${len(params)}::text[]))"
        )
    return f"({' OR '.join(clauses)})" if clauses else "FALSE"


class _NoTransaction:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *exc: object) -> None:
        return None


__all__ = [
    "MCPBundleMemberRecord",
    "MCPBundleOwnerFilter",
    "MCPBundleRecord",
    "MCPBundleRepository",
]
