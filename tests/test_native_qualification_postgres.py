"""Qualification SQL executes against the migrated test schema, not mocks."""

import pytest

from tests.performance.native_qualification_economics import (
    accounting_snapshot,
    native_storage_snapshot,
)
from tests.test_accounting_protocol_postgres import accounting_db as _accounting_db

pytestmark = pytest.mark.postgres
accounting_db = _accounting_db


async def test_qualification_state_and_storage_queries_use_real_schema(accounting_db):
    clients, _ = accounting_db
    state = await accounting_snapshot(clients[0])
    assert set(state) == {
        "unsettled_operations",
        "open_grants",
        "terminal_pending",
        "report_pending_partitions",
        "spend_pending",
        "audit_pending",
        "unsafe_windows",
        "facts",
        "fact_charge",
        "legacy_spend_rows",
    }
    assert isinstance(state["fact_charge"], str)
    storage = await native_storage_snapshot(clients[0])
    assert 1 <= len(storage) <= 64
    assert all(row["table_bytes"] >= 0 and row["index_bytes"] >= 0 for row in storage)
