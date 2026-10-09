"""Bounded PostgreSQL adapter for accounting reservation and finalization batches."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from uuid import UUID

from src.billing.accounting.accounting_protocol import (
    AccountingFinalization,
    AccountingOutcome,
    AccountingReservation,
    DispatchPermit,
    FinalizationReceipt,
    ReserveDecision,
)
from src.db.accounting_calls import (
    AccountingDatabaseCalls,
    AccountingProtocolUnavailable,
    AccountingQueryClient,
    AccountingResultFailure,
    outcome_may_be_ambiguous as _outcome_may_be_ambiguous,
)
from src.db.telemetry_acceptance import AcceptanceFailure


class AccountingProtocolRepository:
    """Bounded, idempotent accounting admission and finalization."""

    def __init__(
        self,
        db: AccountingQueryClient,
        *,
        statement_budget_seconds: float = 0.25,
        grants_enabled: bool = True,
        grantee_id: str = "gateway",
        grant_target_operations: int = 32,
        grant_ttl_seconds: int = 30,
    ) -> None:
        if not 0.01 <= statement_budget_seconds <= 2.0:
            raise ValueError("accounting statement budget must be between 10ms and 2s")
        if not 1 <= len(grantee_id) <= 256:
            raise ValueError("accounting grantee ID must contain 1 to 256 characters")
        if not 1 <= grant_target_operations <= 1024:
            raise ValueError("accounting grant target must be between 1 and 1024 operations")
        if not 1 <= grant_ttl_seconds <= 300:
            raise ValueError("accounting grant TTL must be between 1 and 300 seconds")
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)
        self._statement_budget_seconds = statement_budget_seconds
        self._grants_enabled = grants_enabled
        self._grantee_id = grantee_id
        self._grant_target_operations = grant_target_operations
        self._grant_ttl_seconds = grant_ttl_seconds

    async def protocol_ready(self, generation: int) -> bool:
        rows = await self._call(
            "query",
            "SELECT EXISTS (SELECT 1 FROM deltallm_accounting_protocols "
            "WHERE protocol_name='primary' AND generation=$1 "
            "AND writer_version=2 AND state='active') AS ready",
            generation,
            expires_at=asyncio.get_running_loop().time() + self._statement_budget_seconds,
        )
        return len(rows) == 1 and rows[0].get("ready") is True

    async def reserve_batch(
        self, reservations: Sequence[AccountingReservation], *, expires_at: float
    ) -> list[DispatchPermit]:
        if not reservations:
            return []
        attempt_expires_at = self._attempt_deadline(expires_at)
        recovered: dict[str, int] = {}
        last_failure: AccountingProtocolUnavailable | None = None
        for attempt in range(3):
            try:
                results = await self._reserve_once(
                    reservations,
                    expires_at=attempt_expires_at,
                )
            except AccountingProtocolUnavailable as exc:
                last_failure = exc
                if _outcome_may_be_ambiguous(exc):
                    try:
                        recovered.update(
                            await self._recover_reservations(
                                reservations,
                                expires_at=expires_at,
                            )
                        )
                    except AccountingProtocolUnavailable as recovery_failure:
                        if recovery_failure.reason == AccountingResultFailure.INVALID_RESULT.value:
                            raise
                    if len(recovered) == len(reservations):
                        return _recovered_permits(reservations, recovered)
                remaining = attempt_expires_at - asyncio.get_running_loop().time()
                if attempt == 2 or remaining <= 0.01:
                    raise
                await asyncio.sleep(min(0.01 * (attempt + 1), remaining / 2))
                continue
            if not recovered:
                return results
            promoted = []
            for item, result in zip(reservations, results, strict=True):
                partition = recovered.get(str(item.operation_id))
                if partition is None:
                    promoted.append(result)
                elif result.decision is ReserveDecision.REPLAY:
                    promoted.append(
                        DispatchPermit(
                            protocol_generation=item.protocol_generation,
                            operation_id=item.operation_id,
                            decision=ReserveDecision.DISPATCH,
                            dispatch_token=item.owner_token,
                            accounting_partition=partition,
                        )
                    )
                else:
                    raise AccountingProtocolUnavailable(AccountingResultFailure.INVALID_RESULT)
            return promoted
        if last_failure is not None:  # pragma: no cover - the loop always returns or raises
            raise last_failure
        raise AccountingProtocolUnavailable(AccountingResultFailure.INCOMPLETE_RESULT)

    async def _reserve_once(
        self, reservations: Sequence[AccountingReservation], *, expires_at: float
    ) -> list[DispatchPermit]:
        if not reservations:
            return []
        generation = _one_generation(item.protocol_generation for item in reservations)
        if self._grants_enabled:
            rows = await self._call(
                "admit_grant",
                "SELECT * FROM deltallm_accounting_admit_grant_batch("
                "$1,$2,$3::integer,$4::integer,$5::jsonb)",
                generation,
                self._grantee_id,
                self._grant_target_operations,
                self._grant_ttl_seconds,
                _payload(reservations),
                expires_at=expires_at,
            )
            by_id = {str(row["operation_id"]): row for row in rows}
            invalid_decisions = {str(row["decision"]) for row in rows} - {
                ReserveDecision.DISPATCH.value,
                ReserveDecision.REPLAY.value,
                ReserveDecision.BUDGET_EXHAUSTED.value,
                ReserveDecision.CAPACITY_EXHAUSTED.value,
            }
            if invalid_decisions:
                raise AccountingProtocolUnavailable(AccountingResultFailure.INVALID_RESULT)
        else:
            rows = await self._call(
                "reserve_direct",
                "SELECT * FROM deltallm_accounting_reserve_batch($1,$2::jsonb)",
                generation,
                _payload(reservations),
                expires_at=expires_at,
            )
            by_id = {str(row["operation_id"]): row for row in rows}
        if len(by_id) != len(reservations):
            raise AccountingProtocolUnavailable(AccountingResultFailure.INCOMPLETE_RESULT)
        return [
            DispatchPermit(
                protocol_generation=generation,
                operation_id=item.operation_id,
                decision=ReserveDecision(str(by_id[str(item.operation_id)]["decision"])),
                dispatch_token=(
                    UUID(str(by_id[str(item.operation_id)]["dispatch_token"]))
                    if by_id[str(item.operation_id)].get("dispatch_token") is not None
                    else None
                ),
                accounting_partition=by_id[str(item.operation_id)].get("accounting_partition"),
            )
            for item in reservations
        ]

    async def _recover_reservations(
        self,
        reservations: Sequence[AccountingReservation],
        *,
        expires_at: float,
    ) -> dict[str, int]:
        rows = await self._call(
            "recover_reservation",
            "SELECT operation_id,owner_token,request_fingerprint,accounting_protocol,"
            "accounting_generation,accounting_partition,accounting_state "
            "FROM deltallm_billing_operations WHERE operation_id IN "
            "(SELECT value FROM jsonb_array_elements_text($1::jsonb))",
            json.dumps([str(item.operation_id) for item in reservations]),
            expires_at=expires_at,
        )
        by_id = {str(item.operation_id): item for item in reservations}
        recovered: dict[str, int] = {}
        for row in rows:
            operation_id = str(row["operation_id"])
            item = by_id.get(operation_id)
            if item is None:
                raise AccountingProtocolUnavailable(AccountingResultFailure.INVALID_RESULT)
            if (
                str(row.get("owner_token")) != str(item.owner_token)
                or str(row.get("request_fingerprint")) != item.request_fingerprint
                or str(row.get("accounting_protocol")) != "primary"
                or int(row.get("accounting_generation") or 0) != item.protocol_generation
            ):
                raise AccountingProtocolUnavailable(AccountingResultFailure.INVALID_RESULT)
            if row.get("accounting_state") != "reserved" or row.get("accounting_partition") is None:
                continue
            recovered[operation_id] = int(row["accounting_partition"])
        return recovered

    async def finalize_batch(
        self, finalizations: Sequence[AccountingFinalization], *, expires_at: float
    ) -> list[FinalizationReceipt]:
        if not finalizations:
            return []
        attempt_expires_at = self._attempt_deadline(expires_at)
        generation = _one_generation(item.protocol_generation for item in finalizations)
        function_name = (
            "deltallm_accounting_finalize_grant_batch"
            if self._grants_enabled
            else "deltallm_accounting_finalize_batch"
        )
        rows = None
        for attempt in range(3):
            try:
                rows = await self._call(
                    "finalize_grant" if self._grants_enabled else "finalize_direct",
                    f"SELECT * FROM {function_name}($1,$2::jsonb)",
                    generation,
                    _payload(finalizations),
                    expires_at=attempt_expires_at,
                )
                break
            except AccountingProtocolUnavailable as exc:
                if _outcome_may_be_ambiguous(exc):
                    try:
                        recovered = await self._recover_finalizations(
                            finalizations,
                            expires_at=expires_at,
                        )
                    except AccountingProtocolUnavailable as recovery_failure:
                        if recovery_failure.reason == AccountingResultFailure.INVALID_RESULT.value:
                            raise
                    else:
                        if len(recovered) == len(finalizations):
                            return [recovered[str(item.operation_id)] for item in finalizations]
                remaining = attempt_expires_at - asyncio.get_running_loop().time()
                if attempt == 2 or remaining <= 0.01:
                    raise
                await asyncio.sleep(min(0.01 * (attempt + 1), remaining / 2))
        if rows is None:
            raise AccountingProtocolUnavailable(AcceptanceFailure.UNKNOWN)
        by_id = {str(row["operation_id"]): row for row in rows}
        if len(by_id) != len(finalizations):
            raise AccountingProtocolUnavailable(AccountingResultFailure.INCOMPLETE_RESULT)
        return [
            FinalizationReceipt(
                protocol_generation=generation,
                operation_id=item.operation_id,
                event_sequence=int(by_id[str(item.operation_id)]["event_sequence"]),
                outcome=AccountingOutcome(str(by_id[str(item.operation_id)]["outcome"])),
                replayed=bool(by_id[str(item.operation_id)]["replayed"]),
            )
            for item in finalizations
        ]

    async def _recover_finalizations(
        self,
        finalizations: Sequence[AccountingFinalization],
        *,
        expires_at: float,
    ) -> dict[str, FinalizationReceipt]:
        rows = await self._call(
            "recover_finalization",
            "SELECT b.operation_id,b.owner_token,b.request_fingerprint,"
            "b.accounting_protocol,b.accounting_generation,e.sequence,e.event_id,"
            "e.component_id,e.outcome,e.payload_json,e.audit_envelope_json "
            "FROM deltallm_billing_operations b JOIN deltallm_accounting_events e "
            "ON e.operation_id=b.operation_id AND e.event_type='finalized' "
            "WHERE b.operation_id IN "
            "(SELECT value FROM jsonb_array_elements_text($1::jsonb))",
            json.dumps([str(item.operation_id) for item in finalizations]),
            expires_at=expires_at,
        )
        by_id = {str(item.operation_id): item for item in finalizations}
        recovered: dict[str, FinalizationReceipt] = {}
        for row in rows:
            operation_id = str(row["operation_id"])
            item = by_id.get(operation_id)
            if item is None or str(row.get("component_id")) != item.component_id:
                continue
            serialized = item.model_dump(mode="json")
            payload = row.get("payload_json")
            if (
                str(row.get("owner_token")) != str(item.owner_token)
                or str(row.get("request_fingerprint")) != item.request_fingerprint
                or str(row.get("accounting_protocol")) != "primary"
                or int(row.get("accounting_generation") or 0) != item.protocol_generation
                or str(row.get("event_id")) != str(item.event_id)
                or str(row.get("outcome")) != item.outcome.value
                or not isinstance(payload, dict)
                or payload.get("spend") != serialized["spend_payload"]
                or not _optional_decimal_equal(payload.get("exact_charge"), item.exact_charge)
                or payload.get("uncertainty_reason") != item.uncertainty_reason
                or not _int_equal(payload.get("unresolved_attempts"), item.unresolved_attempts)
                or row.get("audit_envelope_json") != serialized["audit_envelope"]
            ):
                raise AccountingProtocolUnavailable(AccountingResultFailure.INVALID_RESULT)
            recovered[operation_id] = FinalizationReceipt(
                protocol_generation=item.protocol_generation,
                operation_id=item.operation_id,
                event_sequence=int(row["sequence"]),
                outcome=item.outcome,
                replayed=True,
            )
        return recovered

    async def _call(
        self, operation: str, query: str, *parameters: object, expires_at: float
    ) -> Sequence[Mapping[str, object]]:
        return await self._calls.call(operation, query, *parameters, expires_at=expires_at)

    def _attempt_deadline(self, expires_at: float) -> float:
        """Reserve one statement window for exact ambiguity recovery."""

        return self._calls.attempt_deadline(expires_at)


def _one_generation(generations) -> int:
    values = set(generations)
    if len(values) != 1:
        raise ValueError("one accounting batch cannot mix protocol generations")
    return values.pop()


def _optional_decimal_equal(actual: object, expected: Decimal | None) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    try:
        return Decimal(str(actual)) == expected
    except (InvalidOperation, ValueError):
        return False


def _int_equal(actual: object, expected: int) -> bool:
    try:
        return int(actual or 0) == expected
    except (TypeError, ValueError):
        return False


def _recovered_permits(
    reservations: Sequence[AccountingReservation], recovered: dict[str, int]
) -> list[DispatchPermit]:
    return [
        DispatchPermit(
            protocol_generation=item.protocol_generation,
            operation_id=item.operation_id,
            decision=ReserveDecision.DISPATCH,
            dispatch_token=item.owner_token,
            accounting_partition=recovered[str(item.operation_id)],
        )
        for item in reservations
    ]


def _payload(values: Sequence[AccountingReservation | AccountingFinalization]) -> str:
    return json.dumps(
        [value.model_dump(mode="json") for value in values],
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
