"""Keep native billing inputs in the bounded, request-owned batch execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid5

from src.batch.accounting_checkpoint import BatchAccountingCheckpoint, BatchAccountingUnavailable
from src.batch.models import BatchItemRecord
from src.batch.selector_checkpoint import BatchSelectorClaim
from src.billing.accounting_finalization import accounting_audit_envelope
from src.billing.accounting_protocol import AccountingOutcome
from src.billing.accounting_terminal_preparation import prepare_accounting_charge
from src.billing.money import money_string
from src.billing.tier_pricing import PricingResolution, resolve_exact_token_quote_pricing


@dataclass(slots=True)
class BatchAccountingExecution:
    claim: BatchSelectorClaim
    item: BatchItemRecord
    pricing: PricingResolution | None = None
    checkpoint: BatchAccountingCheckpoint | None = None
    stored_checkpoint: BatchAccountingCheckpoint | None = None
    dispatched: bool = False
    completion_saved: bool = False

    def completion_payload(
        self,
        payload: Mapping[str, object],
        usage: Mapping[str, object],
    ) -> dict[str, object]:
        checkpoint, pricing = self.checkpoint, self.pricing
        if checkpoint is None or pricing is None:
            raise BatchAccountingUnavailable()
        if checkpoint.terminal is None:
            checkpoint = self._freeze_completion(checkpoint, pricing, payload, usage)
            self.checkpoint = checkpoint
            self.item.accounting_checkpoint = checkpoint.model_dump(mode="json")
        if checkpoint.terminal.outcome is not AccountingOutcome.COMPLETED:
            raise BatchAccountingUnavailable()
        result = dict(payload)
        result["native_accounting"] = checkpoint.model_dump(mode="json")
        result["billing_event_id"] = str(checkpoint.operation_id)
        return result

    def _freeze_completion(
        self,
        checkpoint: BatchAccountingCheckpoint,
        pricing: PricingResolution,
        payload: Mapping[str, object],
        usage: Mapping[str, object],
    ) -> BatchAccountingCheckpoint:
        counts = _receipt_counts(usage)
        customer = resolve_exact_token_quote_pricing(
            pricing,
            model=pricing.callable_model,
            **counts,
            mode="batch",
        ).pricing
        provider = resolve_exact_token_quote_pricing(
            pricing,
            model=pricing.callable_model,
            **counts,
            mode="sync",
            pricing_view="provider",
        ).pricing
        if customer is None or provider is None:
            raise BatchAccountingUnavailable()
        charge = customer.cost(**counts)
        if charge > checkpoint.operation.reservation.allowance:
            raise BatchAccountingUnavailable()
        provider_cost = provider.cost(**counts)
        owner = checkpoint.operation.reservation.attribution
        accepted = {
            "request_id": str(checkpoint.operation_id),
            "api_key": owner.api_key,
            "user_id": owner.user_id,
            "team_id": owner.team_id,
            "organization_id": owner.organization_id,
            "owner_account_id": owner.owner_account_id,
            "owner_snapshot_complete": True,
            "end_user_id": owner.end_user_id,
            "model": owner.model,
            "call_type": owner.call_type,
            "usage": counts,
            "cost_exact": money_string(charge),
            "provider_cost_exact": money_string(provider_cost),
            "cache_hit": False,
            "metadata": {
                "provider": checkpoint.operation.attempts[-1].provider,
                "provider_cost_exact": money_string(provider_cost),
                "batch_id": self.claim.batch_id,
                "batch_item_id": self.claim.item_id,
                "pricing_tier": "batch",
                "pricing": checkpoint.operation.attempts[-1].pricing_snapshot,
            },
            "start_time": payload["completed_at"],
            "end_time": payload["completed_at"],
        }
        event_id = uuid5(checkpoint.operation_id, "provider-finalization:v2")
        terminal = prepare_accounting_charge(
            checkpoint.operation,
            payload=accepted,
            occurred_at=datetime.now(UTC),
            audit_envelope=accounting_audit_envelope(
                checkpoint.operation,
                event_id=event_id,
                status="success",
                metadata={"attempt_count": len(checkpoint.operation.attempts), "batch": True},
            ),
        )
        return BatchAccountingCheckpoint(
            operation_id=checkpoint.operation_id,
            claim_epoch=checkpoint.claim_epoch,
            operation=checkpoint.operation,
            terminal=terminal,
        )


def _receipt_counts(usage: Mapping[str, object]) -> dict[str, int]:
    counts = {
        field: usage.get(field, 0)
        for field in (
            "prompt_tokens",
            "completion_tokens",
            "prompt_tokens_cached",
        )
    }
    if any(type(value) is not int or not 0 <= value < 2**31 for value in counts.values()):
        raise BatchAccountingUnavailable()
    if counts["prompt_tokens_cached"] > counts["prompt_tokens"]:
        raise BatchAccountingUnavailable()
    return {field: int(value) for field, value in counts.items()}
