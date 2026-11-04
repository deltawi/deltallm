"""Adapt journal acceptance for real-database local terminal tests."""

from collections.abc import Sequence
from typing import Protocol

from src.billing.accounting.journal.accounting_terminal_snapshots import LocalTerminalValue
from src.billing.accounting.journal.accounting_terminal_receipts import (
    JournalReceipt,
    TerminalIdentity,
)
from src.billing.accounting.permits.accounting_local_receipts import RetainedLocalReceipt


class TerminalJournalPersistence(Protocol):
    async def append_batch(
        self, values: Sequence[LocalTerminalValue], *, expires_at: float
    ) -> Sequence[JournalReceipt]: ...

    async def find_batch(
        self, values: Sequence[TerminalIdentity], *, generation: int, expires_at: float
    ) -> Sequence[JournalReceipt | None]: ...


class JournalTerminalPersistence:
    def __init__(self, journal: TerminalJournalPersistence) -> None:
        self._journal = journal

    async def finalize_batch(
        self, values: Sequence[LocalTerminalValue], *, expires_at: float
    ) -> Sequence[JournalReceipt]:
        return await self._journal.append_batch(values, expires_at=expires_at)

    async def lookup_terminals(
        self, values: Sequence[RetainedLocalReceipt], *, expires_at: float
    ) -> Sequence[JournalReceipt | None]:
        """Serve the receipt release worker straight from the database."""
        if not values:
            return ()
        return await self._journal.find_batch(
            tuple(value.terminal_identity() for value in values),
            generation=values[0].generation,
            expires_at=expires_at,
        )
