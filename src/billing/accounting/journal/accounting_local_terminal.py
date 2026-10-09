"""Freeze complete local terminals and acknowledge all issued proofs together."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import math
from typing import Protocol

from src.billing.accounting.permits.accounting_local_leases import LocalPermitFinalization
from src.billing.accounting.permits.accounting_local_receipts import LocalReceiptStore
from src.billing.accounting.accounting_protocol import FinalizationReceipt
from src.billing.accounting.journal.accounting_terminal_snapshots import (
    FrozenLocalTerminal,
    LocalTerminalValue,
    freeze_terminal_snapshots,
)
from src.billing.accounting.journal.accounting_terminal_receipts import (
    JournalReceipt,
    TerminalReceipt,
    TerminalReceiptType,
)
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.db.telemetry_acceptance import AcceptanceFailure


class LocalTerminalPersistence(Protocol):
    async def finalize_batch(
        self, values: Sequence[LocalTerminalValue], *, expires_at: float
    ) -> Sequence[TerminalReceipt]: ...


class LocalTerminalOwner:
    def __init__(
        self,
        persistence: LocalTerminalPersistence,
        receipts: LocalReceiptStore,
        *,
        generation: int,
        receipt_type: TerminalReceiptType = FinalizationReceipt,
    ) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("local terminal generation is invalid")
        if receipt_type not in (FinalizationReceipt, JournalReceipt):
            raise ValueError("local terminal receipt type is invalid")
        self._receipt_type = receipt_type
        self._persistence = persistence
        self._receipts = receipts
        self._generation = generation

    @property
    def generation(self) -> int:
        return self._generation

    def owns_receipts(self, receipts: LocalReceiptStore) -> bool:
        return self._receipts is receipts

    async def finalize_batch(
        self, values: Sequence[LocalTerminalValue], *, expires_at: float
    ) -> tuple[TerminalReceipt, ...]:
        frozen = freeze_terminal_snapshots(values, generation=self._generation)
        if not frozen:
            return ()
        _caller_deadline(expires_at)
        async with asyncio.timeout_at(expires_at):
            results = await self._persistence.finalize_batch(frozen, expires_at=expires_at)
        acknowledgements = validated_terminal_acks(frozen, results, receipt_type=self._receipt_type)
        prepared = self._receipts.prepare_acknowledgements(
            tuple((value, ack) for value, ack in zip(frozen, acknowledgements, strict=True))
        )
        _caller_deadline(expires_at)
        self._receipts._commit_acknowledgements(prepared)
        return acknowledgements

    async def finalize_documents(
        self, values: Sequence[bytes], *, expires_at: float
    ) -> tuple[TerminalReceipt, ...]:
        if len(values) > 256:
            raise ValueError("local terminal batch exceeds its entry limit")
        if 2 + sum(map(len, values)) + max(0, len(values) - 1) > 1_048_576:
            raise ValueError("local terminal batch exceeds its byte limit")
        snapshots = tuple(
            FrozenLocalTerminal(value, generation=self._generation) for value in values
        )
        return await self.finalize_batch(snapshots, expires_at=expires_at)


def _caller_deadline(expires_at: float) -> None:
    if not math.isfinite(expires_at) or expires_at <= asyncio.get_running_loop().time():
        raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)


def freeze_local_terminals(
    values: Sequence[LocalPermitFinalization], *, generation: int
) -> tuple[LocalPermitFinalization, ...]:
    return tuple(
        value.restore() for value in freeze_terminal_snapshots(values, generation=generation)
    )


def local_terminal_bytes(value: LocalPermitFinalization, *, generation: int) -> bytes:
    return FrozenLocalTerminal(value, generation=generation).document


def validated_terminal_acks(
    values: Sequence[LocalTerminalValue],
    results: Sequence[TerminalReceipt],
    *,
    receipt_type: TerminalReceiptType = FinalizationReceipt,
) -> tuple[TerminalReceipt, ...]:
    if receipt_type not in (FinalizationReceipt, JournalReceipt) or len(results) != len(values):
        raise invalid_result()
    copies = []
    for value, result in zip(values, results, strict=True):
        if type(result) is not receipt_type:
            raise invalid_result()
        try:
            copy = receipt_type.model_validate(result.model_dump())
        except ValueError:
            raise invalid_result() from None
        generation = (
            value.generation
            if isinstance(value, FrozenLocalTerminal)
            else value.finalization.protocol_generation
        )
        operation_id = (
            value.operation_id
            if isinstance(value, FrozenLocalTerminal)
            else value.finalization.operation_id
        )
        outcome = (
            value.outcome if isinstance(value, FrozenLocalTerminal) else value.finalization.outcome
        )
        if (
            copy.protocol_generation != generation
            or copy.operation_id != operation_id
            or copy.outcome is not outcome
        ):
            raise invalid_result()
        copies.append(copy)
    return tuple(copies)
