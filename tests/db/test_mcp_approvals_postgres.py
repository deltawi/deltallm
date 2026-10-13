"""MCP approval-request lifecycle against real PostgreSQL."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from src.db.mcp.mcp import MCPRepository
from tests.db.tier_migration_helpers import connect_prisma, seed_organization

pytestmark = pytest.mark.postgres


async def _seed_server(db, server_id: str) -> None:  # noqa: ANN001
    await seed_organization(db, organization_id=server_id)
    await db.execute_raw(
        """
        INSERT INTO deltallm_mcpserver (
            mcp_server_id, server_key, name, base_url, forwarded_headers_allowlist, updated_at
        )
        VALUES ($1, $2, 'Approval test', 'https://mcp.example.com', ARRAY[]::text[], NOW())
        """,
        server_id,
        f"approvals-{server_id}",
    )


async def _cleanup(db, server_id: str) -> None:  # noqa: ANN001
    await db.execute_raw("DELETE FROM deltallm_mcpserver WHERE mcp_server_id = $1", server_id)
    await db.execute_raw(
        "DELETE FROM deltallm_organizationtable WHERE organization_id = $1", server_id
    )
    await db.disconnect()


async def _create(repository: MCPRepository, server_id: str, fingerprint: str):  # noqa: ANN202
    return await repository.create_approval_request(
        server_id=server_id,
        tool_name="search",
        scope_type="organization",
        scope_id=server_id,
        request_fingerprint=fingerprint,
        requested_by_api_key=None,
        requested_by_user=None,
        organization_id=server_id,
        request_id=None,
        correlation_id=None,
        arguments_json={"query": "delta"},
        expires_at=None,
        metadata={"requested_by_account_id": "acct-requester"},
    )


async def _expire(db, approval_request_id: str) -> None:  # noqa: ANN001
    await db.execute_raw(
        "UPDATE deltallm_mcpapprovalrequest SET expires_at = NOW() - INTERVAL '1 minute' "
        "WHERE mcp_approval_request_id = $1",
        approval_request_id,
    )


async def test_expired_pending_request_reads_as_expired_and_cannot_be_decided() -> None:
    db = await connect_prisma()
    server_id = uuid4().hex
    repository = MCPRepository(db)
    try:
        await _seed_server(db, server_id)
        created = await _create(repository, server_id, f"fp-{server_id}")
        assert created is not None and created.requested_by_account_id == "acct-requester"
        await _expire(db, created.mcp_approval_request_id)

        loaded = await repository.get_approval_request(created.mcp_approval_request_id)
        assert loaded is not None and loaded.status == "expired"
        pending, pending_total = await repository.list_approval_requests(
            server_id=server_id, status="pending"
        )
        expired, expired_total = await repository.list_approval_requests(
            server_id=server_id, status="expired"
        )
        assert (pending, pending_total) == ([], 0)
        assert expired_total == 1 and expired[0].status == "expired"

        decided = await repository.decide_approval_request(
            created.mcp_approval_request_id,
            status="approved",
            decided_by_account_id="acct-admin",
            decision_comment=None,
            expires_at=None,
        )
        assert decided is None
    finally:
        await _cleanup(db, server_id)


async def test_approval_is_consumed_once_under_concurrency() -> None:
    db = await connect_prisma()
    server_id = uuid4().hex
    fingerprint = f"fp-{server_id}"
    repository = MCPRepository(db)
    try:
        await _seed_server(db, server_id)
        created = await _create(repository, server_id, fingerprint)
        assert created is not None
        decided = await repository.decide_approval_request(
            created.mcp_approval_request_id,
            status="approved",
            decided_by_account_id="acct-admin",
            decision_comment=None,
            expires_at=None,
        )
        assert decided is not None and decided.status == "approved"

        results = await asyncio.gather(
            *(
                repository.consume_approval_request(created.mcp_approval_request_id)
                for _ in range(5)
            )
        )
        consumed = [item for item in results if item is not None]
        assert len(consumed) == 1 and consumed[0].consumed_at is not None
        assert (
            await repository.find_active_approval_request(request_fingerprint=fingerprint) is None
        )

        # A used approval keeps its decision after its window ends.
        await _expire(db, created.mcp_approval_request_id)
        assert await repository.expire_stale_approval_requests(request_fingerprint=fingerprint) == 0
        loaded = await repository.get_approval_request(created.mcp_approval_request_id)
        assert loaded is not None and loaded.status == "approved"
    finally:
        await _cleanup(db, server_id)


async def test_unused_approval_reads_as_expired_after_its_window() -> None:
    db = await connect_prisma()
    server_id = uuid4().hex
    repository = MCPRepository(db)
    try:
        await _seed_server(db, server_id)
        created = await _create(repository, server_id, f"fp-{server_id}")
        assert created is not None
        await repository.decide_approval_request(
            created.mcp_approval_request_id,
            status="approved",
            decided_by_account_id="acct-admin",
            decision_comment=None,
            expires_at=None,
        )
        await _expire(db, created.mcp_approval_request_id)

        loaded = await repository.get_approval_request(created.mcp_approval_request_id)
        assert loaded is not None and loaded.status == "expired"
        _, approved_total = await repository.list_approval_requests(
            server_id=server_id, status="approved"
        )
        _, expired_total = await repository.list_approval_requests(
            server_id=server_id, status="expired"
        )
        assert (approved_total, expired_total) == (0, 1)
        assert await repository.consume_approval_request(created.mcp_approval_request_id) is None
    finally:
        await _cleanup(db, server_id)
