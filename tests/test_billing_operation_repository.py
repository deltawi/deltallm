import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.billing.operation_reservation import BillingOperationUnavailable, ComponentState
from src.db.billing_operations import BillingOperationRepository
from tests.test_operation_reservation import make_operation


def transaction_mock(results):
    tx = MagicMock()
    tx.query_raw = AsyncMock(side_effect=results)
    tx.execute_raw = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=tx)
    context.__aexit__ = AsyncMock(return_value=False)
    return tx, context


def deadline():
    return asyncio.get_running_loop().time() + 2


async def test_reservation_locks_operation_then_accounts_then_capacity_with_four_statements():
    operation = make_operation()
    tx, context = transaction_mock([[], [{"operation_id": "inserted"}], [{"pending_count": 1}]])
    db = MagicMock()
    db.tx.return_value = context
    result = await BillingOperationRepository(db).reserve(operation, expires_at=deadline())
    statements = [call.args[0] for call in tx.mock_calls if call[0] in {"query_raw", "execute_raw"}]
    assert len(statements) == 4
    assert "set_config('statement_timeout'" in statements[0]
    assert "INSERT INTO deltallm_billing_operations" in statements[1]
    assert "ON CONFLICT (operation_id) DO NOTHING RETURNING" in statements[1]
    assert "deltallm_adjust_operation_hold" in statements[2]
    assert "pending_count=pending_count+1" in statements[3]
    assert result.selector_state is ComponentState.RESERVED
    assert db.tx.call_args.kwargs["timeout"].total_seconds() <= 0.25


@pytest.mark.parametrize("changed", [False, True])
async def test_duplicate_only_reads_frozen_operation_without_touching_holds_or_capacity(changed):
    operation = make_operation()
    snapshot = operation.model_dump(mode="json")
    if changed:
        snapshot["owner_token"] = "different-owner"
    tx, context = transaction_mock(
        [[], [], [{"snapshot": snapshot, "selector_state": "reserved", "answer_state": "reserved"}]]
    )
    db = MagicMock()
    db.tx.return_value = context
    repository = BillingOperationRepository(db)
    if changed:
        with pytest.raises(BillingOperationUnavailable):
            await repository.reserve(operation, expires_at=deadline())
    else:
        assert (await repository.reserve(operation, expires_at=deadline())).operation == operation
    assert tx.query_raw.await_count == 3
    assert "FOR UPDATE" in tx.query_raw.call_args.args[0]
    tx.execute_raw.assert_not_awaited()
    assert all("ingestion_capacity" not in call.args[0] for call in tx.query_raw.call_args_list)


async def test_capacity_exhaustion_aborts_transaction_after_hold_adjustment():
    tx, context = transaction_mock([[], [{"operation_id": "inserted"}], []])
    db = MagicMock()
    db.tx.return_value = context
    with pytest.raises(BillingOperationUnavailable):
        await BillingOperationRepository(db).reserve(make_operation(), expires_at=deadline())
    tx.execute_raw.assert_awaited_once()
    assert context.__aexit__.call_args.args[0] is BillingOperationUnavailable


@pytest.mark.parametrize("error", [asyncio.CancelledError, TimeoutError])
async def test_reservation_interruption_leaves_transaction_and_never_retries(error):
    tx, context = transaction_mock([[], error()])
    db = MagicMock()
    db.tx.return_value = context
    expected = (
        asyncio.CancelledError if error is asyncio.CancelledError else BillingOperationUnavailable
    )
    with pytest.raises(expected):
        await BillingOperationRepository(db).reserve(make_operation(), expires_at=deadline())
    assert tx.query_raw.await_count == 2
    assert context.__aexit__.call_args.args[0] is error
    tx.execute_raw.assert_not_awaited()
