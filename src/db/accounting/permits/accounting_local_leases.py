"""Bounded bulk funding, suffix return, and terminal persistence for local issues."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitFinalization,
    LocalPermitGrant,
    LocalPermitReturn,
)
from src.billing.accounting.accounting_protocol import (
    FinalizationReceipt,
    PreissuedPermitAllocation,
    ReserveDecision,
)
from src.billing.accounting.journal.accounting_terminal_snapshots import (
    FrozenLocalTerminal,
    LocalTerminalValue,
)
from src.db.accounting.accounting_batches import (
    batch_payload,
    one_generation,
    result_rows,
    with_recovery,
)
from src.db.accounting.accounting_calls import AccountingDatabaseCalls, AccountingQueryClient
from src.db.accounting.permits.accounting_local_lease_results import (
    allocation_result,
    finalization_payload,
    finalization_result,
    recovered_finalization,
    recovered_grant,
    recovered_return,
    return_payload,
    return_result,
    same_funding,
)
from src.db.accounting.permits.accounting_permit_results import (
    PERMIT_ALLOCATION_FIELDS,
    invalid_result,
)

FundingResult = LocalPermitGrant | ReserveDecision
_RECOVERY_GRANT_FIELDS = (
    "g.grant_id,g.protocol_name,g.generation,g.grantee_id,g.fence_token,"
    "g.accounting_partition,g.dispatch_mode,g.local_dispatch,g.state,"
    "g.unit_allowance_exact::text AS unit_allowance_exact,g.operation_limit,"
    "g.consumed_operations,g.returned_operations,g.returned_exact::text AS returned_exact,"
    "g.dispatch_expires_at,g.expires_at"
)


class AccountingLocalLeaseRepository:
    """Use one call per phase for up to 256 subjects or grants, never a subject loop."""

    def __init__(
        self,
        db: AccountingQueryClient,
        *,
        owner_id: str,
        statement_budget_seconds: float = 0.25,
        grant_ttl_seconds: int = 30,
    ) -> None:
        if not 1 <= len(owner_id) <= 219:
            raise ValueError("local lease owner ID must contain 1 to 219 characters")
        if not 1 <= grant_ttl_seconds <= 300:
            raise ValueError("accounting grant TTL must be between 1 and 300 seconds")
        self._owner_id = owner_id
        self._ttl = grant_ttl_seconds
        self._calls = AccountingDatabaseCalls(db, statement_budget_seconds=statement_budget_seconds)

    async def allocate_batch(
        self, allocations: Sequence[PreissuedPermitAllocation], *, expires_at: float
    ) -> list[FundingResult]:
        if not allocations:
            return []
        keys = [str(item.fence_token) for item in allocations]
        generation = one_generation(item.reservation.protocol_generation for item in allocations)
        payload = batch_payload([item.model_dump(mode="json") for item in allocations], keys)

        async def attempt(deadline: float) -> dict[str, FundingResult]:
            observed = asyncio.get_running_loop().time()
            rows = await self._calls.call(
                "allocate_local_permit_grants",
                f"SELECT {PERMIT_ALLOCATION_FIELDS},dispatch_expires_at,observed_at "
                "FROM deltallm_accounting_allocate_local_permit_grants_batch("
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
                    item, by_key[str(item.fence_token)], self._owner_id, observed
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
        if any(not same_funding(results[key], grant) for key, grant in recovered.items()):
            raise invalid_result()
        return [results[key] for key in keys]

    async def return_batch(
        self, returns: Sequence[LocalPermitReturn], *, expires_at: float
    ) -> list[int]:
        if not returns:
            return []
        keys = [item.grant.grant_id for item in returns]
        generation = one_generation(item.grant.protocol_generation for item in returns)
        payload = batch_payload([return_payload(item) for item in returns], keys)

        async def attempt(deadline: float) -> dict[str, int]:
            rows = await self._calls.call(
                "return_local_permits",
                "SELECT * FROM deltallm_accounting_return_local_permits_batch($1,$2,$3::jsonb)",
                generation,
                self._owner_id,
                payload,
                expires_at=deadline,
            )
            by_key = result_rows(rows, "grant_id", keys)
            return {
                item.grant.grant_id: return_result(item, by_key[item.grant.grant_id])
                for item in returns
            }

        results, recovered = await with_recovery(
            keys,
            attempt,
            lambda deadline: self._recover_returns(returns, payload, deadline),
            calls=self._calls,
            expires_at=expires_at,
        )
        if any(results[key] != count for key, count in recovered.items()):
            raise invalid_result()
        return [results[key] for key in keys]

    async def finalize_batch(
        self, finalizations: Sequence[LocalTerminalValue], *, expires_at: float
    ) -> list[FinalizationReceipt]:
        if not finalizations:
            return []
        # The non-native adapter keeps its current payload contract. Restore a
        # private graph here; native journal and transport owners reuse bytes.
        finalizations = tuple(
            value.restore() if isinstance(value, FrozenLocalTerminal) else value
            for value in finalizations
        )
        keys = [str(item.finalization.operation_id) for item in finalizations]
        generation = one_generation(item.finalization.protocol_generation for item in finalizations)
        if len(
            {(item.receipt.grant.grant_id, item.receipt.permit_ordinal) for item in finalizations}
        ) != len(finalizations):
            raise ValueError("one local terminal batch cannot repeat a grant ordinal")
        payload = batch_payload([finalization_payload(item) for item in finalizations], keys)

        async def attempt(deadline: float) -> dict[str, FinalizationReceipt]:
            rows = await self._calls.call(
                "finalize_local_permits",
                "SELECT * FROM deltallm_accounting_finalize_local_permit_batch($1,$2::jsonb)",
                generation,
                payload,
                expires_at=deadline,
            )
            by_key = result_rows(rows, "operation_id", keys)
            return {
                str(item.finalization.operation_id): finalization_result(
                    item, by_key[str(item.finalization.operation_id)]
                )
                for item in finalizations
            }

        results, recovered = await with_recovery(
            keys,
            attempt,
            lambda deadline: self._recover_finalizations(finalizations, payload, deadline),
            calls=self._calls,
            expires_at=expires_at,
        )
        for key, receipt in recovered.items():
            if results[key].model_copy(update={"replayed": True}) != receipt:
                raise invalid_result()
            results[key] = receipt
        return [results[key] for key in keys]

    async def _recover_allocations(
        self, allocations: Sequence[PreissuedPermitAllocation], payload: str, expires_at: float
    ) -> dict[str, FundingResult]:
        observed = asyncio.get_running_loop().time()
        rows = await self._calls.call(
            "recover_local_permit_grants",
            f"SELECT {_RECOVERY_GRANT_FIELDS},CURRENT_TIMESTAMP AS observed_at,"
            "(g.subject_key=deltallm_accounting_grant_subject(value->'reservation')) AS subject_matches "
            "FROM jsonb_array_elements($1::jsonb) value CROSS JOIN LATERAL "
            "(SELECT g.* FROM deltallm_accounting_grants g "
            "WHERE g.fence_token=(value->>'fence_token')::uuid OFFSET 0) g",
            payload,
            expires_at=expires_at,
        )
        expected = {str(item.fence_token): item for item in allocations}
        result: dict[str, FundingResult] = {}
        for row in rows:
            key = str(row.get("fence_token"))
            if key not in expected or key in result:
                raise invalid_result()
            result[key] = recovered_grant(expected[key], row, self._owner_id, observed)
        return result

    async def _recover_returns(
        self, returns: Sequence[LocalPermitReturn], payload: str, expires_at: float
    ) -> dict[str, int]:
        rows = await self._calls.call(
            "recover_local_permit_returns",
            f"SELECT {_RECOVERY_GRANT_FIELDS} FROM jsonb_array_elements($1::jsonb) value CROSS JOIN LATERAL "
            "(SELECT g.* FROM deltallm_accounting_grants g "
            "WHERE g.grant_id=value->>'grant_id' OFFSET 0) g",
            payload,
            expires_at=expires_at,
        )
        expected = {item.grant.grant_id: item for item in returns}
        result: dict[str, int] = {}
        for row in rows:
            key = str(row.get("grant_id"))
            if key not in expected or key in result:
                raise invalid_result()
            count = recovered_return(expected[key], row, self._owner_id)
            if count is not None:
                result[key] = count
        return result

    async def _recover_finalizations(
        self, finalizations: Sequence[LocalPermitFinalization], payload: str, expires_at: float
    ) -> dict[str, FinalizationReceipt]:
        rows = await self._calls.call(
            "recover_local_permit_finalizations",
            "SELECT b.*,e.sequence,e.event_id,e.component_id,e.outcome,e.payload_json,"
            "e.operation_id AS event_operation_id,e.protocol_name AS event_protocol,"
            "e.generation AS event_generation,e.event_type,"
            "e.audit_envelope_json,e.occurred_at FROM jsonb_array_elements($1::jsonb) value "
            "CROSS JOIN LATERAL (SELECT b.* FROM deltallm_billing_operations b "
            "WHERE b.operation_id=value#>>'{reservation,operation_id}' OFFSET 0) b "
            "CROSS JOIN LATERAL (SELECT e.* FROM deltallm_accounting_events e "
            "WHERE e.event_id=value#>>'{finalization,event_id}' OFFSET 0) e",
            payload,
            expires_at=expires_at,
        )
        expected = {str(item.finalization.operation_id): item for item in finalizations}
        result: dict[str, FinalizationReceipt] = {}
        for row in rows:
            key = str(row.get("operation_id"))
            if key not in expected or key in result:
                raise invalid_result()
            result[key] = recovered_finalization(expected[key], row)
        return result
