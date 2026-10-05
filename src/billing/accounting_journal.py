"""A durable terminal acknowledgement is not a canonical accounting event."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
from uuid import UUID

from pydantic import Field

from src.billing.accounting_local_leases import LocalPermitFinalization
from src.billing.accounting_local_terminal import freeze_local_terminals
from src.billing.accounting_protocol import AccountingOutcome
from src.billing.accounting_snapshots import finalization_bytes, reservation_bytes
from src.billing.money import money_string
from src.billing.selector_charge import FrozenBillingContract


class JournalReceipt(FrozenBillingContract):
    protocol_generation: int = Field(ge=1, le=2**63 - 1)
    operation_id: UUID
    journal_sequence: int = Field(ge=1, le=2**63 - 1)
    outcome: AccountingOutcome
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class TerminalJournalBatch:
    """Own complete immutable documents before the first persistence await."""

    generation: int
    values: tuple[LocalPermitFinalization, ...]
    compact: str
    reservations: tuple[str, ...]
    finalizations: tuple[str, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(str(value.finalization.operation_id) for value in self.values)


def journal_batch(values: Sequence[LocalPermitFinalization]) -> TerminalJournalBatch:
    generations = {value.finalization.protocol_generation for value in values}
    if len(generations) != 1:
        raise ValueError("a terminal journal batch requires one generation")
    generation = generations.pop()
    frozen = freeze_local_terminals(values, generation=generation)
    identities, reservations, finalizations = [], [], []
    ordinals = set()
    for value in frozen:
        proof = value.receipt
        ordinal = (proof.grant.grant_id, proof.permit_ordinal)
        if ordinal in ordinals:
            raise ValueError("a terminal journal batch repeats a grant ordinal")
        ordinals.add(ordinal)
        reservation = reservation_bytes(proof.reservation)
        finalization = finalization_bytes(value.finalization)
        reservation_fields = proof.reservation.model_dump(mode="json")
        reservations.append(reservation.decode())
        finalizations.append(finalization.decode())
        identities.append(
            {
                "operation_id": str(value.finalization.operation_id),
                "grant_id": proof.grant.grant_id,
                "grantee_id": proof.grant.grantee_id,
                "fence_token": str(proof.grant.fence_token),
                "permit_ordinal": proof.permit_ordinal,
                "allowance_exact": money_string(proof.reservation.allowance),
                "outcome": value.finalization.outcome.value,
                "expires_at": proof.reservation.expires_at.isoformat(),
                "subject": {
                    "attribution": {
                        key: getattr(proof.reservation.attribution, key)
                        for key in ("api_key", "user_id", "team_id", "organization_id", "model")
                    },
                    "windows": reservation_fields["windows"],
                    "allowance": reservation_fields["allowance"],
                },
                "reservation_sha256": hashlib.sha256(reservation).hexdigest(),
                "finalization_sha256": hashlib.sha256(finalization).hexdigest(),
            }
        )
    compact = json.dumps(identities, allow_nan=False, sort_keys=True, separators=(",", ":"))
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
