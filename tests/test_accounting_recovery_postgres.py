"""Native recovery settles exact money without a reporting dependency."""

from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.health.accounting_health import (
    AccountingBacklogPolicy,
    AccountingBacklogProbe,
)
from src.billing.accounting.journal.accounting_recovery import (
    AccountingRecoveryWorker,
    RecoveryConfig,
)
from src.db.accounting.health.accounting_health import AccountingBacklogRepository
from src.db.accounting.accounting_recovery import AccountingRecoveryRepository
from src.db.accounting.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_local_leases_postgres import allocation, deadline, owner
from tests.test_accounting_local_runtime_postgres import processor, runtime
from tests.test_accounting_local_service import handle
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _outstanding,
    _reservation,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


def recovery(db, generation, limit=4):
    counted = CountingClient(db)
    probe = AccountingBacklogProbe(
        AccountingBacklogRepository(counted), AccountingBacklogPolicy(generation=generation)
    )
    return AccountingRecoveryWorker(
        AccountingRecoveryRepository(counted),
        probe,
        RecoveryConfig(generation=generation, batch_size=limit),
    ), counted


async def test_empty_recovery_requires_five_native_calls_and_keeps_truth(accounting_db):
    clients, generation = accounting_db
    worker, counted = recovery(clients[0], generation)
    assert await worker.run_once(expires_at=deadline()) == 0
    assert counted.calls == 5 and worker.probe.snapshot.sampled_drained
    assert await _outstanding(clients[0], generation) == 0


@pytest.mark.parametrize("materialized", [False, True])
async def test_journal_acceptance_is_not_settlement_until_materialized(accounting_db, materialized):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    local, *_ = runtime(db, generation)
    await local.start(expires_at=deadline())
    operation = handle(await local.service.reserve(_reservation(generation, window)))
    await local.service.finalize_operation(operation, _finalization(operation.reservation))
    assert await local.close(expires_at=deadline())
    if materialized:
        processing = processor(clients[1], generation)
        assert await processing.run_once(expires_at=deadline()) == 1
        assert await processing.close(expires_at=deadline())
    worker, counted = recovery(db, generation)
    assert await worker.run_once(expires_at=deadline()) == int(materialized)
    assert counted.calls == 5
    assert worker.probe.snapshot.sampled_drained is materialized
    expected = (
        (Decimal("0.6"), Decimal(0), Decimal(0))
        if materialized
        else (Decimal(0), Decimal(4), Decimal(0))
    )
    assert await _window(db, window) == expected
    assert await worker.run_once(expires_at=deadline()) == 0
    assert await _window(db, window) == expected


async def test_owner_loss_recovers_conservatively_and_never_releases_unknown_charge(accounting_db):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window)
    item = _reservation(generation, window)
    grant = (await owner(db).allocate_batch([allocation(item)], expires_at=deadline()))[0]
    await db.execute_raw(
        "UPDATE deltallm_accounting_grants SET "
        "dispatch_expires_at=NOW()-INTERVAL '2 seconds',"
        "expires_at=NOW()-INTERVAL '1 second' WHERE grant_id=$1",
        grant.grant_id,
    )
    worker, _ = recovery(db, generation)
    assert await worker.run_once(expires_at=deadline()) == 1
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(4))
    assert await _outstanding(db, generation) == 0
    assert await worker.run_once(expires_at=deadline()) == 0
    assert await _window(db, window) == (Decimal(0), Decimal(0), Decimal(4))


async def test_missing_generation_is_unavailable_not_empty_ready(accounting_db):
    clients, generation = accounting_db
    worker, _ = recovery(clients[0], generation + 1)
    with pytest.raises(AccountingProtocolUnavailable):
        await worker.run_once(expires_at=deadline())
    assert not worker.probe.worker_health.ready and worker.probe.snapshot is None
