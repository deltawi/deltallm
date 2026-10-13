"""Persistence for MCP tool-call approval requests.

Lifecycle of one row:

- ``pending``: created by a ``tools/call`` that needs manual approval.
- ``approved`` or ``rejected``: decided by an operator before ``expires_at``.
- ``expired``: nobody decided before ``expires_at``.

An approved row allows exactly one execution. The gateway sets
``consumed_at`` in the same statement that checks the row is still usable,
so two concurrent calls cannot both use one approval.

Expiry is lazy. Reads report a pending row past its ``expires_at``, and an
approval that nobody used before its window ended, as ``expired`` so lists
and counts do not show dead requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.db.mcp.mcp_scope_policies import _parse_datetime, _parse_json_object

APPROVAL_STATUS_FILTERS: frozenset[str] = frozenset({"pending", "approved", "rejected", "expired"})

# Status as callers see it. A pending row past its expiry, and an approval
# that nobody used before its window ended, read as expired.
APPROVAL_EFFECTIVE_STATUS_SQL = (
    "CASE WHEN r.status IN ('pending', 'approved') AND r.consumed_at IS NULL "
    "AND r.expires_at <= NOW() THEN 'expired' ELSE r.status END"
)

APPROVAL_SELECT_COLUMNS = f"""
    r.mcp_approval_request_id,
    r.mcp_server_id,
    r.tool_name,
    r.scope_type,
    r.scope_id,
    {APPROVAL_EFFECTIVE_STATUS_SQL} AS status,
    r.request_fingerprint,
    r.requested_by_api_key,
    r.requested_by_user,
    r.organization_id,
    r.request_id,
    r.correlation_id,
    r.arguments_json,
    r.decision_comment,
    r.decided_by_account_id,
    r.decided_at,
    r.expires_at,
    r.consumed_at,
    r.metadata,
    r.created_at,
    r.updated_at
"""

_RETURNING_COLUMNS = """
    mcp_approval_request_id, mcp_server_id, tool_name, scope_type, scope_id, status,
    request_fingerprint, requested_by_api_key, requested_by_user, organization_id,
    request_id, correlation_id, arguments_json, decision_comment, decided_by_account_id,
    decided_at, expires_at, consumed_at, metadata, created_at, updated_at
