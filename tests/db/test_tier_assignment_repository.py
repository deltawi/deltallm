from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from src.db.tiers import OrganizationTierAssignmentRecord, TierRepository

from tests.db.tier_repository_fakes import _FakePrisma


@pytest.mark.asyncio
async def test_upsert_org_assignment_inserts_then_fetches_joined_assignment() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)

    record = await repository.upsert_org_assignment(
        organization_id="org-1",
        tier_id="tier-1",
        tier_version_id="ver-1",
        assignment_type="primary",
        enabled=True,
        weight=1,
        metadata={"reason": "signup"},
    )

    assert isinstance(record, OrganizationTierAssignmentRecord)
    assert record.assignment_id == "assign-1"
    assert record.organization_id == "org-1"
    assert record.tier_key == "pro"
    assert record.tier_version_number == 2
    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    tx = prisma.tx_clients[0]
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR UPDATE" in tx.calls[0][0]
    assert tx.calls[0][1] == ("tier-1",)
    assert "v.tier_id AS version_tier_id" in tx.calls[1][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]
    assert tx.calls[1][1] == ("ver-1",)
    assert "pg_advisory_xact_lock" in tx.calls[2][0]
    assert tx.calls[2][1] == ("org-1",)
    assert "SELECT COUNT(*)::int AS overlap_count" in tx.calls[3][0]
    assert "INSERT INTO deltallm_organizationtierassignment" in tx.calls[4][0]
    assert tx.calls[4][1][:6] == ("org-1", "tier-1", "ver-1", "primary", True, 1)
    assert json.loads(str(tx.calls[4][1][8])) == {"reason": "signup"}
    assert "FROM deltallm_organizationtierassignment a" in tx.calls[5][0]
    assert tx.calls[5][1] == ("assign-1",)


@pytest.mark.asyncio
async def test_upsert_org_assignment_rejects_cross_tier_version() -> None:
    prisma = _FakePrisma(enable_tx=True, assignment_version_tier_id="tier-other")
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="belong to tier_id"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            tier_version_id="ver-other",
            assignment_type="primary",
            enabled=True,
        )

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 2
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR UPDATE" in tx.calls[0][0]
    assert "v.tier_id AS version_tier_id" in tx.calls[1][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]


@pytest.mark.asyncio
async def test_upsert_org_assignment_rejects_draft_version() -> None:
    prisma = _FakePrisma(enable_tx=True, assignment_version_status="draft")
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="active tier version"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            tier_version_id="ver-1",
            assignment_type="primary",
            enabled=True,
        )

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 2
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR UPDATE" in tx.calls[0][0]
    assert "v.tier_id AS version_tier_id" in tx.calls[1][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]


@pytest.mark.asyncio
async def test_upsert_org_assignment_rejects_archived_version() -> None:
    prisma = _FakePrisma(enable_tx=True, assignment_version_status="archived")
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="active tier version"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            tier_version_id="ver-1",
            assignment_type="primary",
            enabled=True,
        )

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 2
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR UPDATE" in tx.calls[0][0]
    assert "v.tier_id AS version_tier_id" in tx.calls[1][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]


@pytest.mark.asyncio
async def test_upsert_org_assignment_rejects_missing_version() -> None:
    prisma = _FakePrisma(enable_tx=True, assignment_version_tier_id=None)
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="existing tier version"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            tier_version_id="ver-missing",
            assignment_type="addon",
            enabled=True,
        )

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 2
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR UPDATE" in tx.calls[0][0]
    assert "v.tier_id AS version_tier_id" in tx.calls[1][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]


@pytest.mark.asyncio
async def test_upsert_org_assignment_rejects_unpinned_enabled_without_active_version() -> None:
    prisma = _FakePrisma(enable_tx=True, current_active_version_id=None)
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="active tier version"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            assignment_type="primary",
            enabled=True,
        )

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 2
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR UPDATE" in tx.calls[0][0]
    assert tx.calls[0][1] == ("tier-1",)
    assert "FROM deltallm_tierversion v" in tx.calls[1][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]
    assert tx.calls[1][1] == ("tier-1",)


@pytest.mark.asyncio
async def test_upsert_org_assignment_rejects_overlapping_primary_assignment() -> None:
    prisma = _FakePrisma(enable_tx=True, overlap_count=1)
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="one active primary"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            assignment_type="primary",
            enabled=True,
        )

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 4
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]
    assert "pg_advisory_xact_lock" in tx.calls[2][0]
    assert "SELECT COUNT(*)::int AS overlap_count" in tx.calls[3][0]


@pytest.mark.asyncio
async def test_upsert_org_assignment_checks_effective_window_overlap_params() -> None:
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    ends_at = datetime(2026, 8, 1, tzinfo=UTC)
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)

    await repository.upsert_org_assignment(
        assignment_id="assign-existing",
        organization_id="org-1",
        tier_id="tier-1",
        assignment_type="primary",
        enabled=True,
        starts_at=starts_at,
        ends_at=ends_at,
    )

    tx = prisma.tx_clients[0]
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]
    assert "pg_advisory_xact_lock" in tx.calls[2][0]
    assert tx.calls[3][1] == ("org-1", "assign-existing", starts_at, ends_at)


@pytest.mark.asyncio
async def test_upsert_org_assignment_requires_transaction_support_for_enabled_primary() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="upsert_org_assignment"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            assignment_type="primary",
            enabled=True,
        )

    assert prisma.calls == []
    assert prisma.executions == []


@pytest.mark.asyncio
async def test_upsert_org_assignment_requires_transaction_support_for_enabled_unpinned_addon() -> (
    None
):
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="upsert_org_assignment"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            assignment_type="addon",
            enabled=True,
        )

    assert prisma.calls == []
    assert prisma.executions == []


