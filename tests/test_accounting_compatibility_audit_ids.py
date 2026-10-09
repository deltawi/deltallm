"""Compatibility reporting must accept the durable recovery event keys."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.billing.accounting.reporting.accounting_projection import (
    AccountingCompatibilityProjector,
    AccountingProjectionConfig,
)
from src.db.accounting_projection import AccountingProjectionEvent


@pytest.mark.parametrize("suffix", ["expired:v2", "reconciled:v2"])
@pytest.mark.parametrize(
    "candidate", [None, "source-audit-key", "00000000-0000-0000-0000-000000000002"]
)
async def test_recovery_audit_keys_project_once_and_keep_existing_uuid_identity(suffix, candidate):
    source_id = "00000000-0000-0000-0000-000000000001:" + suffix
    audit = MagicMock()
    audit.enqueue_bundle = AsyncMock(return_value=SimpleNamespace(statuses={"fixture": "accepted"}))
    spend = MagicMock()
    spend.log_batch_once = AsyncMock()
    projector = AccountingCompatibilityProjector(
        spend=spend,
        audit=audit,
        config=AccountingProjectionConfig(generation=7, worker_id="audit-recovery"),
    )
    event = AccountingProjectionEvent(
        sequence=1,
        event_id=source_id,
        operation_id="00000000-0000-0000-0000-000000000001",
        accounting_partition=0,
        outcome="uncertain",
        payload={},
        audit_envelope={
            "event_id": candidate,
            "payload": {"event": {"action": "RECOVERY"}},
            "redacted_payload": {"event": {"action": "RECOVERY"}},
        },
        occurred_at=datetime.now(UTC),
    )
    await projector.project(event)
    await projector.project(event)
    identifiers = [
        call.kwargs["envelopes"][0].event_id for call in audit.enqueue_bundle.await_args_list
    ]
    assert len(identifiers) == 2 and identifiers[0] == identifiers[1]
    assert str(UUID(identifiers[0])) == identifiers[0]
    if candidate == "00000000-0000-0000-0000-000000000002":
        assert identifiers[0] == candidate
    else:
        # Golden identities computed by PostgreSQL's native audit expression.
        assert (
            identifiers[0]
            == {
                "expired:v2": "8aedfdae-849c-60f2-838b-50f5f958a0fc",
                "reconciled:v2": "55a329d7-7230-0654-4e6b-f28ca6191f2b",
            }[suffix]
        )
    spend.log_batch_once.assert_not_awaited()