"""


def approval_status_clause(status: str, params: list[Any]) -> str:
    """Return a filter on the effective status. Appends bind values to ``params``."""
    still_open = "(r.consumed_at IS NOT NULL OR r.expires_at IS NULL OR r.expires_at > NOW())"
    if status == "pending":
        return f"(r.status = 'pending' AND {still_open})"
    if status == "approved":
        return f"(r.status = 'approved' AND {still_open})"
    if status == "expired":
        return (
            "(r.status = 'expired' OR (r.status IN ('pending', 'approved') "
            "AND r.consumed_at IS NULL AND r.expires_at <= NOW()))"
        )
    params.append(status)
    return f"r.status = ${len(params)}"


@dataclass
class MCPApprovalRequestRecord:
    mcp_approval_request_id: str
    mcp_server_id: str
    tool_name: str
    scope_type: str
    scope_id: str
    status: str = "pending"
    request_fingerprint: str = ""
    requested_by_api_key: str | None = None
    requested_by_user: str | None = None
    organization_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    arguments_json: dict[str, Any] | None = None
    decision_comment: str | None = None
    decided_by_account_id: str | None = None
    decided_at: datetime | None = None
    expires_at: datetime | None = None
    consumed_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def requested_by_account_id(self) -> str | None:
        """Platform account that owns the calling key, when the gateway recorded it."""
        value = (self.metadata or {}).get("requested_by_account_id")
        return str(value) if value else None


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def to_approval_request_record(row: dict[str, Any]) -> MCPApprovalRequestRecord:
    return MCPApprovalRequestRecord(
        mcp_approval_request_id=str(row.get("mcp_approval_request_id") or ""),
        mcp_server_id=str(row.get("mcp_server_id") or ""),
        tool_name=str(row.get("tool_name") or ""),
        scope_type=str(row.get("scope_type") or ""),
        scope_id=str(row.get("scope_id") or ""),
        status=str(row.get("status") or "pending"),
        request_fingerprint=str(row.get("request_fingerprint") or ""),
        requested_by_api_key=_optional_text(row.get("requested_by_api_key")),
        requested_by_user=_optional_text(row.get("requested_by_user")),
        organization_id=_optional_text(row.get("organization_id")),
        request_id=_optional_text(row.get("request_id")),
        correlation_id=_optional_text(row.get("correlation_id")),
        arguments_json=_parse_json_object(row.get("arguments_json")) or None,
        decision_comment=_optional_text(row.get("decision_comment")),
        decided_by_account_id=_optional_text(row.get("decided_by_account_id")),
        decided_at=_parse_datetime(row.get("decided_at")),
        expires_at=_parse_datetime(row.get("expires_at")),
        consumed_at=_parse_datetime(row.get("consumed_at")),
        metadata=_parse_json_object(row.get("metadata")) or None,
        created_at=_parse_datetime(row.get("created_at")),
        updated_at=_parse_datetime(row.get("updated_at")),
    )


class MCPApprovalRepositoryMixin:
    """Approval-request queries for ``MCPRepository``."""

    prisma: Any | None

    async def expire_stale_approval_requests(self, *, request_fingerprint: str) -> int:
        """Mark pending rows for this call as expired once nobody decided in time.

        Decided rows keep their decision. Their ``expires_at`` only ends the
        window in which they apply to a new call.
        """
        if self.prisma is None:
            return 0
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpapprovalrequest
            SET status = 'expired',
                updated_at = NOW()
            WHERE request_fingerprint = $1
              AND status = 'pending'
              AND expires_at IS NOT NULL
              AND expires_at <= NOW()
            RETURNING mcp_approval_request_id
            """,
            request_fingerprint,
        )
        return len(rows)

    async def find_active_approval_request(
        self, *, request_fingerprint: str
    ) -> MCPApprovalRequestRecord | None:
        """Return the newest row that still applies to a new call with this fingerprint."""
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            SELECT {APPROVAL_SELECT_COLUMNS}
            FROM deltallm_mcpapprovalrequest r
            WHERE r.request_fingerprint = $1
              AND r.status IN ('pending', 'approved', 'rejected')
              AND r.consumed_at IS NULL
              AND (r.expires_at IS NULL OR r.expires_at > NOW())
            ORDER BY COALESCE(r.decided_at, r.created_at) DESC, r.updated_at DESC
            LIMIT 1
            """,
            request_fingerprint,
        )
        return to_approval_request_record(rows[0]) if rows else None

    async def consume_approval_request(
        self, approval_request_id: str
    ) -> MCPApprovalRequestRecord | None:
        """Use an approval for one execution. Returns ``None`` if it is already used or expired."""
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_mcpapprovalrequest
            SET consumed_at = NOW(),
                updated_at = NOW()
            WHERE mcp_approval_request_id = $1
              AND status = 'approved'
              AND consumed_at IS NULL
              AND (expires_at IS NULL OR expires_at > NOW())
            RETURNING {_RETURNING_COLUMNS}
            """,
            approval_request_id,
        )
        return to_approval_request_record(rows[0]) if rows else None

    async def get_approval_request(
        self, approval_request_id: str
    ) -> MCPApprovalRequestRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            SELECT {APPROVAL_SELECT_COLUMNS}
            FROM deltallm_mcpapprovalrequest r
            WHERE r.mcp_approval_request_id = $1
            LIMIT 1
            """,
            approval_request_id,
        )
        return to_approval_request_record(rows[0]) if rows else None

    async def create_approval_request(
        self,
        *,
        server_id: str,
        tool_name: str,
        scope_type: str,
        scope_id: str,
        request_fingerprint: str,
        requested_by_api_key: str | None,
        requested_by_user: str | None,
        organization_id: str | None,
        request_id: str | None,
        correlation_id: str | None,
        arguments_json: dict[str, Any],
        expires_at: datetime | None,
        metadata: dict[str, Any] | None,
    ) -> MCPApprovalRequestRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            INSERT INTO deltallm_mcpapprovalrequest (
                mcp_approval_request_id, mcp_server_id, tool_name, scope_type, scope_id, status,
                request_fingerprint, requested_by_api_key, requested_by_user, organization_id,
                request_id, correlation_id, arguments_json, expires_at, metadata, created_at, updated_at
            )
            VALUES (
                gen_random_uuid()::text, $1, $2, $3, $4, 'pending',
                $5, $6, $7, $8,
                $9, $10, $11::jsonb, $12, $13::jsonb, NOW(), NOW()
            )
            ON CONFLICT (request_fingerprint) WHERE status = 'pending'
            DO UPDATE SET updated_at = deltallm_mcpapprovalrequest.updated_at
            RETURNING {_RETURNING_COLUMNS}
            """,
            server_id,
            tool_name,
            scope_type,
            scope_id,
            request_fingerprint,
            requested_by_api_key,
            requested_by_user,
            organization_id,
            request_id,
            correlation_id,
            arguments_json,
            expires_at,
            metadata or {},
        )
        return to_approval_request_record(rows[0]) if rows else None

    async def list_approval_requests(
        self,
        *,
        server_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[MCPApprovalRequestRecord], int]:
        if self.prisma is None:
            return [], 0

        clauses: list[str] = []
        params: list[Any] = []
        if server_id:
            params.append(server_id)
            clauses.append(f"r.mcp_server_id = ${len(params)}")
        if status:
            clauses.append(approval_status_clause(status, params))
        where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""

        count_rows = await self.prisma.query_raw(
            f"SELECT COUNT(*)::int AS total FROM deltallm_mcpapprovalrequest r {where_sql}",
            *params,
        )
        total = int((count_rows[0] if count_rows else {}).get("total") or 0)

        page_params = [*params, limit, offset]
        rows = await self.prisma.query_raw(
            f"""
            SELECT {APPROVAL_SELECT_COLUMNS}
            FROM deltallm_mcpapprovalrequest r
            {where_sql}
            ORDER BY r.created_at DESC, r.mcp_approval_request_id ASC
            LIMIT ${len(page_params) - 1} OFFSET ${len(page_params)}
            """,
            *page_params,
        )
        return [to_approval_request_record(row) for row in rows], total

    async def decide_approval_request(
        self,
        approval_request_id: str,
        *,
        status: str,
        decided_by_account_id: str | None,
        decision_comment: str | None,
        expires_at: datetime | None,
    ) -> MCPApprovalRequestRecord | None:
        """Decide a pending request. Returns ``None`` if it is decided or expired already."""
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_mcpapprovalrequest
            SET status = $2,
                decided_by_account_id = $3,
                decision_comment = $4,
                expires_at = $5,
                decided_at = NOW(),
                updated_at = NOW()
            WHERE mcp_approval_request_id = $1
              AND status = 'pending'
              AND (expires_at IS NULL OR expires_at > NOW())
            RETURNING {_RETURNING_COLUMNS}
            """,
            approval_request_id,
            status,
            decided_by_account_id,
            decision_comment,
            expires_at,
        )
        return to_approval_request_record(rows[0]) if rows else None


__all__ = [
    "APPROVAL_EFFECTIVE_STATUS_SQL",
    "APPROVAL_SELECT_COLUMNS",
    "APPROVAL_STATUS_FILTERS",
    "MCPApprovalRepositoryMixin",
    "MCPApprovalRequestRecord",
    "approval_status_clause",
    "to_approval_request_record",
]
