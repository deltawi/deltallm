"""Large valid terminal records retain exact charges across bounded native calls."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.accounting_protocol import ReserveDecision
from src.db.accounting_protocol import AccountingProtocolRepository
from tests.test_accounting_permits_postgres import CountingClient
from tests.test_accounting_protocol_postgres import (
    _create_window,
    _finalization,
    _outstanding,
    _reservation,
    _settle_grants,
    _window,
    accounting_db as _accounting_db,
)

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


@pytest.mark.parametrize("grants", [False, True])
@pytest.mark.parametrize("lose_ack", [False, True])
async def test_large_completed_records_split_and_recover_without_losing_native_facts(
    accounting_db, grants, lose_ack
):
    clients, generation = accounting_db
    db = clients[0]
    window = str(uuid4())
    await _create_window(db, generation, window, limit="100")
    counted = CountingClient(db)
    service = AccountingProtocolService(
        AccountingProtocolRepository(counted, grants_enabled=grants, statement_budget_seconds=2),
        generation=generation,
        dwell_seconds=0.01,
        statement_budget_seconds=2,
        reservation_ack_budget_seconds=4,
        finalization_ack_budget_seconds=4,
    )
    items = [_reservation(generation, window) for _ in range(8)]
    terminals = [_finalization(item) for item in items]
    for terminal in terminals:
        terminal.spend_payload["data"] = "s" * 200000
        terminal.audit_envelope["data"] = "a" * 64000
    service.start()
    try:
        permits = await asyncio.gather(*(service.reserve(item) for item in items))
        assert all(item.decision is ReserveDecision.DISPATCH for item in permits)
        assert counted.calls == 1
        counted.lose_ack = lose_ack
        receipts = await asyncio.gather(*(service.finalize(item) for item in terminals))
        assert [item.operation_id for item in receipts] == [item.operation_id for item in items]
        assert counted.calls == 4 + lose_ack
        assert sum(item.replayed for item in receipts) == (3 if lose_ack else 0)
    finally:
        await service.close()
    assert service.reservations.retained_bytes == service.finalizations.retained_bytes == 0
    rows = await db.query_raw(
        "SELECT COUNT(*)::integer AS count,"
        "bool_and(length(payload_json->'spend'->>'data')=200000) AS spend,"
        "bool_and(length(audit_envelope_json->>'data')=64000) AS audit "
        "FROM deltallm_accounting_events WHERE generation=$1 AND event_type='finalized'",
        generation,
    )
    assert rows == [{"count": 8, "spend": True, "audit": True}]
    counts = await db.query_raw(
        "SELECT event_type,COUNT(*)::integer AS count FROM deltallm_accounting_events "
        "WHERE generation=$1 GROUP BY event_type ORDER BY event_type",
        generation,
    )
    assert counts == [
        {"event_type": "finalized", "count": 8},
        {"event_type": "reserved", "count": 8},
    ]
    if grants:
        assert await _settle_grants(db, generation) == 0
        assert await _window(db, window) == (Decimal(0), Decimal(32), Decimal(0))
        assert await _outstanding(db, generation) == 32
        await db.execute_raw(
            "UPDATE deltallm_accounting_grants SET expires_at=clock_timestamp() "
            "WHERE generation=$1 AND state='active'",
            generation,
        )
        assert await _settle_grants(db, generation) == 1
    assert await _window(db, window) == (Decimal("4.8"), Decimal(0), Decimal(0))
    assert await _outstanding(db, generation) == 0
