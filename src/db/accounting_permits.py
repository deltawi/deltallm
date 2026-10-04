"""Typed, bounded persistence for permit refills and claims across subjects."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeVar
from uuid import UUID

from src.billing.accounting_protocol import (
    DispatchPermit,
    PreissuedPermitAllocation,
    PreissuedPermitClaim,
    PreissuedPermitGrant,
    ReserveDecision,
)
from src.db.accounting_calls import (
    AccountingDatabaseCalls,
    AccountingProtocolUnavailable,
    AccountingQueryClient,
    AccountingResultFailure,
    outcome_may_be_ambiguous,
)

T = TypeVar("T")
AllocationResult = PreissuedPermitGrant | ReserveDecision


class AccountingPermitRepository:
    """Use one refill call or one claim call for up to 256 subjects or grants."""

    def __init__(
        self,
        db: AccountingQueryClient,
        *,
        owner_id: str,
        statement_budget_seconds: float = 0.25,
        grant_ttl_seconds: int = 30,
    ) -> None:
        if not 1 <= len(owner_id) <= 219:
            raise ValueError("permit owner ID must contain 1 to 219 characters")
        if not 1 <= grant_ttl_seconds <= 300:
            raise ValueError("accounting grant TTL must be between 1 and 300 seconds")
        self._owner_id = owner_id
        self._ttl = grant_ttl_seconds
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)

    async def allocate_batch(
        self, allocations: Sequence[PreissuedPermitAllocation], *, expires_at: float
    ) -> list[AllocationResult]:
        if not allocations:
            return []
        keys = [str(item.fence_token) for item in allocations]
        generation = _generation(item.reservation.protocol_generation for item in allocations)
        payload = _batch_payload([item.model_dump(mode="json") for item in allocations], keys)

        async def attempt(deadline: float) -> dict[str, AllocationResult]:
            rows = await self._calls.call(
                "allocate_permit_grants",
                "SELECT * FROM deltallm_accounting_allocate_permit_grants_batch("
                "$1,$2,$3::integer,$4::jsonb)",
                generation,
                self._owner_id,
                self._ttl,
                payload,
                expires_at=deadline,
            )
            by_key = _result_rows(rows, "allocation_fence_token", keys)
            return {
                str(item.fence_token): _allocation_result(
                    item, by_key[str(item.fence_token)], self._owner_id
                )
                for item in allocations
            }

        results, recovered = await _with_recovery(
            keys,
            attempt,
            lambda deadline: self._recover_allocations(allocations, payload, deadline),
            calls=self._calls,
            expires_at=expires_at,
        )
        for key, grant in recovered.items():
            if results[key] != grant:
                raise _invalid()
        return [results[key] for key in keys]

    async def claim_batch(
        self, claims: Sequence[PreissuedPermitClaim], *, expires_at: float
    ) -> list[DispatchPermit]:
        if not claims:
            return []
        keys = [str(item.reservation.operation_id) for item in claims]
        generation = _generation(item.reservation.protocol_generation for item in claims)
        if len({(item.grant.grant_id, item.permit_ordinal) for item in claims}) != len(claims):
            raise ValueError("one permit batch cannot repeat a grant ordinal")
        payload = _batch_payload([_claim_payload(item) for item in claims], keys)

        async def attempt(deadline: float) -> dict[str, DispatchPermit]:
            rows = await self._calls.call(
                "claim_permits",
                "SELECT * FROM deltallm_accounting_claim_permits_batch($1,$2::jsonb)",
                generation,
                payload,
                expires_at=deadline,
            )
            by_key = _result_rows(rows, "operation_id", keys)
            return {
                str(item.reservation.operation_id): _claim_result(
                    item, by_key[str(item.reservation.operation_id)]
                )
                for item in claims
            }

        results, recovered = await _with_recovery(
            keys,
            attempt,
            lambda deadline: self._recover_claims(claims, deadline),
            calls=self._calls,
            expires_at=expires_at,
        )
        for key, permit in recovered.items():
            if results[key] != permit:
                if results[key].decision is not ReserveDecision.REPLAY:
                    raise _invalid()
                results[key] = permit
        return [results[key] for key in keys]

    async def _recover_allocations(
        self, allocations: Sequence[PreissuedPermitAllocation], payload: str, expires_at: float
    ) -> dict[str, AllocationResult]:
        rows = await self._calls.call(
            "recover_permit_grants",
            "SELECT g.grant_id,g.generation,g.grantee_id,g.fence_token,g.accounting_partition,"
            "g.unit_allowance_exact AS allowance_exact,g.operation_limit,g.expires_at,g.state,"
            "g.dispatch_mode,(g.subject_key=deltallm_accounting_grant_subject("
            "value->'reservation')) AS subject_matches FROM jsonb_array_elements($1::jsonb) value "
            "JOIN deltallm_accounting_grants g ON g.fence_token=(value->>'fence_token')::uuid",
            payload,
            expires_at=expires_at,
        )
        expected = {str(item.fence_token): item for item in allocations}
        result: dict[str, AllocationResult] = {}
        for row in rows:
            key = str(row.get("fence_token"))
            item = expected.get(key)
            if (
                item is None
                or key in result
                or row.get("subject_matches") is not True
                or row.get("dispatch_mode") != "preissued"
                or row.get("state") != "active"
                or row.get("generation") != item.reservation.protocol_generation
            ):
                raise _invalid()
            result[key] = _grant_from_row(item, row, self._owner_id)
        return result

    async def _recover_claims(
        self, claims: Sequence[PreissuedPermitClaim], expires_at: float
    ) -> dict[str, DispatchPermit]:
        rows = await self._calls.call(
            "recover_permit_claims",
            "SELECT operation_id,owner_token,request_fingerprint,snapshot,accounting_protocol,"
            "accounting_generation,accounting_partition,accounting_state,accounting_grant_id,"
            "accounting_permit_ordinal,accounting_grant_fence_token "
            "FROM deltallm_billing_operations WHERE operation_id IN "
            "(SELECT value FROM jsonb_array_elements_text($1::jsonb))",
            json.dumps([str(item.reservation.operation_id) for item in claims]),
            expires_at=expires_at,
        )
        expected = {str(item.reservation.operation_id): item for item in claims}
        result: dict[str, DispatchPermit] = {}
        for row in rows:
            key = str(row.get("operation_id"))
            item = expected.get(key)
            if item is None or key in result or not _claim_identity_matches(item, row):
                raise _invalid()
            state = row.get("accounting_state")
            if state not in {"reserved", "finalized", "released", "provisional"}:
                raise _invalid()
            dispatch = state == "reserved"
            result[key] = DispatchPermit(
                protocol_generation=item.reservation.protocol_generation,
                operation_id=item.reservation.operation_id,
                decision=ReserveDecision.DISPATCH if dispatch else ReserveDecision.REPLAY,
                dispatch_token=item.reservation.owner_token if dispatch else None,
                accounting_partition=item.grant.accounting_partition if dispatch else None,
            )
        return result


async def _with_recovery(
    keys: Sequence[str],
    attempt: Callable[[float], Awaitable[dict[str, T]]],
    recover: Callable[[float], Awaitable[dict[str, T]]],
    *,
    calls: AccountingDatabaseCalls,
    expires_at: float,
) -> tuple[dict[str, T], dict[str, T]]:
    deadline = calls.attempt_deadline(expires_at)
    recovered: dict[str, T] = {}
    for number in range(3):
        try:
            return await attempt(deadline), recovered
        except AccountingProtocolUnavailable as exc:
            if exc.reason in {item.value for item in AccountingResultFailure}:
                raise
            if outcome_may_be_ambiguous(exc):
                try:
                    recovered.update(await recover(expires_at))
                except AccountingProtocolUnavailable as recovery_error:
                    if recovery_error.reason in {item.value for item in AccountingResultFailure}:
                        raise
                if len(recovered) == len(keys):
                    return recovered, recovered
            remaining = deadline - asyncio.get_running_loop().time()
            if number == 2 or remaining <= 0.01:
                raise
            await asyncio.sleep(min(0.01 * (number + 1), remaining / 2))
    raise _invalid()  # pragma: no cover - each attempt returns or raises


def _allocation_result(
    item: PreissuedPermitAllocation, row: Mapping[str, object], owner: str
) -> AllocationResult:
    try:
        decision = ReserveDecision(str(row["decision"]))
    except (KeyError, ValueError):
        raise _invalid() from None
    if decision in {ReserveDecision.BUDGET_EXHAUSTED, ReserveDecision.CAPACITY_EXHAUSTED}:
        if any(
            row.get(field) is not None
            for field in (
                "grant_id",
                "grantee_id",
                "fence_token",
                "accounting_partition",
                "allowance_exact",
                "operation_limit",
                "expires_at",
            )
        ):
            raise _invalid()
        return decision
    if decision is not ReserveDecision.DISPATCH:
        raise _invalid()
    return _grant_from_row(item, row, owner)


def _grant_from_row(
    item: PreissuedPermitAllocation, row: Mapping[str, object], owner: str
) -> PreissuedPermitGrant:
    try:
        timestamp = row["expires_at"]
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        grant = PreissuedPermitGrant(
            protocol_generation=item.reservation.protocol_generation,
            grant_id=row["grant_id"],
            grantee_id=row["grantee_id"],
            fence_token=UUID(str(row["fence_token"])),
            accounting_partition=row["accounting_partition"],
            allowance=Decimal(str(row["allowance_exact"])),
            operation_limit=row["operation_limit"],
            expires_at=timestamp,
        )
        if (
            grant.fence_token != item.fence_token
            or grant.grantee_id != f"{owner}:{item.fence_token}"
            or grant.allowance != item.reservation.allowance
            or grant.operation_limit > item.target_operations
            or grant.expires_at <= datetime.now(UTC)
        ):
            raise _invalid()
        return grant
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise _invalid() from None


def _claim_result(item: PreissuedPermitClaim, row: Mapping[str, object]) -> DispatchPermit:
    try:
        decision = ReserveDecision(str(row["decision"]))
        if decision not in {ReserveDecision.DISPATCH, ReserveDecision.REPLAY}:
            raise _invalid()
        dispatch = decision is ReserveDecision.DISPATCH
        if row.get("dispatch_token") != (
            str(item.reservation.owner_token) if dispatch else None
        ) or row.get("accounting_partition") != (
            item.grant.accounting_partition if dispatch else None
        ):
            raise _invalid()
        return DispatchPermit(
            protocol_generation=item.reservation.protocol_generation,
            operation_id=item.reservation.operation_id,
            decision=decision,
            dispatch_token=item.reservation.owner_token if dispatch else None,
            accounting_partition=item.grant.accounting_partition if dispatch else None,
        )
    except (KeyError, ValueError):
        raise _invalid() from None


def _claim_identity_matches(item: PreissuedPermitClaim, row: Mapping[str, object]) -> bool:
    reservation = item.reservation
    return (
        row.get("owner_token") == str(reservation.owner_token)
        and row.get("request_fingerprint") == reservation.request_fingerprint
        and row.get("snapshot") == reservation.model_dump(mode="json")
        and row.get("accounting_protocol") == "primary"
        and row.get("accounting_generation") == reservation.protocol_generation
        and row.get("accounting_partition") == item.grant.accounting_partition
        and row.get("accounting_grant_id") == item.grant.grant_id
        and row.get("accounting_permit_ordinal") == item.permit_ordinal
        and str(row.get("accounting_grant_fence_token")) == str(item.grant.fence_token)
    )


def _result_rows(
    rows: Sequence[Mapping[str, object]], field: str, keys: Sequence[str]
) -> dict[str, Mapping[str, object]]:
    result = {str(row.get(field)): row for row in rows}
    if len(result) != len(rows) or set(result) != set(keys):
        raise AccountingProtocolUnavailable(AccountingResultFailure.INCOMPLETE_RESULT)
    return result


def _batch_payload(values: list[dict[str, object]], keys: Sequence[str]) -> str:
    if len(values) > 256 or len(set(keys)) != len(keys):
        raise ValueError("permit batches must have up to 256 unique identities")
    payload = json.dumps(values, allow_nan=False, separators=(",", ":"), sort_keys=True)
    if len(payload.encode()) > 1_048_576:
        raise ValueError("permit batch exceeds its serialized size limit")
    return payload


def _claim_payload(item: PreissuedPermitClaim) -> dict[str, object]:
    return {
        "grant_id": item.grant.grant_id,
        "grantee_id": item.grant.grantee_id,
        "fence_token": str(item.grant.fence_token),
        "permit_ordinal": item.permit_ordinal,
        "reservation": item.reservation.model_dump(mode="json"),
    }


def _generation(values: Iterable[int]) -> int:
    generations = set(values)
    if len(generations) != 1:
        raise ValueError("one permit batch cannot mix protocol generations")
    return generations.pop()


def _invalid() -> AccountingProtocolUnavailable:
    return AccountingProtocolUnavailable(AccountingResultFailure.INVALID_RESULT)
