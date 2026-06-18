from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from src.db.tiers import TierRepository

from tests.db.tier_repository_fakes import _FakePrisma


@pytest.mark.asyncio
async def test_create_tier_version_maps_counts_and_metadata() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    record = await repository.create_tier_version(
        tier_id="tier-1",
        version_number=2,
        status="draft",
        metadata={"notes": "pricing update"},
    )

    assert record.tier_id == "tier-1"
    assert record.version_number == 2
    assert record.status == "draft"
    assert record.metadata == {"notes": "pricing update"}
    assert record.model_policy_count == 0
    assert record.capacity_pool_count == 0
    assert json.loads(str(prisma.calls[0][1][5])) == {"notes": "pricing update"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["active", "archived"])
async def test_create_tier_version_requires_draft_status(status: str) -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="created as draft"):
        await repository.create_tier_version(
            tier_id="tier-1",
            version_number=2,
            status=status,
        )

    assert prisma.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("version_number", [None, 0, -1, True, 1.0, "1.0"])
async def test_create_tier_version_requires_positive_integer_version_number(
    version_number: object,
) -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="version_number"):
        await repository.create_tier_version(
            tier_id="tier-1",
            version_number=version_number,  # type: ignore[arg-type]
        )

    assert prisma.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("published_at", "published_by_account_id"),
    [
        (datetime(2026, 7, 1, tzinfo=UTC), None),
        (None, "acct-1"),
    ],
)
async def test_create_tier_version_rejects_publish_metadata_for_drafts(
    published_at: datetime | None,
    published_by_account_id: str | None,
) -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="publish metadata"):
        await repository.create_tier_version(
            tier_id="tier-1",
            version_number=2,
            published_at=published_at,
            published_by_account_id=published_by_account_id,
        )

    assert prisma.calls == []


@pytest.mark.asyncio
async def test_version_lookup_queries_expand_shared_select_sql() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    record = await repository.get_tier_version("ver-1")

    assert record is not None
    assert record.tier_id == "tier-1"
    assert record.version_number == 2
    assert "FROM deltallm_tierversion v" in prisma.calls[0][0]
    assert "{self._tier_version_select_sql()}" not in prisma.calls[0][0]


@pytest.mark.asyncio
async def test_get_active_tier_version_filters_active_status() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    record = await repository.get_active_tier_version("tier-1")

    assert record is not None
    assert record.status == "active"
    assert "v.status = 'active'" in prisma.calls[0][0]
    assert prisma.calls[0][1] == ("tier-1",)


@pytest.mark.asyncio
async def test_publish_tier_version_archives_existing_active_version_then_activates_target() -> (
    None
):
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)

    record = await repository.publish_tier_version(
        "ver-1",
        published_by_account_id="acct-1",
    )

    assert record is not None
    assert record.status == "active"
    assert record.published_by_account_id == "acct-1"
    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    assert prisma.executions == []
    tx = prisma.tx_clients[0]
    assert "WHERE v.tier_version_id = $1" in tx.calls[0][0]
    assert "FOR UPDATE OF v" not in tx.calls[0][0]
    assert "FROM deltallm_tier" in tx.calls[1][0]
    assert "FOR UPDATE" in tx.calls[1][0]
    assert tx.calls[1][1] == ("tier-1",)
    assert "WHERE v.tier_version_id = $1" in tx.calls[2][0]
    assert "FOR UPDATE OF v" in tx.calls[2][0]
    assert "v.status = 'active'" in tx.calls[3][0]
    assert tx.calls[3][1] == ("tier-1", "ver-1")
    assert "tier_version_id = $1" in tx.calls[4][0]
    assert tx.calls[4][1] == ("ver-active",)
    assert "UPDATE deltallm_tierversion" in tx.executions[0][0]
    assert tx.executions[0][1] == ("tier-1", "ver-1")
    assert "SET status = 'active'" in tx.calls[5][0]
    assert tx.calls[5][1] == ("ver-1", "acct-1")


@pytest.mark.asyncio
async def test_publish_tier_version_requires_transaction_support() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="publish_tier_version"):
        await repository.publish_tier_version(
            "ver-1",
            published_by_account_id="acct-1",
        )

    assert prisma.calls == []
    assert prisma.executions == []


@pytest.mark.asyncio
async def test_publish_tier_version_uses_transaction_client_when_available() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)

    record = await repository.publish_tier_version(
        "ver-1",
        published_by_account_id="acct-1",
    )

    assert record is not None
    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    assert prisma.executions == []
    tx = prisma.tx_clients[0]
    assert "FOR UPDATE OF v" not in tx.calls[0][0]
    assert "FROM deltallm_tier" in tx.calls[1][0]
    assert "FOR UPDATE OF v" in tx.calls[2][0]
    assert "v.status = 'active'" in tx.calls[3][0]
    assert "tier_version_id = $1" in tx.calls[4][0]
    assert "UPDATE deltallm_tierversion" in tx.executions[0][0]
    assert "SET status = 'active'" in tx.calls[5][0]


