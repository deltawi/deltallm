"""Keep native billing inputs in the bounded, request-owned batch execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid5

from src.batch.accounting_checkpoint import BatchAccountingCheckpoint, BatchAccountingUnavailable
from src.batch.models import BatchItemRecord
from src.batch.selector_checkpoint import BatchSelectorClaim
from src.billing.accounting.accounting_finalization import accounting_audit_envelope
from src.billing.accounting.accounting_protocol import AccountingOutcome
from src.billing.accounting.journal.accounting_terminal_preparation import prepare_accounting_charge
from src.billing.money import money_string
from src.billing.pricing.pricing import with_reported_prompt_cache
from src.billing.pricing.token_quote_policy import ExactTokenQuote
from src.billing.pricing.tier_pricing import PricingResolution, resolve_exact_token_quote_pricing

# A quote with no price for these reasons is a model nobody priced, not a broken
# price. Live calls record such usage at $0 with the reason; batch does the same.
_UNPRICED_REASONS = frozenset({"no_configured_pricing", "missing_usage_for_billing_mode"})


@dataclass(slots=True)
class BatchAccountingExecution:
    claim: BatchSelectorClaim
    item: BatchItemRecord
    pricing: PricingResolution | None = None
    checkpoint: BatchAccountingCheckpoint | None = None
    stored_checkpoint: BatchAccountingCheckpoint | None = None
    dispatched: bool = False
    completion_saved: bool = False
    # Left open for a later claim's retry: cleanup must not close it.
    released: bool = False
    # The proof for this claim's new attempt could not be saved, so the
    # provider was not called for it. The funded operation is still open.
    proof_unsaved: bool = False

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
        prices = _CompletionPrices.resolve(pricing, counts)
        if prices.charge > checkpoint.operation.reservation.allowance:
            raise BatchAccountingUnavailable()
        owner = checkpoint.operation.reservation.attribution
        metadata: dict[str, object] = {
            "provider": checkpoint.operation.attempts[-1].provider,
            "batch_id": self.claim.batch_id,
            "batch_item_id": self.claim.item_id,
            "pricing_tier": "batch",
            "pricing": checkpoint.operation.attempts[-1].pricing_snapshot,
        }
        accepted: dict[str, object] = {
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
            "cost_exact": money_string(prices.charge),
            "cache_hit": False,
            "metadata": metadata,
            "start_time": payload["completed_at"],
            "end_time": payload["completed_at"],
        }
        prices.record(accepted, metadata)
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


@dataclass(frozen=True, slots=True)
class _CompletionPrices:
    charge: Decimal
    # None is unknown, as on live calls: an unpriced provider view is not a $0 cost.
    provider_cost: Decimal | None
    customer: ExactTokenQuote
    provider: ExactTokenQuote

    @classmethod
    def resolve(cls, pricing: PricingResolution, counts: Mapping[str, int]) -> _CompletionPrices:
        customer = resolve_exact_token_quote_pricing(
            pricing, model=pricing.callable_model, **counts, mode="batch"
        )
        provider = resolve_exact_token_quote_pricing(
            pricing, model=pricing.callable_model, **counts, mode="sync", pricing_view="provider"
        )
        if customer.pricing is None and not _funded_unpriced(pricing, customer):
            raise BatchAccountingUnavailable()
        if provider.pricing is None and provider.unpriced_reason not in _UNPRICED_REASONS:
            raise BatchAccountingUnavailable()
        return cls(
            charge=Decimal(0) if customer.pricing is None else customer.pricing.cost(**counts),
            provider_cost=None if provider.pricing is None else provider.pricing.cost(**counts),
            customer=customer,
            provider=provider,
        )

    def record(self, accepted: dict[str, object], metadata: dict[str, object]) -> None:
        if self.provider_cost is None:
            metadata["provider_billing"] = _unpriced_billing(self.provider)
        else:
            accepted["provider_cost_exact"] = money_string(self.provider_cost)
            metadata["provider_cost_exact"] = money_string(self.provider_cost)
        if self.customer.pricing is None:
            # The spend row's unpriced_reason column reads this block.
            metadata["billing"] = _unpriced_billing(self.customer)
            metadata["billing_status"] = "unpriced"


def _funded_unpriced(pricing: PricingResolution, quote: ExactTokenQuote) -> bool:
    """Whether funding also saw no token price, so it reserved only the request price."""
    return (
        quote.unpriced_reason in _UNPRICED_REASONS
        and pricing.customer_token_pricing is None
        and pricing.catalog_token_pricing is None
    )


def _unpriced_billing(quote: ExactTokenQuote) -> dict[str, object]:
    billing: dict[str, object] = {"cost": 0.0, "unpriced_reason": quote.unpriced_reason}
    if quote.missing_pricing_fields:
        billing["missing_pricing_fields"] = list(quote.missing_pricing_fields)
    return billing


def _receipt_counts(usage: Mapping[str, object]) -> dict[str, int]:
    usage = with_reported_prompt_cache(usage)
    counts = {
        field: usage.get(field, 0)
        for field in (
            "prompt_tokens",
            "completion_tokens",
            "prompt_tokens_cached",
        )
    }
    # Write counts are kept only when present, so receipts without writes keep their shape.
    for field in _CACHE_WRITE_COUNTS:
        if usage.get(field):
            counts[field] = usage[field]
    if any(type(value) is not int or not 0 <= value < 2**31 for value in counts.values()):
        raise BatchAccountingUnavailable()
    written = counts.get("prompt_tokens_cache_write", 0)
    if counts["prompt_tokens_cached"] + written > counts["prompt_tokens"]:
        raise BatchAccountingUnavailable()
    if counts.get("prompt_tokens_cache_write_1h", 0) > written:
        raise BatchAccountingUnavailable()
    return {field: int(value) for field, value in counts.items()}


_CACHE_WRITE_COUNTS = ("prompt_tokens_cache_write", "prompt_tokens_cache_write_1h")
