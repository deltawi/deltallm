"""Distinct durable acceptance and canonical processing receipts."""

from uuid import UUID

from pydantic import Field

from src.billing.accounting.accounting_protocol import AccountingOutcome, FinalizationReceipt
from src.billing.charges.selector_charge import FrozenBillingContract, Identifier


class JournalReceipt(FrozenBillingContract):
    protocol_generation: int = Field(ge=1, le=2**63 - 1)
    operation_id: UUID
    journal_sequence: int = Field(ge=1, le=2**63 - 1)
    outcome: AccountingOutcome
    replayed: bool = False


class TerminalIdentity(FrozenBillingContract):
    """Issue fields a durable terminal row must match before a proof is released."""

    operation_id: UUID
    grant_id: Identifier
    grantee_id: Identifier
    fence_token: UUID
    permit_ordinal: int = Field(ge=0, le=1023)
    reservation_sha256: str = Field(min_length=64, max_length=64, pattern="^[0-9a-f]{64}$")


TerminalReceipt = FinalizationReceipt | JournalReceipt
TerminalReceiptType = type[FinalizationReceipt] | type[JournalReceipt]
