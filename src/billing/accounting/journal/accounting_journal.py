"""A durable terminal acknowledgement is not a canonical accounting event."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json

from src.billing.accounting.journal.accounting_terminal_snapshots import (
    FrozenLocalTerminal,
    LocalTerminalValue,
    freeze_terminal_snapshots,
)
from src.billing.accounting.journal.accounting_terminal_receipts import (
    JournalReceipt as JournalReceipt,
)


@dataclass(frozen=True, slots=True)
class TerminalJournalBatch:
    """Own complete immutable documents before the first persistence await."""

    generation: int
    values: tuple[FrozenLocalTerminal, ...]
    compact: str
    reservations: tuple[str, ...]
    finalizations: tuple[str, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(str(value.operation_id) for value in self.values)


def journal_batch(values: Sequence[LocalTerminalValue]) -> TerminalJournalBatch:
    generations = {
        value.generation
        if isinstance(value, FrozenLocalTerminal)
        else value.finalization.protocol_generation
        for value in values
    }
    if len(generations) != 1:
        raise ValueError("a terminal journal batch requires one generation")
    generation = generations.pop()
    frozen = freeze_terminal_snapshots(values, generation=generation)
    reservations, finalizations = [], []
    ordinals = set()
    for value in frozen:
        proof = value.retained_receipt
        ordinal = (proof.grant.grant_id, proof.permit_ordinal)
        if ordinal in ordinals:
            raise ValueError("a terminal journal batch repeats a grant ordinal")
        ordinals.add(ordinal)
        reservations.append(value.reservation_json.decode())
        finalizations.append(value.finalization_json.decode())
    compact = (b"[" + b",".join(value.journal_identity_json for value in frozen) + b"]").decode()
    # The actual bound includes metadata and both document arrays, not just the
    # request DTO. Escaping a document inside an array also consumes capacity.
    size = len(compact.encode()) + sum(
        len(json.dumps(documents, ensure_ascii=True, separators=(",", ":")).encode())
        for documents in (reservations, finalizations)
    )
    if size > 1_048_576:
        raise ValueError("terminal journal batch exceeds its serialized byte limit")
    return TerminalJournalBatch(
        generation, frozen, compact, tuple(reservations), tuple(finalizations)
    )
