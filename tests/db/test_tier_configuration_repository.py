from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.db.tiers import (
    TierActivationActiveVersionChangedError,
    TierActivationConfigurationChangedError,
    TierBootstrapIdempotencyConflictError,
    TierCapacityPoolRecord,
    TierConfigurationChildNotFoundError,
    TierConfigurationIdentityImmutableError,
    TierConfigurationPoolInUseError,
    TierConfigurationPoolReferenceError,
    TierConfigurationStaleError,
    TierConfigurationVersionNotDraftError,
    TierConfigurationVersionNotFoundError,
    TierModelPolicyRecord,
    TierRepository,
)


class _ScriptedPrisma:
    def __init__(self, responses: list[list[dict[str, object]] | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.tx_clients: list[_ScriptedPrisma] = []
        self.tx_started = 0
        self.tx_committed = 0
        self.tx_rolled_back = 0

    async def query_raw(self, sql: str, *params: object) -> list[dict[str, object]]:
        self.calls.append((sql, params))
        if not self.responses:
            raise AssertionError(f"unexpected query: {sql}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def execute_raw(self, sql: str, *params: object) -> int:
        self.executions.append((sql, params))
        return 1

    def tx(self) -> _ScriptedTransaction:
        return _ScriptedTransaction(self)


class _ScriptedTransaction:
    def __init__(self, root: _ScriptedPrisma) -> None:
        self.root = root
        self.client: _ScriptedPrisma | None = None

    async def __aenter__(self) -> _ScriptedPrisma:
        self.root.tx_started += 1
        self.client = _ScriptedPrisma(self.root.responses)
        self.root.tx_clients.append(self.client)
        return self.client

    async def __aexit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        del exc, tb
        if exc_type is None:
            self.root.tx_committed += 1
        else:
            self.root.tx_rolled_back += 1
        return False


def _version_row(*, status: str = "draft", revision: int = 4) -> dict[str, object]:
    return {
        "tier_version_id": "ver-1",
        "tier_id": "tier-1",
        "version_number": 2,
        "status": status,
        "configuration_revision": revision,
        "published_at": None,
        "published_by_account_id": None,
        "created_by_account_id": "acct-1",
        "created_by_kind": "account",
        "source_tier_version_id": "ver-live",
        "metadata": None,
        "created_at": "2026-08-15T08:00:00Z",
        "updated_at": "2026-08-15T08:00:00Z",
    }


def _tier_row() -> dict[str, object]:
    return {
        "tier_id": "tier-new",
        "tier_key": "enterprise",
        "name": "Enterprise",
        "description": "Production package",
        "enabled": True,
        "metadata": {"segment": "enterprise"},
        "active_version_id": None,
        "version_count": 0,
        "assignment_count": 0,
        "created_at": "2026-08-15T08:00:00Z",
        "updated_at": "2026-08-15T08:00:00Z",
    }


def _policy_row() -> dict[str, object]:
    return {
        "tier_model_policy_id": "policy-1",
        "tier_version_id": "ver-1",
        "callable_key": "openai/gpt-4.1",
        "enabled": True,
        "access_mode": "allow",
        "rpm_limit": 100,
        "tpm_limit": 10_000,
        "rph_limit": None,
        "rpd_limit": None,
        "tpd_limit": None,
        "max_parallel_requests": 5,
        "batch_rpm_limit": None,
        "batch_tpm_limit": None,
        "pricing": {"input_cost_per_token": 0.001},
        "capacity_pool_key": "shared",
        "priority": 10,
        "metadata": {"region": "us"},
        "created_at": "2026-08-15T08:00:00Z",
        "updated_at": "2026-08-15T08:01:00Z",
    }


def _policy(*, callable_key: str = "openai/gpt-4.1") -> TierModelPolicyRecord:
    return TierModelPolicyRecord(
        tier_model_policy_id="policy-1",
        tier_version_id="ver-1",
        callable_key=callable_key,
        enabled=True,
        access_mode="allow",
        rpm_limit=100,
        tpm_limit=10_000,
        max_parallel_requests=5,
        pricing={"input_cost_per_token": 0.001},
        capacity_pool_key="shared",
        priority=10,
        metadata={"region": "us"},
    )


def _pool_row() -> dict[str, object]:
    return {
        "tier_capacity_pool_id": "pool-1",
        "tier_version_id": "ver-1",
        "pool_key": "shared",
        "callable_key": "openai/gpt-4.1",
        "rpm_capacity": 1_000,
        "tpm_capacity": 500_000,
        "max_parallel_requests": 25,
        "strategy": "weighted_fair",
        "saturation_threshold": 0.8,
        "burst_multiplier": 1.5,
        "metadata": {"region": "us"},
        "created_at": "2026-08-15T08:00:00Z",
        "updated_at": "2026-08-15T08:01:00Z",
    }


def _pool(
    *,
    pool_key: str = "shared",
    callable_key: str = "openai/gpt-4.1",
) -> TierCapacityPoolRecord:
    return TierCapacityPoolRecord(
        tier_capacity_pool_id="pool-1",
        tier_version_id="ver-1",
        pool_key=pool_key,
        callable_key=callable_key,
        rpm_capacity=1_000,
        tpm_capacity=500_000,
        max_parallel_requests=25,
        strategy="weighted_fair",
        saturation_threshold=0.8,
        burst_multiplier=1.5,
        metadata={"region": "us"},
    )


def _revision_row(revision: int = 5) -> dict[str, object]:
    return {
        "configuration_revision": revision,
        "updated_at": "2026-08-15T08:02:00Z",
    }


@pytest.mark.asyncio
async def test_model_policy_page_uses_server_filters_stable_order_and_revision() -> None:
    prisma = _ScriptedPrisma(
        [
            [{"configuration_revision": 4, "updated_at": "2026-08-15T08:00:00Z"}],
            [{"total": 13}],
            [_policy_row()],
        ]
    )
    repository = TierRepository(prisma)

    page = await repository.list_model_policies_page(
        tier_id="tier-1",
        tier_version_id="ver-1",
        search="gpt",
        enabled=True,
        access_mode="allow",
        capacity_pool_key="shared",
        sort="updated_at",
        order="desc",
        limit=10,
        offset=10,
    )

    assert page is not None
    assert page.total == 13
    assert page.configuration_revision == 4
    assert [record.tier_model_policy_id for record in page.records] == ["policy-1"]
    assert len(prisma.calls) == 3
    assert "tier_version_id = $1" in prisma.calls[0][0]
    assert "tier_id = $2" in prisma.calls[0][0]
    assert "p.callable_key ILIKE $2" in prisma.calls[1][0]
    assert "p.enabled = $3" in prisma.calls[1][0]
    assert "p.access_mode = $4" in prisma.calls[1][0]
    assert "p.capacity_pool_key = $5" in prisma.calls[1][0]
    assert "ORDER BY p.updated_at DESC, p.tier_model_policy_id ASC" in prisma.calls[2][0]
    assert prisma.calls[2][1][-2:] == (10, 10)


@pytest.mark.asyncio
async def test_capacity_pool_page_uses_server_filters_stable_order_and_revision() -> None:
    prisma = _ScriptedPrisma(
        [
            [{"configuration_revision": 7, "updated_at": "2026-08-15T08:00:00Z"}],
            [{"total": 1}],
            [_pool_row()],
        ]
    )
    repository = TierRepository(prisma)

    page = await repository.list_capacity_pools_page(
        tier_id="tier-1",
        tier_version_id="ver-1",
        search="shared",
        strategy="weighted_fair",
        sort="callable_key",
        order="asc",
        limit=25,
        offset=0,
    )

    assert page is not None
    assert page.total == 1
    assert page.configuration_revision == 7
    assert page.records[0].tier_capacity_pool_id == "pool-1"
    assert "p.strategy = $3" in prisma.calls[1][0]
    assert "ORDER BY p.callable_key ASC, p.tier_capacity_pool_id ASC" in prisma.calls[2][0]
    assert prisma.calls[2][1][-2:] == (25, 0)


@pytest.mark.asyncio
async def test_version_page_filters_status_and_uses_stable_descending_order() -> None:
    prisma = _ScriptedPrisma([[{"total": 11}], [_version_row(status="archived")]])
    repository = TierRepository(prisma)

    records, total = await repository.list_tier_versions_page(
        "tier-1",
        statuses=("archived",),
        limit=10,
        offset=10,
    )

    assert total == 11
    assert len(records) == 1
    assert "v.status IN ($2)" in prisma.calls[0][0]
    assert "ORDER BY v.version_number DESC, v.tier_version_id ASC" in prisma.calls[1][0]
    assert prisma.calls[1][1] == ("tier-1", "archived", 10, 10)


@pytest.mark.asyncio
async def test_tier_bootstrap_locks_key_before_lookup_and_creates_atomically() -> None:
    initial_version = {
        **_version_row(revision=0),
        "tier_id": "tier-new",
        "version_number": 1,
        "created_by_account_id": "acct-1",
        "source_tier_version_id": None,
    }
    prisma = _ScriptedPrisma(
        [
            [{"locked": None}],
            [],
            [_tier_row()],
            [initial_version],
            [{"tier_creation_request_id": "request-1"}],
        ]
    )
    repository = TierRepository(prisma)

    result = await repository.create_tier_with_initial_draft(
        principal_scope="account:acct-1",
        idempotency_key="request-key-1",
        request_hash="hash-1",
        tier_key="enterprise",
        name="Enterprise",
        description="Production package",
        enabled=True,
        metadata={"segment": "enterprise"},
        created_by_account_id="acct-1",
        created_by_kind="account",
    )

    assert result.idempotency_resolution == "created"
    assert result.tier.tier_id == "tier-new"
    assert result.tier.version_count == 1
    assert result.initial_version.version_number == 1
    assert result.initial_version.configuration_revision == 0
    assert prisma.tx_committed == 1
    calls = prisma.tx_clients[0].calls
    assert len(calls) == 5
    assert "pg_advisory_xact_lock(hashtextextended($1, 0))" in calls[0][0]
    assert calls[0][1] == (
        "tier-bootstrap:v1:account:acct-1:request-key-1",
    )
    assert "FROM deltallm_tiercreationrequest" in calls[1][0]
    assert "INSERT INTO deltallm_tier (" in calls[2][0]
    assert "INSERT INTO deltallm_tierversion" in calls[3][0]
    assert "INSERT INTO deltallm_tiercreationrequest" in calls[4][0]


@pytest.mark.asyncio
async def test_tier_bootstrap_replays_same_principal_key_and_hash() -> None:
    replay_row = {
        "tier_creation_request_id": "request-1",
        "principal_scope": "account:acct-1",
        "idempotency_key": "request-key-1",
        "request_hash": "hash-1",
        "tier_id": "tier-new",
        "created_at": "2026-08-15T08:00:00Z",
    }
    initial_version = {
        **_version_row(revision=0),
        "tier_id": "tier-new",
        "version_number": 1,
    }
    replay_tier = {**_tier_row(), "version_count": 1}
    prisma = _ScriptedPrisma(
        [[{"locked": None}], [replay_row], [replay_tier], [initial_version]]
    )
    repository = TierRepository(prisma)

    result = await repository.create_tier_with_initial_draft(
        principal_scope="account:acct-1",
        idempotency_key="request-key-1",
        request_hash="hash-1",
        tier_key="ignored-on-replay",
        name="Ignored on replay",
        description=None,
        enabled=False,
        metadata=None,
        created_by_account_id="acct-1",
        created_by_kind="account",
    )

    assert result.idempotency_resolution == "replayed"
    assert result.tier.tier_id == "tier-new"
    assert result.initial_version.version_number == 1
    calls = prisma.tx_clients[0].calls
    assert len(calls) == 4
    assert all("INSERT INTO" not in sql for sql, _ in calls)


@pytest.mark.asyncio
async def test_tier_bootstrap_rejects_mismatched_replay_before_resource_insert() -> None:
    replay_row = {
        "tier_creation_request_id": "request-1",
        "principal_scope": "master_key",
        "idempotency_key": "request-key-1",
        "request_hash": "old-hash",
        "tier_id": "tier-existing",
        "created_at": None,
    }
    prisma = _ScriptedPrisma([[{"locked": None}], [replay_row]])
    repository = TierRepository(prisma)

    with pytest.raises(TierBootstrapIdempotencyConflictError):
        await repository.create_tier_with_initial_draft(
            principal_scope="master_key",
            idempotency_key="request-key-1",
            request_hash="new-hash",
            tier_key="enterprise",
            name="Enterprise",
            description=None,
            enabled=True,
            metadata=None,
            created_by_account_id=None,
            created_by_kind="master_key",
        )

    assert prisma.tx_rolled_back == 1
    calls = prisma.tx_clients[0].calls
    assert len(calls) == 2
    assert all("INSERT INTO" not in sql for sql, _ in calls)


@pytest.mark.asyncio
async def test_tier_bootstrap_rolls_back_resources_when_request_record_fails() -> None:
    initial_version = {
        **_version_row(revision=0),
        "tier_id": "tier-new",
        "version_number": 1,
    }
    prisma = _ScriptedPrisma(
        [
            [{"locked": None}],
            [],
            [_tier_row()],
            [initial_version],
            RuntimeError("request insert failed"),
        ]
    )
    repository = TierRepository(prisma)

    with pytest.raises(RuntimeError, match="request insert failed"):
        await repository.create_tier_with_initial_draft(
            principal_scope="master_key",
            idempotency_key="request-key-1",
            request_hash="hash-1",
            tier_key="enterprise",
            name="Enterprise",
            description=None,
            enabled=True,
            metadata=None,
            created_by_account_id=None,
            created_by_kind="master_key",
        )

    assert prisma.tx_committed == 0
    assert prisma.tx_rolled_back == 1


@pytest.mark.asyncio
async def test_create_next_tier_version_locks_tier_before_allocating_number() -> None:
    created_row = {
        **_version_row(revision=0),
        "version_number": 5,
        "created_by_account_id": "acct-2",
        "source_tier_version_id": None,
    }
    prisma = _ScriptedPrisma([[{"tier_id": "tier-1"}], [created_row]])
    repository = TierRepository(prisma)

    created = await repository.create_next_tier_version(
        tier_id="tier-1",
        created_by_account_id="acct-2",
        created_by_kind="account",
        metadata={"reason": "edit-live"},
    )

    assert created is not None
    assert created.version_number == 5
    assert created.configuration_revision == 0
    assert created.created_by_account_id == "acct-2"
    assert prisma.tx_committed == 1
    calls = prisma.tx_clients[0].calls
    assert len(calls) == 2
    assert "FROM deltallm_tier" in calls[0][0]
    assert "FOR UPDATE" in calls[0][0]
    assert "COALESCE(MAX(version_number), 0) + 1" in calls[1][0]
    assert calls[1][1][:3] == ("tier-1", "acct-2", "account")


@pytest.mark.asyncio
async def test_guarded_activation_rechecks_revision_and_active_version_under_lock() -> None:
    activated_row = {
        **_version_row(status="active", revision=4),
        "published_by_account_id": "acct-1",
    }
    prisma = _ScriptedPrisma(
        [
            [{"tier_id": "tier-1"}],
            [_version_row(revision=4)],
            [{"tier_version_id": "ver-live"}],
            [{"assignment_count": 0}],
            [activated_row],
        ]
    )
    repository = TierRepository(prisma)

    activated = await repository.activate_tier_version(
        tier_id="tier-1",
        tier_version_id="ver-1",
        expected_revision=4,
        expected_active_version_id="ver-live",
        published_by_account_id="acct-1",
    )

    assert activated is not None
    assert activated.status == "active"
    assert activated.configuration_revision == 4
    calls = prisma.tx_clients[0].calls
    assert "FROM deltallm_tier" in calls[0][0]
    assert "FOR UPDATE" in calls[0][0]
    assert "FOR UPDATE OF v" in calls[1][0]
    assert "v.status = 'active'" in calls[2][0]
    assert calls[2][1] == ("tier-1", "ver-1")
    assert "tier_version_id = $1" in calls[3][0]
    assert "SET status = 'active'" in calls[4][0]
    executions = prisma.tx_clients[0].executions
    assert len(executions) == 1
    assert "SET status = 'archived'" in executions[0][0]


@pytest.mark.asyncio
async def test_guarded_activation_rejects_changed_revision_before_lifecycle_mutation() -> None:
    prisma = _ScriptedPrisma(
        [[{"tier_id": "tier-1"}], [_version_row(revision=5)]]
    )
    repository = TierRepository(prisma)

    with pytest.raises(TierActivationConfigurationChangedError) as caught:
        await repository.activate_tier_version(
            tier_id="tier-1",
            tier_version_id="ver-1",
            expected_revision=4,
            expected_active_version_id="ver-live",
        )

    assert caught.value.current_revision == 5
    assert prisma.tx_rolled_back == 1
    assert len(prisma.tx_clients[0].calls) == 2
    assert prisma.tx_clients[0].executions == []


@pytest.mark.asyncio
async def test_guarded_activation_rejects_changed_active_version_before_mutation() -> None:
    prisma = _ScriptedPrisma(
        [
            [{"tier_id": "tier-1"}],
            [_version_row(revision=4)],
            [{"tier_version_id": "ver-new-live"}],
        ]
    )
    repository = TierRepository(prisma)

    with pytest.raises(TierActivationActiveVersionChangedError) as caught:
        await repository.activate_tier_version(
            tier_id="tier-1",
            tier_version_id="ver-1",
            expected_revision=4,
            expected_active_version_id="ver-live",
        )

    assert caught.value.current_active_version_id == "ver-new-live"
    assert prisma.tx_rolled_back == 1
    assert len(prisma.tx_clients[0].calls) == 3
    assert prisma.tx_clients[0].executions == []


@pytest.mark.asyncio
async def test_configuration_guard_scopes_and_locks_the_path_version() -> None:
    prisma = _ScriptedPrisma([[_version_row()]])
    repository = TierRepository(prisma)

    version = await repository.lock_draft_version_for_configuration_mutation(
        tier_id="tier-1",
        tier_version_id="ver-1",
        expected_revision=4,
    )

    assert version.configuration_revision == 4
    sql, params = prisma.calls[0]
    assert "v.tier_version_id = $1" in sql
    assert "v.tier_id = $2" in sql
    assert "FOR UPDATE" in sql
    assert params == ("ver-1", "tier-1")


@pytest.mark.asyncio
async def test_configuration_guard_rejects_wrong_scope_without_mutation() -> None:
    prisma = _ScriptedPrisma([[]])
    repository = TierRepository(prisma)

    with pytest.raises(TierConfigurationVersionNotFoundError):
        await repository.lock_draft_version_for_configuration_mutation(
            tier_id="tier-other",
            tier_version_id="ver-1",
            expected_revision=4,
        )

    assert len(prisma.calls) == 1


@pytest.mark.asyncio
async def test_configuration_guard_rejects_non_draft_and_stale_versions() -> None:
    non_draft = TierRepository(_ScriptedPrisma([[_version_row(status="active")]]))
    with pytest.raises(TierConfigurationVersionNotDraftError):
        await non_draft.lock_draft_version_for_configuration_mutation(
            tier_id="tier-1",
            tier_version_id="ver-1",
            expected_revision=4,
        )

    stale_prisma = _ScriptedPrisma([[_version_row(revision=5)]])
    stale = TierRepository(stale_prisma)
    with pytest.raises(TierConfigurationStaleError) as caught:
        await stale.lock_draft_version_for_configuration_mutation(
            tier_id="tier-1",
            tier_version_id="ver-1",
            expected_revision=4,
        )
    assert caught.value.expected_revision == 4
    assert caught.value.current_revision == 5
    assert len(stale_prisma.calls) == 1


@pytest.mark.parametrize("expected_revision", [-1, True, 1.5])
@pytest.mark.asyncio
async def test_configuration_guard_validates_expected_revision(expected_revision: object) -> None:
    repository = TierRepository(_ScriptedPrisma([]))

    with pytest.raises(ValueError, match="expected_revision"):
        await repository.lock_draft_version_for_configuration_mutation(
            tier_id="tier-1",
            tier_version_id="ver-1",
            expected_revision=expected_revision,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_create_model_policy_validates_pool_and_bumps_revision_once() -> None:
    prisma = _ScriptedPrisma(
        [
            [_version_row()],
            [{"tier_capacity_pool_id": "pool-1"}],
            [_policy_row()],
            [_revision_row()],
        ]
    )
    repository = TierRepository(prisma)

    result = await repository.create_model_policy(
        tier_id="tier-1",
        tier_version_id="ver-1",
        expected_revision=4,
        policy=_policy(),
    )

    assert result.policy is not None
    assert result.policy.tier_model_policy_id == "policy-1"
    assert result.configuration_revision == 5
    assert result.version_updated_at == datetime(2026, 8, 15, 8, 2, tzinfo=UTC)
    assert prisma.tx_committed == 1
    calls = prisma.tx_clients[0].calls
    assert len(calls) == 4
    assert "tier_version_id = $1" in calls[1][0]
    assert calls[1][1] == ("ver-1", "shared", "openai/gpt-4.1")
    assert "INSERT INTO deltallm_tiermodelpolicy" in calls[2][0]
    assert "configuration_revision = configuration_revision + 1" in calls[3][0]
    assert "AND tier_id = $2" in calls[3][0]
    assert calls[3][1] == ("ver-1", "tier-1")


@pytest.mark.asyncio
async def test_create_model_policy_rejects_missing_pool_without_insert_or_bump() -> None:
    prisma = _ScriptedPrisma([[_version_row()], []])
    repository = TierRepository(prisma)

    with pytest.raises(TierConfigurationPoolReferenceError):
        await repository.create_model_policy(
            tier_id="tier-1",
            tier_version_id="ver-1",
            expected_revision=4,
            policy=_policy(),
        )

    assert prisma.tx_rolled_back == 1
    assert len(prisma.tx_clients[0].calls) == 2


@pytest.mark.asyncio
async def test_update_model_policy_scopes_child_before_writing_or_bumping() -> None:
    prisma = _ScriptedPrisma([[_version_row()], []])
    repository = TierRepository(prisma)

    with pytest.raises(TierConfigurationChildNotFoundError):
        await repository.update_model_policy(
            tier_id="tier-1",
            tier_version_id="ver-1",
            tier_model_policy_id="policy-other",
            expected_revision=4,
            policy=_policy(),
        )

    assert prisma.tx_rolled_back == 1
    calls = prisma.tx_clients[0].calls
    assert len(calls) == 2
    assert "tier_model_policy_id = $1" in calls[1][0]
    assert "tier_version_id = $2" in calls[1][0]
    assert calls[1][1] == ("policy-other", "ver-1")


@pytest.mark.asyncio
async def test_update_model_policy_rejects_callable_identity_change() -> None:
    prisma = _ScriptedPrisma([[_version_row()], [{"callable_key": "openai/gpt-4.1"}]])
    repository = TierRepository(prisma)

    with pytest.raises(TierConfigurationIdentityImmutableError, match="callable_key"):
        await repository.update_model_policy(
            tier_id="tier-1",
            tier_version_id="ver-1",
            tier_model_policy_id="policy-1",
            expected_revision=4,
            policy=_policy(callable_key="anthropic/claude"),
        )

    assert len(prisma.tx_clients[0].calls) == 2
    assert prisma.tx_rolled_back == 1


@pytest.mark.asyncio
async def test_delete_model_policy_uses_compound_scope_and_bumps_once() -> None:
    prisma = _ScriptedPrisma(
        [[_version_row()], [{"tier_model_policy_id": "policy-1"}], [_revision_row()]]
    )
    repository = TierRepository(prisma)

    result = await repository.delete_model_policy(
        tier_id="tier-1",
        tier_version_id="ver-1",
        tier_model_policy_id="policy-1",
        expected_revision=4,
    )

    assert result.policy is None
    assert result.configuration_revision == 5
    calls = prisma.tx_clients[0].calls
    assert "DELETE FROM deltallm_tiermodelpolicy" in calls[1][0]
    assert "tier_version_id = $2" in calls[1][0]
    assert calls[1][1] == ("policy-1", "ver-1")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_create_capacity_pool_bumps_revision_once() -> None:
    prisma = _ScriptedPrisma([[_version_row()], [_pool_row()], [_revision_row()]])
    repository = TierRepository(prisma)

    result = await repository.create_capacity_pool(
        tier_id="tier-1",
        tier_version_id="ver-1",
        expected_revision=4,
        pool=_pool(),
    )

    assert result.pool is not None
    assert result.pool.tier_capacity_pool_id == "pool-1"
    assert result.configuration_revision == 5
    calls = prisma.tx_clients[0].calls
    assert "INSERT INTO deltallm_tiercapacitypool" in calls[1][0]
    assert "configuration_revision = configuration_revision + 1" in calls[2][0]


@pytest.mark.asyncio
async def test_update_capacity_pool_rejects_identity_change_without_bump() -> None:
    prisma = _ScriptedPrisma(
        [
            [_version_row()],
            [{"pool_key": "shared", "callable_key": "openai/gpt-4.1"}],
        ]
    )
    repository = TierRepository(prisma)

    with pytest.raises(TierConfigurationIdentityImmutableError):
        await repository.update_capacity_pool(
            tier_id="tier-1",
            tier_version_id="ver-1",
            tier_capacity_pool_id="pool-1",
            expected_revision=4,
            pool=_pool(pool_key="renamed"),
        )

    calls = prisma.tx_clients[0].calls
    assert len(calls) == 2
    assert "tier_capacity_pool_id = $1" in calls[1][0]
    assert "tier_version_id = $2" in calls[1][0]
    assert prisma.tx_rolled_back == 1


@pytest.mark.asyncio
async def test_delete_capacity_pool_reports_in_use_without_delete_or_bump() -> None:
    prisma = _ScriptedPrisma(
        [
            [_version_row()],
            [{"pool_key": "shared", "callable_key": "openai/gpt-4.1"}],
            [{"tier_model_policy_id": "policy-1"}],
        ]
    )
    repository = TierRepository(prisma)

    with pytest.raises(TierConfigurationPoolInUseError):
        await repository.delete_capacity_pool(
            tier_id="tier-1",
            tier_version_id="ver-1",
            tier_capacity_pool_id="pool-1",
            expected_revision=4,
        )

    calls = prisma.tx_clients[0].calls
    assert len(calls) == 3
    assert "capacity_pool_key = $2" in calls[2][0]
    assert prisma.tx_rolled_back == 1


@pytest.mark.asyncio
async def test_delete_capacity_pool_uses_compound_scope_and_bumps_once() -> None:
    prisma = _ScriptedPrisma(
        [
            [_version_row()],
            [{"pool_key": "shared", "callable_key": "openai/gpt-4.1"}],
            [],
            [{"tier_capacity_pool_id": "pool-1"}],
            [_revision_row()],
        ]
    )
    repository = TierRepository(prisma)

    result = await repository.delete_capacity_pool(
        tier_id="tier-1",
        tier_version_id="ver-1",
        tier_capacity_pool_id="pool-1",
        expected_revision=4,
    )

    assert result.pool is None
    assert result.configuration_revision == 5
    calls = prisma.tx_clients[0].calls
    assert "DELETE FROM deltallm_tiercapacitypool" in calls[3][0]
    assert "tier_version_id = $2" in calls[3][0]
    assert calls[3][1] == ("pool-1", "ver-1")
    assert len(calls) == 5
