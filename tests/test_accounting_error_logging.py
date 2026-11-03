"""Accounting failures name a safe cause in the log and in the startup error."""

import asyncio
import logging
from uuid import uuid4

import pytest
from prisma.errors import RawQueryError

from src.billing.accounting.reporting.accounting_read_model_claims import (
    ReadModelClaim,
    ReadModelWorkerConfig,
)
from src.billing.accounting.reporting.accounting_read_model_runtime import ReadModelProcessingWorker
from src.db.accounting import accounting_calls
from src.db.accounting.accounting_calls import (
    AccountingDatabaseCalls,
    AccountingProtocolUnavailable,
    accounting_rejection_code,
    database_sqlstate,
)
from src.db.runtime.telemetry_acceptance import AcceptanceFailure
from src.telemetry.lifecycle import WorkerState


def raw_error(sqlstate: str, message: str) -> RawQueryError:
    return RawQueryError(
        {
            "user_facing_error": {
                "error_code": "P2010",
                "meta": {"code": sqlstate, "message": message},
            }
        }
    )


class FailingDatabase:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    async def query_raw(self, query, *parameters):
        self.calls += 1
        raise self.error


@pytest.fixture(autouse=True)
def clear_log_limiter():
    accounting_calls._last_logged.clear()
    yield
    accounting_calls._last_logged.clear()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            raw_error("P0001", "ERROR: accounting_read_model_attribution"),
            "accounting_read_model_attribution",
        ),
        (raw_error("P0001", "accounting_read_model_charge"), "accounting_read_model_charge"),
        # Free text from a trigger can hold customer data and is never returned.
        (raw_error("P0001", "ERROR: key sk-live-123 is not allowed"), None),
        (raw_error("P0001", "ERROR: accounting_x\nDETAIL: user@example.com"), None),
        (raw_error("42883", "ERROR: accounting_read_model_attribution"), None),
        (RuntimeError("accounting_read_model_attribution"), None),
    ],
)
def test_rejection_code_is_only_a_fixed_accounting_code(error, expected):
    assert accounting_rejection_code(error) == expected


def test_rejection_code_and_sqlstate_follow_a_wrapped_cause():
    wrapper = RuntimeError("database unavailable")
    wrapper.__cause__ = raw_error("P0001", "ERROR: accounting_read_model_payload")
    assert accounting_rejection_code(wrapper) == "accounting_read_model_payload"
    assert database_sqlstate(wrapper) == "P0001"
    assert database_sqlstate(RuntimeError("no state")) is None


async def test_failed_call_logs_safe_cause_once_per_interval(caplog):
    db = FailingDatabase(raw_error("P0001", "ERROR: accounting_read_model_attribution"))
    calls = AccountingDatabaseCalls(db, statement_budget_seconds=1.0)
    caplog.set_level(logging.WARNING, logger=accounting_calls.__name__)
    for _ in range(3):
        with pytest.raises(AccountingProtocolUnavailable) as raised:
            await calls.call(
                "project_read_model",
                "SELECT 1",
                expires_at=asyncio.get_running_loop().time() + 1,
            )
    failure = raised.value
    assert failure.reason == AcceptanceFailure.UNKNOWN.value
    assert failure.rejection == "accounting_read_model_attribution"
    assert failure.detail == "unknown:accounting_read_model_attribution"
    assert failure.__cause__ is None
    lines = [record.getMessage() for record in caplog.records]
    assert lines == [
        "Accounting database call failed: operation=project_read_model reason=unknown "
        "rejection=accounting_read_model_attribution sqlstate=P0001 error_type=RawQueryError"
    ]


async def test_failed_call_never_logs_database_text(caplog):
    db = FailingDatabase(raw_error("23505", "ERROR: duplicate key (api_key)=(sk-secret)"))
    calls = AccountingDatabaseCalls(db, statement_budget_seconds=1.0)
    caplog.set_level(logging.WARNING, logger=accounting_calls.__name__)
    with pytest.raises(AccountingProtocolUnavailable) as raised:
        await calls.call("claim", "SELECT 1", expires_at=asyncio.get_running_loop().time() + 1)
    assert raised.value.rejection is None
    assert "sk-secret" not in caplog.text
    assert "sqlstate=23505" in caplog.text


class StuckPage:
    def __init__(self, error: AccountingProtocolUnavailable) -> None:
        self.error = error
        self.page = ReadModelClaim(
            generation=7,
            worker_id="report-test",
            lease_token=uuid4(),
            accounting_partition=3,
            after_sequence=40,
            sequences=(41, 42, 57),
            source_bytes=1024,
        )

    async def initialize(self, **kwargs):
        return None

    async def claim(self, **kwargs):
        return self.page

    async def materialize(self, value, **kwargs):
        raise self.error

    async def progress(self, **kwargs):
        raise AssertionError("a failed page has no progress observation")


async def test_stuck_page_names_its_cause_in_the_log_and_startup_error(caplog):
    error = AccountingProtocolUnavailable(
        AcceptanceFailure.UNKNOWN, rejection="accounting_read_model_attribution"
    )
    owner = ReadModelProcessingWorker(
        StuckPage(error),
        ReadModelWorkerConfig(
            generation=7,
            worker_id="report-test",
            poll_seconds=0.01,
        ),
    )
    caplog.set_level(logging.WARNING)
    with pytest.raises(TimeoutError) as raised:
        await owner.start(expires_at=asyncio.get_running_loop().time() + 0.3)
    message = str(raised.value)
    assert "startup deadline" in message
    assert (
        "last failure: unknown:accounting_read_model_attribution partition=3 sequences=41-57"
        in message
    )
    failures = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("Accounting read-model processing failed")
    ]
    # Retries with the same cause do not add log lines.
    assert failures == [
        "Accounting read-model processing failed: worker=report-test "
        "unknown:accounting_read_model_attribution partition=3 sequences=41-57"
    ]
    assert owner.worker_health.state is WorkerState.FAILED
