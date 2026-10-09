import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from prisma.errors import TransactionExpiredError

from src.db.allocated_client import DatabaseOwner, DatabaseUnavailableError
from src.db.allocation_config import DatabasePolicy
from src.db.telemetry_acceptance import AcceptanceFailure, classify_acceptance_failure
from src.models.errors import RoutingFailureAction
from tests.test_telemetry_acceptance_metrics import (
    Database,
    assert_released,
    data_error,
    enqueue,
    operation,
    sample,
)


@pytest.mark.parametrize("queue", ["audit", "spend"])
@pytest.mark.parametrize("stage", ["acquire", "lock", "sql", "commit"])
@pytest.mark.parametrize(
    ("native_error", "reason"),
    [
        (data_error("P2024"), "pool_timeout"),
        (data_error("P1001"), "connection"),
        (data_error("P2010", "55P03"), "lock_timeout"),
        (data_error("P2010", "57014"), "statement_cancelled"),
        (httpx.PoolTimeout("private"), "client_pool_timeout"),
        (TransactionExpiredError("private"), "transaction_expired"),
        (TimeoutError("private"), "deadline_exceeded"),
    ],
)
async def test_allocated_failure_keeps_outbox_diagnostics_and_public_error(
    queue, stage, native_error, reason, caplog
):
    owner = DatabaseOwner(DatabasePolicy("telemetry", 1, 0.2, 1, 0.1, 2))
    try:
        with pytest.raises(DatabaseUnavailableError) as raised:
            await owner.query(AsyncMock(side_effect=native_error))
        failure = raised.value
        assert failure.__cause__ is native_error
        db = Database(queue, fail=stage, failure=failure)
        labels = {"queue": queue, "phase": stage, "reason": reason}
        before = sample("deltallm_telemetry_acceptance_failures_total", **labels)
        accepted = operation(queue, "accepted")
        with pytest.raises(DatabaseUnavailableError) as observed:
            await enqueue(db, queue)
        assert observed.value is failure
        assert failure.status_code == 503
        assert failure.affects_deployment_health is False
        assert failure.routing_failure_action == RoutingFailureAction.FAIL_FAST
        assert sample("deltallm_telemetry_acceptance_failures_total", **labels) == before + 1
        assert operation(queue, "accepted") == accepted
        record = next(
            r for r in caplog.records if r.message.startswith("telemetry acceptance failed")
        )
        assert record.reason == reason
        assert record.exc_info is None
        assert "private" not in repr(record.__dict__)
        assert_released(queue)
    finally:
        await owner.close()
    assert owner.gate.active == 0
    assert not owner.tasks


async def test_full_allocation_has_an_explicit_availability_classification():
    owner = DatabaseOwner(DatabasePolicy("telemetry", 1, 0.2, 1, 0.1, 2))
    operation = AsyncMock()
    await owner.acquire()
    try:
        with pytest.raises(DatabaseUnavailableError) as raised:
            await owner.query(operation)
        assert raised.value.__cause__ is None
        assert classify_acceptance_failure(raised.value) == AcceptanceFailure.DATABASE_UNAVAILABLE
        operation.assert_not_awaited()
    finally:
        await owner.release()
        await owner.close()


@pytest.mark.parametrize("shape", ["missing", "unknown", "cycle", "deep"])
def test_unresolvable_allocation_causes_remain_bounded_and_explicit(shape):
    error = DatabaseUnavailableError()
    if shape == "unknown":
        error.__cause__ = RuntimeError("P2024 private")
    elif shape == "cycle":
        cause = DatabaseUnavailableError()
        error.__cause__ = cause
        cause.__cause__ = error
    elif shape == "deep":
        cause = error
        for _ in range(32):
            cause.__cause__ = DatabaseUnavailableError()
            cause = cause.__cause__
        cause.__cause__ = data_error("P2024")
    assert classify_acceptance_failure(error) == AcceptanceFailure.DATABASE_UNAVAILABLE


def test_nested_allocation_wrapper_preserves_structured_cause():
    error = DatabaseUnavailableError()
    error.__cause__ = DatabaseUnavailableError()
    error.__cause__.__cause__ = data_error("P2010", "55P03")
    assert classify_acceptance_failure(error) == AcceptanceFailure.LOCK_TIMEOUT


def test_unrelated_exception_and_cancellation_do_not_inherit_cause_classification():
    for error, expected in [
        (RuntimeError("private"), AcceptanceFailure.UNKNOWN),
        (asyncio.CancelledError(), AcceptanceFailure.CANCELLED),
    ]:
        error.__cause__ = data_error("P2024")
        assert classify_acceptance_failure(error) == expected
