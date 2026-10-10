from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from tests.performance import accounting_grant_profile as profile
from tests.test_accounting_request_path import _AccountingRepository


async def test_profile_uses_the_same_bounded_pool_as_production(monkeypatch):
    manager = MagicMock(connect=AsyncMock(), disconnect=AsyncMock(), client=object())
    monkeypatch.setattr(profile, "AccountingPostgresManager", lambda: manager)
    repository = _AccountingRepository()
    monkeypatch.setattr(
        profile, "_ObservedAccountingRepository", lambda *args, **kwargs: repository
    )

    async def one_request(**kwargs):
        assert (await kwargs["request"](0, "profile-test")).status_code == 200
        return "observed-run"

    monkeypatch.setattr(profile, "run_constant_arrival", one_request)
    spec = profile._WorkerSpec(
        database_url="postgresql://localhost/disposable",
        mode="grants",
        generation=7,
        window_id="00000000-0000-0000-0000-000000000001",
        index=0,
        start_at_epoch=0,
        rate=50,
        duration=10,
        batch_size=8,
        dwell_ms=0,
        max_in_flight=16,
        statement_timeout=0.25,
        finalization_timeout=1,
        grant_operations=32,
        grant_ttl=30,
    )
    result = await profile._run_profile_worker(spec)
    assert result["run"] == "observed-run"
    assert len(repository.reservations) == len(repository.finalizations) == 1
    settings = manager.connect.await_args.args[0]
    assert settings.pool_size == 3
    assert manager.connect.await_args.kwargs == {
        "pool_size": 2,
        "acquisition_seconds": 0.2,
        "statement_seconds": 0.25,
        "lock_seconds": 0.2,
    }
    manager.disconnect.assert_awaited_once()


async def test_profile_permit_mode_uses_the_real_bank_and_shared_terminal_owner(monkeypatch):
    async def query(query, *parameters):
        values = json.loads(parameters[-1])
        if "allocate_permit_grants_batch" in query:
            return [
                {
                    "allocation_fence_token": value["fence_token"],
                    "decision": "dispatch",
                    "grant_id": str(uuid4()),
                    "grantee_id": f"{parameters[1]}:{value['fence_token']}",
                    "fence_token": value["fence_token"],
                    "accounting_partition": 0,
                    "allowance_exact": value["reservation"]["allowance"],
                    "operation_limit": value["target_operations"],
                    "expires_at": datetime.now(UTC) + timedelta(seconds=30),
                }
                for value in values
            ]
        if "claim_permits_batch" in query:
            return [
                {
                    "operation_id": value["reservation"]["operation_id"],
                    "decision": "dispatch",
                    "dispatch_token": value["reservation"]["owner_token"],
                    "accounting_partition": 0,
                }
                for value in values
            ]
        assert "finalize_grant_batch" in query
        return [
            {
                "operation_id": value["operation_id"],
                "event_sequence": 1,
                "outcome": value["outcome"],
                "replayed": False,
            }
            for value in values
        ]

    client = MagicMock(query_raw=AsyncMock(side_effect=query))
    manager = MagicMock(connect=AsyncMock(), disconnect=AsyncMock(), client=client)
    monkeypatch.setattr(profile, "AccountingPostgresManager", lambda: manager)

    async def one_request(**kwargs):
        assert (await kwargs["request"](0, "profile-test")).status_code == 200
        return "observed-permit-run"

    monkeypatch.setattr(profile, "run_constant_arrival", one_request)
    spec = profile._WorkerSpec(
        database_url="postgresql://localhost/disposable",
        mode="permits",
        generation=7,
        window_id="00000000-0000-0000-0000-000000000001",
        index=0,
        start_at_epoch=0,
        rate=50,
        duration=10,
        batch_size=8,
        dwell_ms=0,
        max_in_flight=16,
        statement_timeout=0.25,
        finalization_timeout=1,
        grant_operations=32,
        grant_ttl=30,
    )
    result = await profile._run_profile_worker(spec)
    assert result["run"] == "observed-permit-run"
    assert result["database_calls"] == {
        "allocate_permit_grants": 1,
        "claim_permits": 1,
        "finalize_grant": 1,
    }
    assert client.query_raw.await_count == 3
    assert manager.connect.await_args.kwargs["pool_size"] == 2
    manager.disconnect.assert_awaited_once()
