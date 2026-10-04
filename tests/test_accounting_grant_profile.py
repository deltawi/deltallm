from unittest.mock import AsyncMock, MagicMock

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
