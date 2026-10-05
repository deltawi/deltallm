"""Validate nested financial facts before freezing their canonical JSON bytes."""

from __future__ import annotations

import json

from src.billing.accounting_protocol import AccountingFinalization, AccountingReservation


def reservation_bytes(item: AccountingReservation) -> bytes:
    return _encoded(AccountingReservation.model_validate(item.model_dump()))


def finalization_bytes(item: AccountingFinalization) -> bytes:
    return _encoded(AccountingFinalization.model_validate(item.model_dump()))


def _encoded(item: AccountingReservation | AccountingFinalization) -> bytes:
    return json.dumps(
        item.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
