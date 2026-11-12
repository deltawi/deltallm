"""The accounting v2 audit event of a charge carries its token counts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from datetime import UTC, datetime

from src.billing.accounting.accounting_finalization import (
    AccountingSpendFinalizer,
    accounting_audit_envelope,
)
from src.billing.accounting.journal.accounting_terminal_preparation import (
    accounting_audit_token_counts,
    prepare_accounting_charge,
)
from tests.test_accounting_request_ids import _operation


def _audit_event(terminal) -> dict:
    return terminal.audit_envelope["payload"]["event"]


async def _finalize(payload: dict):
    operation = _operation()
    finalize = AsyncMock()
    finalizer = AccountingSpendFinalizer(SimpleNamespace(generation=7, finalize_operation=finalize))
    await finalizer.log_spend(
        operation,
        event_id=str(operation.reservation.operation_id),
        payload={"cost_exact": "0.75"} | payload,
    )
    finalize.assert_awaited_once()
    _, terminal = finalize.await_args.args
    return terminal


async def test_charge_audit_event_has_the_usage_token_counts():
    terminal = await _finalize({"usage": {"prompt_tokens": 1200, "completion_tokens": 300}})

    event = _audit_event(terminal)
    assert event["input_tokens"] == 1200
    assert event["output_tokens"] == 300
    # The redacted copy is the same event, so the counts stay without content storage.
    assert terminal.audit_envelope["redacted_payload"]["event"]["input_tokens"] == 1200


async def test_charge_audit_event_keeps_unknown_counts_unknown():
    terminal = await _finalize({})

    event = _audit_event(terminal)
    assert event["input_tokens"] is None
    assert event["output_tokens"] is None


def test_every_charge_path_gets_the_counts_from_the_shared_boundary():
    # Realtime, batch and the selector build their own envelope without counts
    # and call prepare_accounting_charge directly.
    operation = _operation()
    envelope = accounting_audit_envelope(
        operation,
        event_id=str(operation.reservation.operation_id),
        status="success",
        metadata={"batch": True},
    )
    payload = {"cost_exact": "0.1", "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

    first = prepare_accounting_charge(
        operation, payload=payload, occurred_at=datetime.now(UTC), audit_envelope=envelope
    )
    again = prepare_accounting_charge(
        operation, payload=payload, occurred_at=datetime.now(UTC), audit_envelope=envelope
    )

    event = _audit_event(first)
    assert (event["input_tokens"], event["output_tokens"]) == (5, 2)
    assert first.audit_envelope["redacted_payload"] is first.audit_envelope["payload"]
    # A replay of the same charge builds the same envelope bytes.
    assert again.audit_envelope == first.audit_envelope
    # The caller's envelope is not changed in place.
    assert envelope["payload"]["event"]["input_tokens"] is None


def test_usage_wins_over_the_billing_snapshot():
    payload = {
        "usage": {"prompt_tokens": 7},
        "metadata": {"billing": {"usage_snapshot": {"prompt_tokens": 9, "completion_tokens": 4}}},
    }

    assert accounting_audit_token_counts(payload) == (7, 4)


@pytest.mark.parametrize("value", [True, -1, 2**31, 1.5, "12"])
def test_a_count_that_does_not_fit_the_column_is_unknown(value):
    assert accounting_audit_token_counts({"usage": {"prompt_tokens": value}}) == (None, None)


def test_a_payload_without_mappings_has_no_counts():
    assert accounting_audit_token_counts({"usage": [1], "metadata": "x"}) == (None, None)
