from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from src.billing.money import money_string
from src.billing.charges.realtime_accounting_bounds import RealtimeCostBounds
from src.billing.charges.realtime_pricing import RealtimePrices
from src.billing.charges.realtime_usage import RealtimeDurationUsage, RealtimeUsageReceipt


@dataclass(frozen=True, slots=True)
class RealtimeAttribution:
    session_id: str
    api_key: str = field(repr=False)
    user_id: str | None
    team_id: str | None
    organization_id: str | None
    owner_account_id: str | None
    model: str
    deployment_id: str
    deployment_model: str


@dataclass(frozen=True, slots=True)
class RealtimeChargeContext:
    attribution: RealtimeAttribution = field(repr=False)
    customer: RealtimePrices
    provider: RealtimePrices
    started_at: datetime
    cost_bounds: RealtimeCostBounds | None = None

    def snapshot(self) -> dict[str, object]:
        result = {
            "version": 1,
            "attribution": asdict(self.attribution),
            "customer": self.customer.snapshot(),
            "provider": self.provider.snapshot(),
            "started_at": self.started_at.isoformat(),
        }
        if self.cost_bounds is not None:
            result["cost_bounds"] = self.cost_bounds.snapshot()
        return result

    def spend_payload(
        self,
        receipt: RealtimeUsageReceipt,
        *,
        operation_started_at: datetime,
        completed_at: datetime,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        usage = receipt.usage
        if usage is None or receipt.pending_reason is not None:
            raise ValueError("Unconfirmed Realtime usage cannot be charged")
        duration = isinstance(usage, RealtimeDurationUsage)
        if duration:
            units = {"duration_seconds": str(usage.seconds)}
        else:
            units = {
                "prompt_tokens": usage.input_text + usage.cached_input_text,
                "completion_tokens": usage.output_text,
                "input_audio_tokens": usage.input_audio + usage.cached_input_audio,
                "output_audio_tokens": usage.output_audio,
                "prompt_tokens_cached": usage.cached_input_text + usage.cached_input_audio,
                "total_tokens": sum(asdict(usage).values()),
            }
        owner = self.attribution
        return {
            "request_id": operation_id if operation_id is not None else owner.session_id,
            "api_key": owner.api_key,
            "user_id": owner.user_id,
            "team_id": owner.team_id,
            "organization_id": owner.organization_id,
            "owner_account_id": owner.owner_account_id,
            "owner_snapshot_complete": True,
            "model": owner.model,
            "call_type": "realtime_" + receipt.operation,
            "usage": units,
            "cost_exact": money_string(self.customer.cost(usage)),
            "provider_cost_exact": money_string(self.provider.cost(usage)),
            "start_time": operation_started_at.astimezone(UTC).isoformat(),
            "end_time": completed_at.astimezone(UTC).isoformat(),
            "metadata": {
                **(
                    {
                        "realtime_session_id": owner.session_id,
                        "realtime_receipt_id": receipt.receipt_id,
                    }
                    if operation_id is not None
                    else {}
                ),
                "provider": "openai",
                "deployment_id": owner.deployment_id,
                "deployment_model": owner.deployment_model,
                "realtime_session_started_at": self.started_at.isoformat(),
                "realtime_timing_basis": "dispatch_to_receipt_acceptance",
                "realtime_pricing": {
                    "customer": self.customer.snapshot(),
                    "provider": self.provider.snapshot(),
                },
                "billing": {
                    "billing_unit": "second" if duration else "token",
                    "usage_snapshot": units,
                    "realtime_components": {
                        key: str(value) for key, value in asdict(usage).items()
                    },
                },
            },
        }
