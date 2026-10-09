"""Adapt batch execution to the shared money owner without another queue or pool."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
import json
from uuid import uuid4, uuid5

from src.batch.accounting_checkpoint import (
    CHECKPOINT_BATCH_MAX_ITEMS,
    CHECKPOINT_MAX_BYTES,
    BatchAccountingCheckpoint,
    BatchAccountingCheckpoints,
    BatchAccountingUnavailable,
    BatchAccountingWrite,
)
from src.batch.accounting_execution import BatchAccountingExecution
from src.batch.endpoints import batch_call_type_for_endpoint
from src.batch.models import BatchItemRecord, BatchJobRecord
from src.batch.selector_checkpoint import BatchSelectorClaim
from src.batch.selector_identity import batch_selector_operation_id
from src.billing.accounting.accounting_admission import (
    ACCOUNTING_RECOVERY_LIFETIME,
    admit_accounting_reservation,
    reservation_audit_envelope,
)
from src.billing.accounting.accounting_finalization import accounting_audit_envelope
from src.billing.accounting.accounting_pricing import accounting_pricing_snapshot
from src.billing.accounting.accounting_protocol import (
    AccountingAttribution,
    AccountingAttempt,
    AccountingOperationHandle,
    AccountingReservation,
    request_fingerprint,
)
from src.billing.accounting.accounting_service import AccountingProtocolService
from src.billing.accounting.journal.accounting_terminal_preparation import (
    prepare_accounting_not_dispatched,
    prepare_accounting_uncertain,
)
from src.billing.pricing.frozen_pricing import freeze_operation_pricing
from src.billing.charges.provider_allowance import conservative_provider_allowance
from src.billing.pricing.tier_pricing import resolve_deployment_tier_pricing
from src.models.requests import ChatCompletionRequest, EmbeddingRequest
from src.models.responses import UserAPIKeyAuth
from src.providers.resolution import resolve_provider
from src.router.router import Deployment
from src.services.tier_policy_service import TierPolicyService
from src.telemetry.provider_request_bounds import validated_provider_request_bounds


class NativeBatchBilling:
    def __init__(
        self,
        accounting: AccountingProtocolService,
        checkpoints: BatchAccountingCheckpoints,
        *,
        tier_policy: TierPolicyService | None,
        max_provider_attempts: int,
    ) -> None:
        if type(max_provider_attempts) is not int or not 1 <= max_provider_attempts <= 128:
            raise ValueError("Invalid native batch attempt limit")
        self.accounting = accounting
        self.checkpoints = checkpoints
        self.tier_policy = tier_policy
        self.max_provider_attempts = max_provider_attempts

    def bind(
        self,
        job: BatchJobRecord,
        item: BatchItemRecord,
        *,
        worker_id: str,
        auth: UserAPIKeyAuth | None,
    ) -> BatchAccountingExecution:
        if auth is None or job.created_by_api_key != auth.api_key or item.batch_id != job.batch_id:
            raise BatchAccountingUnavailable()
        claim = BatchSelectorClaim(
            job.batch_id,
            item.item_id,
            auth.api_key,
            worker_id,
            item.claim_epoch,
        )
        checkpoint = decode_checkpoint(item.accounting_checkpoint)
        return BatchAccountingExecution(
            claim=claim,
            item=item,
            checkpoint=checkpoint,
            stored_checkpoint=checkpoint,
        )

    async def prepare_group(
        self,
        executions: Sequence[BatchAccountingExecution],
        *,
        payloads: Sequence[ChatCompletionRequest | EmbeddingRequest],
        auths: Sequence[UserAPIKeyAuth | None],
        deployment: Deployment,
        selector_items: frozenset[str] = frozenset(),
    ) -> None:
        if (
            not executions
            or len(executions) > CHECKPOINT_BATCH_MAX_ITEMS
            or len(executions) != len(payloads)
            or len(executions) != len(auths)
        ):
            raise BatchAccountingUnavailable()
        for execution in executions:
            if execution.checkpoint is not None and execution.pricing is None:
                try:
                    await self.fail_items([execution.item], worker_id=execution.claim.worker_id)
                finally:
                    execution.checkpoint = decode_checkpoint(execution.item.accounting_checkpoint)
                    execution.dispatched = True
                execution.stored_checkpoint = execution.checkpoint
                raise BatchAccountingUnavailable()
        expected = [execution.stored_checkpoint for execution in executions]
        results = await asyncio.gather(
            *(
                self._prepare_attempt(
                    execution,
                    payload=payload,
                    auth=auth,
                    deployment=deployment,
                    selector_expected=execution.claim.item_id in selector_items,
                )
                for execution, payload, auth in zip(executions, payloads, auths, strict=True)
            ),
            return_exceptions=True,
        )
        if any(isinstance(result, BaseException) for result in results):
            await self.close_group(executions)
            first = next(result for result in results if isinstance(result, BaseException))
            raise first
        writes = [
            BatchAccountingWrite(execution.claim, old, execution.checkpoint)
            for execution, old in zip(executions, expected, strict=True)
        ]
        await self.checkpoints.write_many(writes, expires_at=deadline())
        for execution in executions:
            execution.stored_checkpoint = execution.checkpoint
            execution.item.accounting_checkpoint = execution.checkpoint.model_dump(mode="json")
            execution.dispatched = True

    async def _prepare_attempt(
        self,
        execution: BatchAccountingExecution,
        *,
        payload: ChatCompletionRequest | EmbeddingRequest,
        auth: UserAPIKeyAuth | None,
        deployment: Deployment,
        selector_expected: bool,
    ) -> None:
        if (
            auth is None
            or auth.api_key != execution.claim.api_key
            or not self.accounting.worker_health.ready
        ):
            raise BatchAccountingUnavailable()
        pricing = freeze_operation_pricing(
            resolve_deployment_tier_pricing(
                auth=auth,
                model=payload.model,
                deployment=deployment,
                tier_policy_service=self.tier_policy,
                mode="batch",
            )
        )
        allowance = conservative_provider_allowance(
            pricing=pricing,
            model_info=deployment.model_info,
            call_type="completion" if isinstance(payload, ChatCompletionRequest) else "embedding",
            bounds=validated_provider_request_bounds(payload),
            max_attempts=self.max_provider_attempts,
        )
        snapshot = accounting_pricing_snapshot(pricing)
        snapshot["batch_claim_epoch"] = execution.claim.claim_epoch
        if selector_expected:
            snapshot["selector_event_id"] = str(
                uuid5(
                    batch_selector_operation_id(execution.claim.batch_id, execution.claim.item_id),
                    "selector:v1",
                )
            )
        attempt = AccountingAttempt(
            deployment_id=deployment.deployment_id,
            provider=resolve_provider(deployment.deltallm_params),
            model=payload.model,
            pricing_snapshot=snapshot,
        )
        existing = execution.checkpoint
        if existing is None:
            handle = await self._admit(execution, auth, payload, allowance, attempt)
        else:
            reservation = existing.operation.reservation
            if (
                existing.terminal is not None
                or len(existing.operation.attempts) >= self.max_provider_attempts
                or allowance > reservation.allowance
                or reservation.attribution.model != payload.model
                or not _same_subject(reservation, auth, payload)
                or reservation.expires_at <= datetime.now(UTC)
            ):
                raise BatchAccountingUnavailable()
            handle = existing.operation.model_copy(
                update={
                    "attempts": existing.operation.attempts + (attempt,),
                }
            )
        execution.checkpoint = BatchAccountingCheckpoint(
            operation_id=handle.reservation.operation_id,
            claim_epoch=execution.claim.claim_epoch,
            operation=handle,
        )
        execution.pricing = pricing

    async def _admit(
        self,
        execution: BatchAccountingExecution,
        auth: UserAPIKeyAuth,
        payload: ChatCompletionRequest | EmbeddingRequest,
        allowance: Decimal,
        attempt: AccountingAttempt,
    ) -> AccountingOperationHandle:
        operation_id = batch_selector_operation_id(
            execution.claim.batch_id, execution.claim.item_id
        )
        attribution = AccountingAttribution(
            api_key=auth.api_key,
            user_id=auth.user_id,
            team_id=auth.team_id,
            organization_id=auth.organization_id,
            owner_account_id=auth.owner_account_id,
            end_user_id=payload.user,
            model=payload.model,
            deployment_id=attempt.deployment_id,
            provider=attempt.provider,
            call_type=batch_call_type_for_endpoint(
                "/v1/chat/completions"
                if isinstance(payload, ChatCompletionRequest)
                else "/v1/embeddings"
            ),
        )
        reservation = AccountingReservation(
            protocol_generation=self.accounting.generation,
            operation_id=operation_id,
            owner_token=uuid4(),
            request_fingerprint=request_fingerprint(
                operation_kind="batch",
                payload=payload.model_dump(mode="json"),
            ),
            attribution=attribution,
            allowance=allowance,
            pricing_snapshot=dict(attempt.pricing_snapshot),
            audit_envelope=reservation_audit_envelope(
                attribution,
                operation_id=operation_id,
                allowance=allowance,
            ),
            expires_at=datetime.now(UTC) + ACCOUNTING_RECOVERY_LIFETIME,
        )
        return await admit_accounting_reservation(
            self.accounting, reservation=reservation, attempt=attempt
        )

    async def fail_items(self, items: Sequence[BatchItemRecord], *, worker_id: str) -> None:
        writes = []
        for item in items:
            stored = decode_checkpoint(item.accounting_checkpoint)
            if stored is None:
                continue
            claim = BatchSelectorClaim(
                item.batch_id,
                item.item_id,
                stored.operation.reservation.attribution.api_key,
                worker_id,
                item.claim_epoch,
            )
            terminal = terminal_checkpoint(stored, claim_epoch=item.claim_epoch, dispatched=True)
            original_epoch = stored.operation.reservation.pricing_snapshot.get(
                "batch_claim_epoch",
                stored.claim_epoch,
            )
            if type(original_epoch) is not int:
                raise BatchAccountingUnavailable()
            expected = (
                stored.model_copy(update={"terminal": None, "claim_epoch": original_epoch})
                if stored.terminal is not None
                else stored
            )
            writes.append(BatchAccountingWrite(claim, expected, terminal))
            item.accounting_checkpoint = terminal.model_dump(mode="json")
        await self.checkpoints.write_many(writes, expires_at=deadline())

    async def close_group(self, executions: Sequence[BatchAccountingExecution]) -> None:
        writes = []
        for execution in executions:
            if (
                execution.checkpoint is None
                or execution.completion_saved
                or (
                    execution.checkpoint.terminal is not None
                    and execution.stored_checkpoint == execution.checkpoint
                )
            ):
                continue
            terminal = terminal_checkpoint(
                execution.checkpoint,
                claim_epoch=execution.claim.claim_epoch,
                dispatched=execution.dispatched,
            )
            writes.append(
                BatchAccountingWrite(
                    execution.claim,
                    execution.stored_checkpoint,
                    terminal,
                )
            )
            execution.checkpoint = terminal
            execution.item.accounting_checkpoint = terminal.model_dump(mode="json")
        if writes:
            await self.checkpoints.write_many(writes, expires_at=deadline())
            for execution in executions:
                execution.stored_checkpoint = execution.checkpoint


def decode_checkpoint(value: object) -> BatchAccountingCheckpoint | None:
    if value is None:
        return None
    try:
        document = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
        if len(document.encode()) > CHECKPOINT_MAX_BYTES:
            raise ValueError("Checkpoint exceeds its bound")
        return BatchAccountingCheckpoint.model_validate_json(document)
    except (TypeError, ValueError):
        raise BatchAccountingUnavailable() from None


def terminal_checkpoint(
    checkpoint: BatchAccountingCheckpoint,
    *,
    claim_epoch: int,
    dispatched: bool,
) -> BatchAccountingCheckpoint:
    terminal = checkpoint.terminal
    if terminal is None:
        operation = checkpoint.operation
        audit = accounting_audit_envelope(
            operation,
            event_id=uuid5(checkpoint.operation_id, "provider-finalization:v2"),
            status="error",
            metadata={"batch": True, "outcome_unknown": dispatched},
        )
        terminal = (
            prepare_accounting_uncertain(
                operation,
                reason="batch_outcome_unknown",
                occurred_at=datetime.now(UTC),
                audit_envelope=audit,
            )
            if dispatched
            else prepare_accounting_not_dispatched(
                operation,
                occurred_at=datetime.now(UTC),
                audit_envelope=audit,
            )
        )
    return BatchAccountingCheckpoint(
        operation_id=checkpoint.operation_id,
        claim_epoch=claim_epoch,
        operation=checkpoint.operation,
        terminal=terminal,
    )


def deadline() -> float:
    return asyncio.get_running_loop().time() + 0.25


def _same_subject(
    reservation: AccountingReservation,
    auth: UserAPIKeyAuth,
    payload: ChatCompletionRequest | EmbeddingRequest,
) -> bool:
    owner = reservation.attribution
    return (
        owner.api_key == auth.api_key
        and owner.user_id == auth.user_id
        and owner.team_id == auth.team_id
        and owner.organization_id == auth.organization_id
        and owner.owner_account_id == auth.owner_account_id
        and owner.end_user_id == payload.user
        and reservation.request_fingerprint
        == request_fingerprint(operation_kind="batch", payload=payload.model_dump(mode="json"))
    )
