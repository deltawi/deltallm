"""Persistence for per-user MCP OAuth connections and pending authorization states.

Token values arrive here already encrypted. This module never sees plaintext
tokens or code verifiers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.db.mcp.mcp import _parse_datetime, _parse_text_list

_CONNECTION_COLUMNS = """
    mcp_oauth_connection_id,
    mcp_server_id,
    account_id,
    status,
    status_reason,
    client_id,
    access_token_ciphertext,
    refresh_token_ciphertext,
    token_type,
    scopes,
    account_label,
    expires_at,
    token_version,
    refresh_lease_owner,
    refresh_lease_expires_at,
    connected_at,
    last_refreshed_at,
    updated_at
"""

_STATE_COLUMNS = """
    state_hash,
    mcp_server_id,
    account_id,
    client_id,
    redirect_uri,
    code_verifier_ciphertext,
    expires_at,
    (expires_at > NOW()) AS is_live
"""

# Bound the pending rows of one account. A user who clicks "Connect" many
# times keeps only the newest requests.
MAX_PENDING_STATES_PER_ACCOUNT = 20


@dataclass(frozen=True)
class MCPOAuthConnectionRecord:
    connection_id: str
    server_id: str
    account_id: str
    status: str
    client_id: str
    access_token_ciphertext: str = field(repr=False)
    refresh_token_ciphertext: str | None = field(default=None, repr=False)
    status_reason: str | None = None
    token_type: str | None = None
    scopes: tuple[str, ...] = ()
    account_label: str | None = None
    expires_at: datetime | None = None
    token_version: int = 1
    refresh_lease_owner: str | None = None
    refresh_lease_expires_at: datetime | None = None
    connected_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class MCPOAuthStateRecord:
    state_hash: str
    server_id: str
    account_id: str
    client_id: str
    redirect_uri: str
    code_verifier_ciphertext: str = field(repr=False)
    expires_at: datetime | None = None
    is_live: bool = False


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def to_connection_record(row: dict[str, Any]) -> MCPOAuthConnectionRecord:
    return MCPOAuthConnectionRecord(
        connection_id=str(row.get("mcp_oauth_connection_id") or ""),
        server_id=str(row.get("mcp_server_id") or ""),
        account_id=str(row.get("account_id") or ""),
        status=str(row.get("status") or "connected"),
        status_reason=_optional_str(row.get("status_reason")),
        client_id=str(row.get("client_id") or ""),
        access_token_ciphertext=str(row.get("access_token_ciphertext") or ""),
        refresh_token_ciphertext=_optional_str(row.get("refresh_token_ciphertext")),
        token_type=_optional_str(row.get("token_type")),
        scopes=tuple(_parse_text_list(row.get("scopes"))),
        account_label=_optional_str(row.get("account_label")),
        expires_at=_parse_datetime(row.get("expires_at")),
        token_version=int(row.get("token_version") or 1),
        refresh_lease_owner=_optional_str(row.get("refresh_lease_owner")),
        refresh_lease_expires_at=_parse_datetime(row.get("refresh_lease_expires_at")),
        connected_at=_parse_datetime(row.get("connected_at")),
        last_refreshed_at=_parse_datetime(row.get("last_refreshed_at")),
        updated_at=_parse_datetime(row.get("updated_at")),
    )


def to_state_record(row: dict[str, Any]) -> MCPOAuthStateRecord:
    return MCPOAuthStateRecord(
        state_hash=str(row.get("state_hash") or ""),
        server_id=str(row.get("mcp_server_id") or ""),
        account_id=str(row.get("account_id") or ""),
        client_id=str(row.get("client_id") or ""),
        redirect_uri=str(row.get("redirect_uri") or ""),
        code_verifier_ciphertext=str(row.get("code_verifier_ciphertext") or ""),
        expires_at=_parse_datetime(row.get("expires_at")),
        is_live=bool(row.get("is_live")),
    )


class MCPOAuthRepository:
    def __init__(self, prisma_client: Any | None = None) -> None:
        self.prisma = prisma_client

    # Connections -----------------------------------------------------------

    async def get_connection(
        self, *, server_id: str, account_id: str
    ) -> MCPOAuthConnectionRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            SELECT {_CONNECTION_COLUMNS}
            FROM deltallm_mcpoauthconnection
            WHERE mcp_server_id = $1 AND account_id = $2
            LIMIT 1
            """,
            server_id,
            account_id,
        )
        return to_connection_record(rows[0]) if rows else None

    async def list_connections_for_account(self, account_id: str) -> list[MCPOAuthConnectionRecord]:
        if self.prisma is None:
            return []
        rows = await self.prisma.query_raw(
            f"""
            SELECT {_CONNECTION_COLUMNS}
            FROM deltallm_mcpoauthconnection
            WHERE account_id = $1
            ORDER BY connected_at DESC
            LIMIT 500
            """,
            account_id,
        )
        return [to_connection_record(row) for row in rows]

    async def upsert_connection(
        self,
        *,
        server_id: str,
        account_id: str,
        client_id: str,
        access_token_ciphertext: str,
        refresh_token_ciphertext: str | None,
        token_type: str | None,
        scopes: list[str],
        account_label: str | None,
        expires_in_seconds: int | None,
    ) -> MCPOAuthConnectionRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            INSERT INTO deltallm_mcpoauthconnection (
                mcp_oauth_connection_id, mcp_server_id, account_id, status, status_reason,
                client_id, access_token_ciphertext, refresh_token_ciphertext, token_type,
                scopes, account_label, expires_at, token_version, connected_at,
                created_at, updated_at
            )
            VALUES (
                gen_random_uuid()::text, $1, $2, 'connected', NULL,
                $3, $4, $5, $6,
                $7::text[], $8,
                CASE WHEN $9::int IS NULL THEN NULL
                     ELSE NOW() + ($9::int * INTERVAL '1 second') END,
                1, NOW(), NOW(), NOW()
            )
            ON CONFLICT (mcp_server_id, account_id) DO UPDATE SET
                status = 'connected',
                status_reason = NULL,
                client_id = EXCLUDED.client_id,
                access_token_ciphertext = EXCLUDED.access_token_ciphertext,
                refresh_token_ciphertext = EXCLUDED.refresh_token_ciphertext,
                token_type = EXCLUDED.token_type,
                scopes = EXCLUDED.scopes,
                account_label = EXCLUDED.account_label,
                expires_at = EXCLUDED.expires_at,
                token_version = deltallm_mcpoauthconnection.token_version + 1,
                refresh_lease_owner = NULL,
                refresh_lease_expires_at = NULL,
                connected_at = NOW(),
                last_refreshed_at = NULL,
                updated_at = NOW()
            RETURNING {_CONNECTION_COLUMNS}
            """,
            server_id,
            account_id,
            client_id,
            access_token_ciphertext,
            refresh_token_ciphertext,
            token_type,
            list(scopes),
            account_label,
            expires_in_seconds,
        )
        return to_connection_record(rows[0]) if rows else None

    async def claim_refresh_lease(
        self,
        *,
        connection_id: str,
        token_version: int,
        owner: str,
        lease_seconds: int,
    ) -> bool:
        """Take the refresh lease if nobody holds a live one and the token is unchanged."""
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpoauthconnection
            SET refresh_lease_owner = $3,
                refresh_lease_expires_at = NOW() + ($4::int * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE mcp_oauth_connection_id = $1
              AND token_version = $2
              AND status = 'connected'
              AND (refresh_lease_expires_at IS NULL OR refresh_lease_expires_at <= NOW())
            RETURNING mcp_oauth_connection_id
            """,
            connection_id,
            token_version,
            owner,
            lease_seconds,
        )
        return bool(rows)

    async def complete_refresh(
        self,
        *,
        connection_id: str,
        owner: str,
        access_token_ciphertext: str,
        refresh_token_ciphertext: str | None,
        scopes: list[str] | None,
        expires_in_seconds: int | None,
    ) -> MCPOAuthConnectionRecord | None:
        """Store refreshed tokens. Only the lease owner can write (fencing)."""
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            UPDATE deltallm_mcpoauthconnection
            SET access_token_ciphertext = $3,
                refresh_token_ciphertext = COALESCE($4, refresh_token_ciphertext),
                scopes = COALESCE($5::text[], scopes),
                expires_at = CASE WHEN $6::int IS NULL THEN NULL
                                  ELSE NOW() + ($6::int * INTERVAL '1 second') END,
                token_version = token_version + 1,
                refresh_lease_owner = NULL,
                refresh_lease_expires_at = NULL,
                last_refreshed_at = NOW(),
                updated_at = NOW()
            WHERE mcp_oauth_connection_id = $1
              AND refresh_lease_owner = $2
            RETURNING {_CONNECTION_COLUMNS}
            """,
            connection_id,
            owner,
            access_token_ciphertext,
            refresh_token_ciphertext,
            list(scopes) if scopes is not None else None,
            expires_in_seconds,
        )
        return to_connection_record(rows[0]) if rows else None

    async def release_refresh_lease(self, *, connection_id: str, owner: str) -> None:
        if self.prisma is None:
            return
        await self.prisma.execute_raw(
            """
            UPDATE deltallm_mcpoauthconnection
            SET refresh_lease_owner = NULL,
                refresh_lease_expires_at = NULL
            WHERE mcp_oauth_connection_id = $1
              AND refresh_lease_owner = $2
            """,
            connection_id,
            owner,
        )

    async def mark_needs_reconnect(
        self,
        *,
        connection_id: str,
        token_version: int,
        reason: str,
    ) -> bool:
        """Mark the connection unusable unless another writer already replaced the token."""
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpoauthconnection
            SET status = 'needs_reconnect',
                status_reason = $3,
                refresh_lease_owner = NULL,
                refresh_lease_expires_at = NULL,
                updated_at = NOW()
            WHERE mcp_oauth_connection_id = $1
              AND token_version = $2
            RETURNING mcp_oauth_connection_id
            """,
            connection_id,
            token_version,
            reason,
        )
        return bool(rows)

    async def delete_connection(
        self, *, server_id: str, account_id: str
    ) -> MCPOAuthConnectionRecord | None:
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            DELETE FROM deltallm_mcpoauthconnection
            WHERE mcp_server_id = $1 AND account_id = $2
            RETURNING {_CONNECTION_COLUMNS}
            """,
            server_id,
            account_id,
        )
        return to_connection_record(rows[0]) if rows else None

    async def delete_connections_for_server(self, server_id: str) -> int:
        """Delete all connections and pending states of one server."""
        if self.prisma is None:
            return 0
        await self.prisma.execute_raw(
            "DELETE FROM deltallm_mcpoauthstate WHERE mcp_server_id = $1", server_id
        )
        deleted = await self.prisma.execute_raw(
            "DELETE FROM deltallm_mcpoauthconnection WHERE mcp_server_id = $1", server_id
        )
        return int(deleted or 0)

    async def store_server_oauth_config(
        self, *, server_id: str, auth_config: dict[str, Any]
    ) -> bool:
        """Write the OAuth settings of an oauth2 server (discovery result, client)."""
        if self.prisma is None:
            return False
        rows = await self.prisma.query_raw(
            """
            UPDATE deltallm_mcpserver
            SET auth_config = $2::jsonb,
                updated_at = NOW()
            WHERE mcp_server_id = $1
              AND auth_mode = 'oauth2'
            RETURNING mcp_server_id
            """,
            server_id,
            auth_config,
        )
        return bool(rows)

    # Pending authorization states -----------------------------------------

    async def create_state(
        self,
        *,
        state_hash: str,
        server_id: str,
        account_id: str,
        client_id: str,
        redirect_uri: str,
        code_verifier_ciphertext: str,
        ttl_seconds: int,
    ) -> None:
        if self.prisma is None:
            raise RuntimeError("MCP OAuth state store is unavailable")
        await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_mcpoauthstate
            WHERE account_id = $1
              AND (
                expires_at <= NOW()
                OR state_hash IN (
                    SELECT state_hash
                    FROM deltallm_mcpoauthstate
                    WHERE account_id = $1
                    ORDER BY created_at DESC
                    OFFSET $2
                )
              )
            """,
            account_id,
            MAX_PENDING_STATES_PER_ACCOUNT - 1,
        )
        # Also remove a small batch of expired states of other accounts, so
        # abandoned flows do not stay in the table.
        await self.delete_expired_states(limit=100)
        await self.prisma.execute_raw(
            """
            INSERT INTO deltallm_mcpoauthstate (
                state_hash, mcp_server_id, account_id, client_id, redirect_uri,
                code_verifier_ciphertext, expires_at, created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, NOW() + ($7::int * INTERVAL '1 second'), NOW())
            """,
            state_hash,
            server_id,
            account_id,
            client_id,
            redirect_uri,
            code_verifier_ciphertext,
            ttl_seconds,
        )

    async def consume_state(self, state_hash: str) -> MCPOAuthStateRecord | None:
        """Delete and return a state. A state works one time, live or expired."""
        if self.prisma is None:
            return None
        rows = await self.prisma.query_raw(
            f"""
            DELETE FROM deltallm_mcpoauthstate
            WHERE state_hash = $1
            RETURNING {_STATE_COLUMNS}
            """,
            state_hash,
        )
        return to_state_record(rows[0]) if rows else None

    async def delete_expired_states(self, *, limit: int = 500) -> int:
        if self.prisma is None:
            return 0
        deleted = await self.prisma.execute_raw(
            """
            DELETE FROM deltallm_mcpoauthstate
            WHERE state_hash IN (
                SELECT state_hash
                FROM deltallm_mcpoauthstate
                WHERE expires_at <= NOW()
                LIMIT $1
            )
            """,
            limit,
        )
        return int(deleted or 0)
