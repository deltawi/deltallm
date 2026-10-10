"""Non-HTTP finalization keeps one stable reporting ID across retries."""

from datetime import UTC, datetime

import pytest

from src.billing.accounting.accounting_protocol import AccountingAttempt, AccountingOperationHandle
from src.billing.accounting.journal.accounting_terminal_preparation import prepare_accounting_charge
from tests.test_accounting_protocol import reservation


@pytest.mark.parametrize("request_id", [None, "", "bad id", "a" * 257, 42])
def test_terminal_preparation_uses_the_operation_id_for_invalid_correlation(request_id):
    reserved = reservation()
    operation = AccountingOperationHandle(
        reservation=reserved,
        dispatch_token=reserved.owner_token,
        accounting_partition=0,
        attempts=(
            AccountingAttempt(
                deployment_id="deployment-1",
                provider="openai",
                model="model-group",
                pricing_snapshot=reserved.pricing_snapshot,
            ),
        ),
    )
    payload = {"cost_exact": "0.75", "request_id": request_id}
    occurred_at = datetime.now(UTC)
    first, second = [
        prepare_accounting_charge(
            operation, payload=payload, occurred_at=occurred_at, audit_envelope={}
        )
        for _ in range(2)
    ]
    assert first.spend_payload["request_id"] == str(reserved.operation_id)
    assert first == second
    assert payload["request_id"] == request_id
    assert first.operation_id == reserved.operation_id


def test_valid_caller_id_does_not_replace_the_economic_operation_id():
    first, second = reservation(), reservation()
    assert first.operation_id != second.operation_id
    for reserved in (first, second):
        operation = AccountingOperationHandle(
            reservation=reserved,
            dispatch_token=reserved.owner_token,
            accounting_partition=0,
            attempts=(
                AccountingAttempt(
                    deployment_id="deployment-1",
                    provider="openai",
                    model="model-group",
                    pricing_snapshot={},
                ),
            ),
        )
        terminal = prepare_accounting_charge(
            operation,
            payload={"cost_exact": "0.75", "request_id": "same-caller-id"},
            occurred_at=datetime.now(UTC),
            audit_envelope={},
        )
        assert terminal.spend_payload["request_id"] == "same-caller-id"
        assert terminal.operation_id == reserved.operation_id
