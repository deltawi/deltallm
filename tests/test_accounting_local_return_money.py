"""Suffix recovery must compare exact money independent of decimal context."""

from decimal import Decimal, localcontext
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.billing.accounting_local_leases import LocalPermitReturn
from src.db.accounting_calls import AccountingProtocolUnavailable
from tests.test_accounting_local_leases import database_grant, deadline, owner, suffix


@pytest.mark.parametrize("precision", [3, 28])
@pytest.mark.parametrize("proof", ["exact", "rounded"])
async def test_return_recovery_requires_the_complete_funded_amount(precision, proof):
    item = suffix()
    amount = Decimal("9999999999999999999.123456789123456789")
    item = LocalPermitReturn(
        grant=item.grant.model_copy(update={"allowance": amount}), first_unused_ordinal=1
    )
    with localcontext() as exact_context:
        exact_context.prec = 80
        exact = amount * 3
    with localcontext() as caller_context:
        caller_context.prec = precision
        rounded = +exact
        row = {
            **database_grant(item.grant),
            "returned_operations": 3,
            "returned_exact": str(exact if proof == "exact" else rounded),
        }
        db = MagicMock(query_raw=AsyncMock(side_effect=[TimeoutError(), [row]]))
        if proof == "exact":
            assert await owner(db).return_batch([item], expires_at=deadline()) == [3]
        else:
            with pytest.raises(AccountingProtocolUnavailable):
                await owner(db).return_batch([item], expires_at=deadline())
    assert db.query_raw.await_count == 2