@pytest.mark.asyncio
async def test_publish_tier_version_rejects_pinned_assignments_on_current_active() -> None:
    prisma = _FakePrisma(enable_tx=True, pinned_assignment_count=1)
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="pinned"):
        await repository.publish_tier_version("ver-1", published_by_account_id="acct-1")

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert "FOR UPDATE OF v" not in tx.calls[0][0]
    assert "FROM deltallm_tier" in tx.calls[1][0]
    assert "FOR UPDATE OF v" in tx.calls[2][0]
    assert "v.status = 'active'" in tx.calls[3][0]
    assert "tier_version_id = $1" in tx.calls[4][0]
    assert "ends_at IS NULL OR ends_at > NOW()" in tx.calls[4][0]
    assert tx.executions == []
    assert all("SET status = 'active'" not in sql for sql, _ in tx.calls)


@pytest.mark.asyncio
async def test_publish_tier_version_rejects_non_draft_target() -> None:
    prisma = _FakePrisma(enable_tx=True, version_lookup_status="active")
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="draft"):
        await repository.publish_tier_version("ver-1", published_by_account_id="acct-1")

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 3
    assert "FOR UPDATE OF v" not in tx.calls[0][0]
    assert "FROM deltallm_tier" in tx.calls[1][0]
    assert "FOR UPDATE OF v" in tx.calls[2][0]
    assert tx.executions == []


@pytest.mark.asyncio
async def test_publish_tier_version_rolls_back_transaction_on_activation_failure() -> None:
    prisma = _FakePrisma(enable_tx=True, fail_on_sql="SET status = 'active'")
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="simulated query failure"):
        await repository.publish_tier_version("ver-1", published_by_account_id="acct-1")

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1


@pytest.mark.asyncio
async def test_archive_tier_version_archives_draft_version() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)

    record = await repository.archive_tier_version("ver-1")

    assert record is not None
    assert record.status == "archived"
    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    tx = prisma.tx_clients[0]
    assert "FOR UPDATE OF v" not in tx.calls[0][0]
    assert "FROM deltallm_tier" in tx.calls[1][0]
    assert "FOR UPDATE OF v" in tx.calls[2][0]
    assert "SET status = 'archived'" in tx.calls[3][0]


@pytest.mark.asyncio
async def test_archive_tier_version_requires_transaction_support() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="archive_tier_version"):
        await repository.archive_tier_version("ver-1")

    assert prisma.calls == []
    assert prisma.executions == []


@pytest.mark.asyncio
async def test_archive_tier_version_rejects_active_version_with_unpinned_assignments() -> None:
    prisma = _FakePrisma(
        enable_tx=True,
        version_lookup_status="active",
        unpinned_assignment_count=1,
    )
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="enabled assignments"):
        await repository.archive_tier_version("ver-1")

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert "FOR UPDATE OF v" not in tx.calls[0][0]
    assert "FROM deltallm_tier" in tx.calls[1][0]
    assert "FOR UPDATE OF v" in tx.calls[2][0]
    assert "tier_version_id IS NULL" in tx.calls[3][0]
    assert "ends_at IS NULL OR ends_at > NOW()" in tx.calls[3][0]
    assert all("SET status = 'archived'" not in sql for sql, _ in tx.calls)


@pytest.mark.asyncio
async def test_archive_active_tier_version_allows_when_no_non_expired_assignments() -> None:
    prisma = _FakePrisma(enable_tx=True, version_lookup_status="active")
    repository = TierRepository(prisma)

    record = await repository.archive_tier_version("ver-1")

    assert record is not None
    assert record.status == "archived"
    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    tx = prisma.tx_clients[0]
    assert "tier_version_id IS NULL" in tx.calls[3][0]
    assert "ends_at IS NULL OR ends_at > NOW()" in tx.calls[3][0]
    assert "tier_version_id = $1" in tx.calls[4][0]
    assert "ends_at IS NULL OR ends_at > NOW()" in tx.calls[4][0]
    assert "SET status = 'archived'" in tx.calls[5][0]


@pytest.mark.asyncio
async def test_archive_tier_version_rejects_active_version_with_pinned_assignments() -> None:
    prisma = _FakePrisma(
        enable_tx=True,
        version_lookup_status="active",
        pinned_assignment_count=1,
    )
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="pinned"):
        await repository.archive_tier_version("ver-1")

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert "FOR UPDATE OF v" not in tx.calls[0][0]
    assert "FROM deltallm_tier" in tx.calls[1][0]
    assert "FOR UPDATE OF v" in tx.calls[2][0]
    assert "tier_version_id IS NULL" in tx.calls[3][0]
    assert "ends_at IS NULL OR ends_at > NOW()" in tx.calls[3][0]
    assert "tier_version_id = $1" in tx.calls[4][0]
    assert "ends_at IS NULL OR ends_at > NOW()" in tx.calls[4][0]
    assert all("SET status = 'archived'" not in sql for sql, _ in tx.calls)
