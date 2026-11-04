"""Idempotent startup statements wait out a briefly busy database."""

import asyncio

import pytest

from src.db.accounting.accounting_calls import (
    AccountingProtocolUnavailable,
    AccountingResultFailure,
    retry_idempotent_startup_call,
)
from src.db.runtime.telemetry_acceptance import AcceptanceFailure


@pytest.fixture(autouse=True)
def short_pauses(monkeypatch):
    monkeypatch.setattr("src.db.accounting.accounting_calls.STARTUP_RETRY_PAUSE_SECONDS", 0.001)
    monkeypatch.setattr("src.db.accounting.accounting_calls.STARTUP_RETRY_MAX_PAUSE_SECONDS", 0.002)


@pytest.mark.parametrize(
    "reason",
    [AcceptanceFailure.DEADLINE, AcceptanceFailure.POOL_TIMEOUT, AcceptanceFailure.CONNECTION],
)
async def test_a_busy_database_is_waited_for_until_the_statement_succeeds(reason):
    calls = []

    async def call():
        calls.append(None)
        if len(calls) < 3:
            raise AccountingProtocolUnavailable(reason)
        return "ready"

    deadline = asyncio.get_running_loop().time() + 1
    assert await retry_idempotent_startup_call(call, expires_at=deadline) == "ready"
    assert len(calls) == 3


@pytest.mark.parametrize(
    "failure",
    [
        AccountingProtocolUnavailable(AcceptanceFailure.INVALID_INPUT),
        AccountingProtocolUnavailable(AccountingResultFailure.INVALID_RESULT),
        AccountingProtocolUnavailable(
            AcceptanceFailure.DATABASE_UNAVAILABLE, rejection="accounting_protocol_missing"
        ),
    ],
)
async def test_a_rejected_or_invalid_statement_is_raised_at_once(failure):
    calls = []

    async def call():
        calls.append(None)
        raise failure

    with pytest.raises(AccountingProtocolUnavailable):
        await retry_idempotent_startup_call(call, expires_at=asyncio.get_running_loop().time() + 1)
    assert len(calls) == 1


async def test_the_startup_deadline_bounds_the_wait():
    calls = []

    async def call():
        calls.append(None)
        raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)

    started = asyncio.get_running_loop().time()
    with pytest.raises(AccountingProtocolUnavailable):
        await retry_idempotent_startup_call(call, expires_at=started + 0.05)
    assert asyncio.get_running_loop().time() - started < 0.5
    assert len(calls) >= 2
