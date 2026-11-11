"""Freeze accounting terminal events and their required audit envelope."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import logging
from uuid import UUID, uuid5

from src.billing.accounting.accounting_protocol import AccountingOperationHandle
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.journal.accounting_terminal_preparation import (
    UnqualifiedAccountingReceipt,
    prepare_accounting_charge,
    prepare_accounting_not_dispatched,
    prepare_accounting_uncertain,
)
from src.billing.spend.spend_operations import SpendPersistenceUnavailable
from src.metrics.accounting import increment_accounting_failure
from src.request_identity import valid_request_id

logger = logging.getLogger(__name__)


def accounting_audit_envelope(
    operation: AccountingOperationHandle,
    *,
    event_id: object,
    status: str,
    metadata: Mapping[str, object],
    error_type: str | None = None,
    correlation_id: object = None,
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
        "correlation_id": (
            correlation_id
            if valid_request_id(correlation_id)
            else str(operation.reservation.operation_id)
        ),
        "ip": None,
        "user_agent": None,
        "status": status,
        "latency_ms": None,
        "input_tokens": None,
        "output_tokens": None,
        "error_type": None if status == "success" else error_type or "AccountingOutcomeUnknown",
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
        try:
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
        except UnqualifiedAccountingReceipt as exc:
            # The provider already ran. A charge above the allowance means the
            # reservation for this model is sized too small; make it visible.
            increment_accounting_failure("finalization", "receipt", str(exc))
            logger.warning(
                "accounting receipt rejected before journal acceptance",
                extra={
                    "reason": str(exc),
                    "operation_id": str(operation.reservation.operation_id),
                    "model": operation.reservation.attribution.model,
                    "deployment_id": operation.reservation.attribution.deployment_id,
                },
            )
            await self.log_failure(
                operation,
                event_id=event_id,
                payload={"error_type": str(exc), "request_id": payload.get("request_id")},
            )
            raise SpendPersistenceUnavailable() from None
        await service.finalize_operation(operation, finalization)

    async def log_failure(
        self,
        operation: AccountingOperationHandle,
        *,
        event_id: object,
        payload: Mapping[str, object],
    ) -> None:
        service = self._require_accounting(operation, event_id=event_id)
        final_event_id = uuid5(operation.reservation.operation_id, "provider-finalization:v2")
        occurred_at = datetime.now(UTC)
        exc = payload.get("exc")
        reason = (
            str(
                payload.get("error_type")
                or getattr(exc, "error_type", None)
                or (exc.__class__.__name__ if exc is not None else "provider_outcome_unknown")
            ).strip()[:256]
            or "provider_outcome_unknown"
        )
        if operation.charged_nothing():
            # No attempt can carry a provider charge, so the whole allowance is released.
            refused = operation.provider_refused_attempts
            metadata: dict[str, object] = {
                "attempt_count": len(operation.attempts),
                "outcome_unknown": False,
                "reason": "provider_refused" if refused else "provider_pool_timeout",
            }
            if refused:
                metadata["refused_attempts"] = refused
                metadata["failure_reason"] = reason
            finalization = prepare_accounting_not_dispatched(
                operation,
                occurred_at=occurred_at,
                audit_envelope=accounting_audit_envelope(
                    operation,
                    event_id=final_event_id,
                    status="error",
                    error_type=(
                        "AccountingProviderRefused"
                        if refused
                        else "AccountingProviderNotDispatched"
                    ),
                    correlation_id=payload.get("request_id"),
                    metadata=metadata,
                ),
            )
            await service.finalize_operation(operation, finalization)
            return
        finalization = prepare_accounting_uncertain(
            operation,
            reason=reason,
            occurred_at=occurred_at,
            audit_envelope=accounting_audit_envelope(
                operation,
                event_id=final_event_id,
                status="error",
                error_type=reason,
                correlation_id=payload.get("request_id"),
                metadata={
                    "attempt_count": len(operation.attempts),
                    "uncertainty_reason": reason,
                },
            ),
        )
        await service.finalize_operation(operation, finalization)
