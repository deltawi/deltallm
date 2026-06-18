from __future__ import annotations

import json

import pytest

from src.db.tiers import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierRepository,
)

from tests.db.tier_repository_fakes import _FakePrisma


@pytest.mark.asyncio
async def test_replace_model_policies_deletes_then_inserts_serialized_rows() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)
    policy = TierModelPolicyRecord(
        tier_model_policy_id="",
        tier_version_id="ignored",
        callable_key="openai/gpt-4.1",
        enabled=True,
        access_mode="allow",
        rpm_limit=100,
        tpm_limit=10_000,
        pricing={"input_cost_per_token": 0.001},
        capacity_pool_key="shared",
        priority=50,
        metadata={"region": "us"},
    )

    records = await repository.replace_model_policies("ver-1", [policy])

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    tx = prisma.tx_clients[0]
    assert "DELETE FROM deltallm_tiermodelpolicy" in tx.executions[0][0]
    assert tx.executions[0][1] == ("ver-1",)
    assert len(records) == 1
    assert records[0].tier_version_id == "ver-1"
    assert records[0].callable_key == "openai/gpt-4.1"
    assert records[0].pricing == {"input_cost_per_token": 0.001}
    assert records[0].metadata == {"region": "us"}
    assert "FROM deltallm_tierversion" in tx.calls[0][0]
    assert "FOR UPDATE" in tx.calls[0][0]
    params = tx.calls[1][1]
    assert params[:4] == ("ver-1", "openai/gpt-4.1", True, "allow")
    assert json.loads(str(params[12])) == {"input_cost_per_token": 0.001}
    assert json.loads(str(params[15])) == {"region": "us"}


@pytest.mark.asyncio
async def test_replace_model_policies_uses_transaction_client_when_available() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)
    policy = TierModelPolicyRecord(
        tier_model_policy_id="",
        tier_version_id="ignored",
        callable_key="openai/gpt-4.1",
    )

    records = await repository.replace_model_policies("ver-1", [policy])

    assert len(records) == 1
    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    tx = prisma.tx_clients[0]
    assert "FROM deltallm_tierversion" in tx.calls[0][0]
    assert "DELETE FROM deltallm_tiermodelpolicy" in tx.executions[0][0]
    assert "INSERT INTO deltallm_tiermodelpolicy" in tx.calls[1][0]


@pytest.mark.asyncio
async def test_replace_model_policies_rejects_non_draft_version_without_delete() -> None:
    prisma = _FakePrisma(enable_tx=True, mutation_version_status="active")
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="draft"):
        await repository.replace_model_policies("ver-1", [])

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 1
    assert "FROM deltallm_tierversion" in tx.calls[0][0]
    assert tx.executions == []


@pytest.mark.asyncio
async def test_replace_model_policies_requires_transaction_support() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="replace_model_policies"):
        await repository.replace_model_policies("ver-1", [])

    assert prisma.calls == []
    assert prisma.executions == []


@pytest.mark.asyncio
async def test_replace_capacity_pools_deletes_then_inserts_serialized_rows() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)
    pool = TierCapacityPoolRecord(
        tier_capacity_pool_id="",
        tier_version_id="ignored",
        pool_key="shared",
        callable_key="openai/gpt-4.1",
        rpm_capacity=1_000,
        tpm_capacity=500_000,
        max_parallel_requests=25,
        strategy="weighted_fair",
        saturation_threshold=0.8,
        burst_multiplier=1.5,
        metadata={"region": "us"},
    )

    records = await repository.replace_capacity_pools("ver-1", [pool])

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    tx = prisma.tx_clients[0]
    assert "DELETE FROM deltallm_tiercapacitypool" in tx.executions[0][0]
    assert tx.executions[0][1] == ("ver-1",)
    assert len(records) == 1
    assert records[0].tier_version_id == "ver-1"
    assert records[0].pool_key == "shared"
    assert records[0].strategy == "weighted_fair"
    assert records[0].metadata == {"region": "us"}
    assert "FROM deltallm_tierversion" in tx.calls[0][0]
    assert "FOR UPDATE" in tx.calls[0][0]
    params = tx.calls[1][1]
    assert params[:4] == ("ver-1", "shared", "openai/gpt-4.1", 1_000)
    assert params[7] == 0.8
    assert params[8] == 1.5
    assert json.loads(str(params[9])) == {"region": "us"}


@pytest.mark.asyncio
async def test_replace_capacity_pools_uses_transaction_client_when_available() -> None:
    prisma = _FakePrisma(enable_tx=True)
    repository = TierRepository(prisma)
    pool = TierCapacityPoolRecord(
        tier_capacity_pool_id="",
        tier_version_id="ignored",
        pool_key="shared",
        callable_key="openai/gpt-4.1",
    )

    records = await repository.replace_capacity_pools("ver-1", [pool])

    assert len(records) == 1
    assert prisma.tx_started == 1
    assert prisma.tx_committed == 1
    assert prisma.calls == []
    tx = prisma.tx_clients[0]
    assert "FROM deltallm_tierversion" in tx.calls[0][0]
    assert "DELETE FROM deltallm_tiercapacitypool" in tx.executions[0][0]
    assert "INSERT INTO deltallm_tiercapacitypool" in tx.calls[1][0]


@pytest.mark.asyncio
async def test_replace_capacity_pools_rejects_non_draft_version_without_delete() -> None:
    prisma = _FakePrisma(enable_tx=True, mutation_version_status="archived")
    repository = TierRepository(prisma)

    with pytest.raises(ValueError, match="draft"):
        await repository.replace_capacity_pools("ver-1", [])

    assert prisma.tx_started == 1
    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1
    tx = prisma.tx_clients[0]
    assert len(tx.calls) == 1
    assert "FROM deltallm_tierversion" in tx.calls[0][0]
    assert tx.executions == []


@pytest.mark.asyncio
async def test_replace_capacity_pools_requires_transaction_support() -> None:
    prisma = _FakePrisma()
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="replace_capacity_pools"):
        await repository.replace_capacity_pools("ver-1", [])

    assert prisma.calls == []
    assert prisma.executions == []
