"""MCP OAuth connections and pending states against real PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.db.mcp.mcp_oauth import MCPOAuthRepository
from tests.db.tier_migration_helpers import connect_prisma

pytestmark = pytest.mark.postgres


class _Seed:
    def __init__(self, db) -> None:  # noqa: ANN001
        self.db = db
        self.prefix = "oauth-" + uuid4().hex[:12]
        self.server_id = self.prefix + "-srv"
        self.account_id = self.prefix + "-acct"

    async def create(self) -> None:
        await self.db.execute_raw(
            "INSERT INTO deltallm_platformaccount(account_id,email,role,updated_at) "
            "VALUES ($1,$2,'org_user',NOW())",
            self.account_id,
            self.prefix + "@example.invalid",
        )
        await self.db.execute_raw(
            """
            INSERT INTO deltallm_mcpserver (
                mcp_server_id, server_key, name, base_url, auth_mode, auth_config,
                forwarded_headers_allowlist, updated_at
            )
            VALUES ($1, $2, 'OAuth test', 'https://mcp.example.com/mcp', 'oauth2', '{}'::jsonb,
                    ARRAY[]::text[], NOW())
            """,
            self.server_id,
            self.prefix,
        )

    async def cleanup(self) -> None:
        await self.db.execute_raw(
            "DELETE FROM deltallm_mcpserver WHERE mcp_server_id = $1", self.server_id
        )
        await self.db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1", self.account_id
        )
        await self.db.disconnect()


@pytest.fixture
async def seed():  # noqa: ANN201
    db = await connect_prisma()
    item = _Seed(db)
    await item.create()
    try:
        yield item
    finally:
        await item.cleanup()


async def _upsert(repository: MCPOAuthRepository, seed: _Seed, access: str = "ct-access-1"):  # noqa: ANN202
    return await repository.upsert_connection(
        server_id=seed.server_id,
        account_id=seed.account_id,
        client_id="cid",
        access_token_ciphertext=access,
        refresh_token_ciphertext="ct-refresh-1",
        token_type="Bearer",
        scopes=["repo"],
        account_label="dana-k",
        expires_in_seconds=3600,
    )


async def test_upsert_replaces_tokens_and_bumps_version(seed: _Seed) -> None:
    repository = MCPOAuthRepository(seed.db)
    first = await _upsert(repository, seed)
    assert first.status == "connected"
    assert first.expires_at is not None

    await repository.mark_needs_reconnect(
        connection_id=first.connection_id, token_version=first.token_version, reason="revoked"
    )
    second = await _upsert(repository, seed, access="ct-access-2")

    assert second.connection_id == first.connection_id
    assert second.token_version == first.token_version + 1
    assert second.status == "connected"
    assert second.status_reason is None
    assert second.access_token_ciphertext == "ct-access-2"
    listed = await repository.list_connections_for_account(seed.account_id)
    assert [item.connection_id for item in listed] == [first.connection_id]


async def test_refresh_lease_is_exclusive_and_fenced(seed: _Seed) -> None:
    repository = MCPOAuthRepository(seed.db)
    connection = await _upsert(repository, seed)
    version = connection.token_version

    assert await repository.claim_refresh_lease(
        connection_id=connection.connection_id, token_version=version, owner="a", lease_seconds=30
    )
    assert not await repository.claim_refresh_lease(
        connection_id=connection.connection_id, token_version=version, owner="b", lease_seconds=30
    )
    # Only the lease owner can store the refreshed token.
    assert (
        await repository.complete_refresh(
            connection_id=connection.connection_id,
            owner="b",
            access_token_ciphertext="ct-b",
            refresh_token_ciphertext=None,
            scopes=None,
            expires_in_seconds=60,
        )
        is None
    )
    refreshed = await repository.complete_refresh(
        connection_id=connection.connection_id,
        owner="a",
        access_token_ciphertext="ct-a",
        refresh_token_ciphertext=None,
        scopes=None,
        expires_in_seconds=60,
    )
    assert refreshed is not None
    assert refreshed.token_version == version + 1
    assert refreshed.refresh_token_ciphertext == "ct-refresh-1"
    assert refreshed.refresh_lease_owner is None
    # A stale version cannot claim the lease or mark the connection.
    assert not await repository.claim_refresh_lease(
        connection_id=connection.connection_id, token_version=version, owner="c", lease_seconds=30
    )
    assert not await repository.mark_needs_reconnect(
        connection_id=connection.connection_id, token_version=version, reason="x"
    )


async def test_state_works_one_time_and_reports_expiry(seed: _Seed) -> None:
    repository = MCPOAuthRepository(seed.db)
    common = {
        "server_id": seed.server_id,
        "account_id": seed.account_id,
        "client_id": "cid",
        "redirect_uri": "https://gw.example.com/mcp/oauth/callback",
        "code_verifier_ciphertext": "ct-verifier",
    }
    await repository.create_state(state_hash="live-" + seed.prefix, ttl_seconds=600, **common)
    await repository.create_state(state_hash="old-" + seed.prefix, ttl_seconds=0, **common)

    live = await repository.consume_state("live-" + seed.prefix)
    assert live is not None and live.is_live is True
    assert live.account_id == seed.account_id
    assert await repository.consume_state("live-" + seed.prefix) is None

    rows = await seed.db.query_raw(
        "SELECT state_hash FROM deltallm_mcpoauthstate WHERE account_id = $1", seed.account_id
    )
    # Creating a state first deletes the expired states of the account.
    assert [row["state_hash"] for row in rows] in ([], ["old-" + seed.prefix])
    expired = await repository.consume_state("old-" + seed.prefix)
    assert expired is None or expired.is_live is False


async def test_pending_states_per_account_are_capped(seed: _Seed) -> None:
    repository = MCPOAuthRepository(seed.db)
    for index in range(25):
        await repository.create_state(
            state_hash=f"{seed.prefix}-{index}",
            server_id=seed.server_id,
            account_id=seed.account_id,
            client_id="cid",
            redirect_uri="https://gw.example.com/mcp/oauth/callback",
            code_verifier_ciphertext="ct",
            ttl_seconds=600,
        )
    rows = await seed.db.query_raw(
        "SELECT COUNT(*)::int AS n FROM deltallm_mcpoauthstate WHERE account_id = $1",
        seed.account_id,
    )
    assert rows[0]["n"] == 20


async def test_store_server_oauth_config_updates_only_oauth2_servers(seed: _Seed) -> None:
    repository = MCPOAuthRepository(seed.db)
    assert await repository.store_server_oauth_config(
        server_id=seed.server_id, auth_config={"client_id": "cid", "scopes": ["repo"]}
    )
    rows = await seed.db.query_raw(
        "SELECT auth_config FROM deltallm_mcpserver WHERE mcp_server_id = $1", seed.server_id
    )
    assert rows[0]["auth_config"]["client_id"] == "cid"
    await seed.db.execute_raw(
        "UPDATE deltallm_mcpserver SET auth_mode = 'none' WHERE mcp_server_id = $1",
        seed.server_id,
    )
    assert not await repository.store_server_oauth_config(
        server_id=seed.server_id, auth_config={"client_id": "other"}
    )


@pytest.mark.parametrize("deleted", ["account", "server"])
async def test_connections_and_states_cascade_on_delete(seed: _Seed, deleted: str) -> None:
    repository = MCPOAuthRepository(seed.db)
    await _upsert(repository, seed)
    await repository.create_state(
        state_hash="cascade-" + seed.prefix,
        server_id=seed.server_id,
        account_id=seed.account_id,
        client_id="cid",
        redirect_uri="https://gw.example.com/mcp/oauth/callback",
        code_verifier_ciphertext="ct",
        ttl_seconds=600,
    )
    if deleted == "account":
        await seed.db.execute_raw(
            "DELETE FROM deltallm_platformaccount WHERE account_id = $1", seed.account_id
        )
    else:
        await seed.db.execute_raw(
            "DELETE FROM deltallm_mcpserver WHERE mcp_server_id = $1", seed.server_id
        )

    assert (
        await repository.get_connection(server_id=seed.server_id, account_id=seed.account_id)
        is None
    )
    assert await repository.consume_state("cascade-" + seed.prefix) is None


async def test_delete_connections_for_server(seed: _Seed) -> None:
    repository = MCPOAuthRepository(seed.db)
    await _upsert(repository, seed)
    assert await repository.delete_connections_for_server(seed.server_id) == 1
    assert await repository.list_connections_for_account(seed.account_id) == []
