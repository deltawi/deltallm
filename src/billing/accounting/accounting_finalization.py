"""Freeze accounting terminal events and their required audit envelope."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid5

from src.billing.accounting.accounting_protocol import AccountingOperationHandle
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.journal.accounting_terminal_preparation import (
    prepare_accounting_charge,
    prepare_accounting_uncertain,
)
from src.billing.spend.spend_operations import SpendPersistenceUnavailable


def accounting_audit_envelope(
    operation: AccountingOperationHandle,
    *,
    event_id: object,
    status: str,
    metadata: Mapping[str, object],
) -> dict[str, object]:
    attribution = operation.reservation.attribution
    audit_event_id = uuid5(UUID(str(event_id)), "accounting-audit:v1")
    event = {
        "action": "ACCOUNTING_PROVIDER_FINALIZATION",
        "organization_id": attribution.organization_id,
        "actor_type": "api_key",
        "actor_id": attribution.user_id or attribution.api_key,
        "api_key": attribution.api_key,
        "resource_type": "model",
        "resource_id": attribution.model,
        "request_id": str(operation.reservation.operation_id),
        "correlation_id": str(operation.reservation.operation_id),
        "ip": None,
        "user_agent": None,
        "status": status,
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "error_type": None if status == "success" else "AccountingOutcomeUnknown",
        "error_code": None,
        "metadata": metadata,
        "prev_hash": None,
        "event_hash": None,
        "event_id": str(event_id),
    }
    payload = {"event": event, "payloads": [], "critical": True}
    return {
        "event_id": str(audit_event_id),
        "record_type": "audit_event",
        "organization_id": attribution.organization_id,
        "payload": payload,
        "redacted_payload": payload,
    }


class AccountingSpendFinalizer:
    def __init__(self, accounting: AccountingProtocolService | None) -> None:
        self.accounting = accounting

    def _require_accounting(
        self,
        operation: AccountingOperationHandle,
        *,
        event_id: object,
    ) -> AccountingProtocolService:
        if (
            self.accounting is None
            or event_id != str(operation.reservation.operation_id)
            or operation.reservation.protocol_generation != self.accounting.generation
        ):
            raise SpendPersistenceUnavailable()
        return self.accounting

    async def log_spend(
        self,
        operation: AccountingOperationHandle,
        *,
        event_id: object,
        payload: Mapping[str, object],
    ) -> None:
        service = self._require_accounting(operation, event_id=event_id)
        final_event_id = uuid5(operation.reservation.operation_id, "provider-finalization:v2")
        finalization = prepare_accounting_charge(
            operation,
            payload=payload,
            occurred_at=datetime.now(UTC),
            audit_envelope=accounting_audit_envelope(
                operation,
                event_id=final_event_id,
                status="success",
                metadata={
                    "attempt_count": len(operation.attempts),
                    "cache_hit": payload.get("cache_hit") is True,
                },
            ),
        )
        await service.finalize_operation(operation, finalization)

    async def log_failure(
        self,
        operation: AccountingOperationHandle,
        *,
        event_id: object,
        payload: Mapping[str, object],
    ) -> None:
        service = self._require_accounting(operation, event_id=event_id)
        exc = payload.get("exc")
        reason = (
            str(
                payload.get("error_type")
                or getattr(exc, "error_type", None)
                or (exc.__class__.__name__ if exc is not None else "provider_outcome_unknown")
            ).strip()[:256]
            or "provider_outcome_unknown"
        )
        final_event_id = uuid5(operation.reservation.operation_id, "provider-finalization:v2")
        finalization = prepare_accounting_uncertain(
            operation,
            reason=reason,
            occurred_at=datetime.now(UTC),
            audit_envelope=accounting_audit_envelope(
                operation,
                event_id=final_event_id,
                status="error",
                metadata={
                    "attempt_count": len(operation.attempts),
                    "uncertainty_reason": reason,
                },
            ),
        )
        await service.finalize_operation(operation, finalization)
