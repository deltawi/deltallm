"""Typed, bounded persistence for permit refills and claims across subjects."""

from __future__ import annotations

import json
from collections.abc import Sequence

from src.billing.accounting.accounting_protocol import (
    DispatchPermit,
    PreissuedPermitAllocation,
    PreissuedPermitClaim,
    PreissuedPermitGrant,
    ReserveDecision,
)
from src.db.accounting_batches import batch_payload, one_generation, result_rows, with_recovery
from src.db.accounting_calls import AccountingDatabaseCalls, AccountingQueryClient
from src.db.accounting_permit_results import (
    PERMIT_ALLOCATION_FIELDS,
    allocation_result,
    claim_identity_matches,
    claim_payload,
    claim_result,
    grant_from_row,
    invalid_result,
)

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
        generation = one_generation(item.reservation.protocol_generation for item in allocations)
        payload = batch_payload([item.model_dump(mode="json") for item in allocations], keys)

        async def attempt(deadline: float) -> dict[str, AllocationResult]:
            rows = await self._calls.call(
                "allocate_permit_grants",
                f"SELECT {PERMIT_ALLOCATION_FIELDS} FROM deltallm_accounting_allocate_permit_grants_batch("
                "$1,$2,$3::integer,$4::jsonb)",
                generation,
                self._owner_id,
                self._ttl,
                payload,
                expires_at=deadline,
            )
            by_key = result_rows(rows, "allocation_fence_token", keys)
            return {
                str(item.fence_token): allocation_result(
                    item, by_key[str(item.fence_token)], self._owner_id
                )
                for item in allocations
            }

        results, recovered = await with_recovery(
            keys,
            attempt,
            lambda deadline: self._recover_allocations(allocations, payload, deadline),
            calls=self._calls,
            expires_at=expires_at,
        )
        for key, grant in recovered.items():
            if results[key] != grant:
                raise invalid_result()
        return [results[key] for key in keys]

    async def claim_batch(
        self, claims: Sequence[PreissuedPermitClaim], *, expires_at: float
    ) -> list[DispatchPermit]:
        if not claims:
            return []
        keys = [str(item.reservation.operation_id) for item in claims]
        generation = one_generation(item.reservation.protocol_generation for item in claims)
        if len({(item.grant.grant_id, item.permit_ordinal) for item in claims}) != len(claims):
            raise ValueError("one permit batch cannot repeat a grant ordinal")
        payload = batch_payload([claim_payload(item) for item in claims], keys)

        async def attempt(deadline: float) -> dict[str, DispatchPermit]:
            rows = await self._calls.call(
                "claim_permits",
                "SELECT * FROM deltallm_accounting_claim_permits_batch($1,$2::jsonb)",
                generation,
                payload,
                expires_at=deadline,
            )
            by_key = result_rows(rows, "operation_id", keys)
            return {
                str(item.reservation.operation_id): claim_result(
                    item, by_key[str(item.reservation.operation_id)]
                )
                for item in claims
            }

        results, recovered = await with_recovery(
            keys,
            attempt,
            lambda deadline: self._recover_claims(claims, deadline),
            calls=self._calls,
            expires_at=expires_at,
        )
        for key, permit in recovered.items():
            if results[key] != permit:
                if results[key].decision is not ReserveDecision.REPLAY:
                    raise invalid_result()
                results[key] = permit
        return [results[key] for key in keys]

    async def _recover_allocations(
        self, allocations: Sequence[PreissuedPermitAllocation], payload: str, expires_at: float
    ) -> dict[str, AllocationResult]:
        rows = await self._calls.call(
            "recover_permit_grants",
            "SELECT g.grant_id,g.generation,g.grantee_id,g.fence_token,g.accounting_partition,"
            "g.unit_allowance_exact::text AS allowance_exact,g.operation_limit,g.expires_at,g.state,"
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
                raise invalid_result()
            result[key] = grant_from_row(item, row, self._owner_id)
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
            if item is None or key in result or not claim_identity_matches(item, row):
                raise invalid_result()
            state = row.get("accounting_state")
            if state not in {"reserved", "finalized", "released", "provisional"}:
                raise invalid_result()
            dispatch = state == "reserved"
            result[key] = DispatchPermit(
                protocol_generation=item.reservation.protocol_generation,
                operation_id=item.reservation.operation_id,
                decision=ReserveDecision.DISPATCH if dispatch else ReserveDecision.REPLAY,
                dispatch_token=item.reservation.owner_token if dispatch else None,
                accounting_partition=item.grant.accounting_partition if dispatch else None,
            )
        return result
