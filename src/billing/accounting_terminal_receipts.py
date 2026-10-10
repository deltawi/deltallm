"""Distinct durable acceptance and canonical processing receipts."""

from uuid import UUID

from pydantic import Field

from src.billing.accounting_protocol import AccountingOutcome, FinalizationReceipt
from src.billing.selector_charge import FrozenBillingContract


class JournalReceipt(FrozenBillingContract):
    protocol_generation: int = Field(ge=1, le=2**63 - 1)
    operation_id: UUID
    journal_sequence: int = Field(ge=1, le=2**63 - 1)
    outcome: AccountingOutcome
    replayed: bool = False


TerminalReceipt = FinalizationReceipt | JournalReceipt
TerminalReceiptType = type[FinalizationReceipt] | type[JournalReceipt]