@pytest.mark.asyncio
async def test_upsert_org_assignment_requires_transaction_support_for_enabled_pinned_addon() -> (
    None
):
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="upsert_org_assignment"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            tier_version_id="ver-1",
            assignment_type="addon",
            enabled=True,
        )

    assert prisma.calls == []
    assert prisma.executions == []


@pytest.mark.asyncio
async def test_upsert_expired_enabled_pinned_addon_skips_active_version_preflight() -> None:
    prisma = _FakePrisma(assignment_version_status="archived")
    repository = TierRepository(prisma)

    record = await repository.upsert_org_assignment(
        organization_id="org-1",
        tier_id="tier-1",
        tier_version_id="ver-1",
        assignment_type="addon",
        enabled=True,
        ends_at=datetime.now(UTC) - timedelta(days=1),
    )

    assert record is not None
    assert prisma.tx_started == 0
    assert "INSERT INTO deltallm_organizationtierassignment" in prisma.calls[0][0]
    assert all("SELECT tier_id" not in sql for sql, _ in prisma.calls)
    assert all("v.tier_id AS version_tier_id" not in sql for sql, _ in prisma.calls)
    assert all("v.status = 'active'" not in sql for sql, _ in prisma.calls)


@pytest.mark.asyncio
async def test_upsert_expired_enabled_unpinned_addon_skips_active_version_preflight() -> None:
    prisma = _FakePrisma(current_active_version_id=None)
    repository = TierRepository(prisma)

    record = await repository.upsert_org_assignment(
        organization_id="org-1",
        tier_id="tier-1",
        tier_version_id=None,
        assignment_type="addon",
        enabled=True,
        ends_at=datetime.now(UTC) - timedelta(days=1),
    )

    assert record is not None
    assert prisma.tx_started == 0
    assert "INSERT INTO deltallm_organizationtierassignment" in prisma.calls[0][0]
    assert all("SELECT tier_id" not in sql for sql, _ in prisma.calls)
    assert all("v.tier_id AS version_tier_id" not in sql for sql, _ in prisma.calls)
    assert all("v.status = 'active'" not in sql for sql, _ in prisma.calls)


@pytest.mark.asyncio
async def test_upsert_future_enabled_pinned_addon_requires_active_version_preflight() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="upsert_org_assignment"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            tier_version_id="ver-1",
            assignment_type="addon",
            enabled=True,
            starts_at=datetime.now(UTC) + timedelta(days=1),
            ends_at=datetime.now(UTC) + timedelta(days=2),
        )

    assert prisma.calls == []
    assert prisma.executions == []


@pytest.mark.asyncio
async def test_upsert_expired_enabled_primary_still_requires_transaction_support() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="upsert_org_assignment"):
        await repository.upsert_org_assignment(
            organization_id="org-1",
            tier_id="tier-1",
            assignment_type="primary",
            enabled=True,
            ends_at=datetime.now(UTC) - timedelta(days=1),
        )

    assert prisma.calls == []
    assert prisma.executions == []


@pytest.mark.asyncio
async def test_upsert_org_assignment_uses_transaction_client_when_available() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)

    record = await repository.upsert_org_assignment(
        organization_id="org-1",
        tier_id="tier-1",
        assignment_type="primary",
        enabled=True,
    )

    assert record is not None
    assert record.tier_version_id is None
    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    tx = prisma.tx_clients[0]
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]
    assert "pg_advisory_xact_lock" in tx.calls[2][0]
    assert "SELECT COUNT(*)::int AS overlap_count" in tx.calls[3][0]
    assert "INSERT INTO deltallm_organizationtierassignment" in tx.calls[4][0]
    assert "FROM deltallm_organizationtierassignment a" in tx.calls[5][0]


@pytest.mark.asyncio
async def test_upsert_org_assignment_updates_by_assignment_id() -> None:
    prisma = _FakePrisma(current_active_version_id=None)
    repository = TierRepository(prisma)

    record = await repository.upsert_org_assignment(
        assignment_id="assign-existing",
        organization_id="org-1",
        tier_id="tier-1",
        tier_version_id=None,
        assignment_type="addon",
        enabled=False,
        weight=2,
        metadata=None,
    )

    assert record is not None
    assert record.assignment_id == "assign-existing"
    assert "UPDATE deltallm_organizationtierassignment" in prisma.calls[0][0]
    assert prisma.calls[0][1][:7] == (
        "assign-existing",
        "org-1",
        "tier-1",
        None,
        "addon",
        False,
        2,
    )
    assert "FROM deltallm_organizationtierassignment a" in prisma.calls[1][0]
    assert prisma.calls[1][1] == ("assign-existing",)


@pytest.mark.asyncio
async def test_upsert_org_assignment_skips_primary_lock_for_addon_assignment() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)

    record = await repository.upsert_org_assignment(
        organization_id="org-1",
        tier_id="tier-1",
        assignment_type="addon",
        enabled=True,
    )

    assert record is not None
    assert record.tier_version_id is None
    assert prisma.calls == []
    tx = prisma.tx_clients[0]
    assert "FROM deltallm_tier" in tx.calls[0][0]
    assert "FOR SHARE OF v" in tx.calls[1][0]
    assert "INSERT INTO deltallm_organizationtierassignment" in tx.calls[2][0]
    assert all("pg_advisory_xact_lock" not in sql for sql, _ in tx.calls)
    assert all("SELECT COUNT(*)::int AS overlap_count" not in sql for sql, _ in tx.calls)
