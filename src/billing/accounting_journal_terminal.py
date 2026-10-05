"""Use journal acceptance through the shared local terminal owner."""

from collections.abc import Sequence
from typing import Protocol

from src.billing.accounting_local_leases import LocalPermitFinalization
from src.billing.accounting_terminal_receipts import JournalReceipt


class TerminalJournalPersistence(Protocol):
    async def append_batch(
        self, values: Sequence[LocalPermitFinalization], *, expires_at: float
    ) -> Sequence[JournalReceipt]: ...


class JournalTerminalPersistence:
    def __init__(self, journal: TerminalJournalPersistence) -> None:
        self._journal = journal

    async def finalize_batch(
        self, values: Sequence[LocalPermitFinalization], *, expires_at: float
    ) -> Sequence[JournalReceipt]:
        return await self._journal.append_batch(values, expires_at=expires_at)
