"""Remote persistence reuses the API's local issue and terminal owners."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import math

from pydantic import TypeAdapter

from src.billing.accounting.transport.accounting_http import AccountingHttpTransport
from src.billing.accounting.permits.accounting_local_leases import (
    LocalPermitGrant,
    LocalPermitReturn,
)
from src.billing.accounting.journal.accounting_local_terminal import validated_terminal_acks
from src.billing.accounting.journal.accounting_terminal_snapshots import (
    LocalTerminalValue,
    freeze_terminal_snapshots,
)
from src.billing.accounting.transport.accounting_local_wire import (
    WireLocalGrant,
    wire_local_terminals,
)
from src.billing.accounting.accounting_protocol import PreissuedPermitAllocation, ReserveDecision
from src.billing.accounting.transport.accounting_rpc_contracts import (
    AccountingRpcHealth,
    FUNDING_REPLIES,
    RETURN_REPLIES,
    WireLocalReturn,
    LocalFundingRequest,
    LocalReturnRequest,
    rpc_batch_bytes,
    rpc_request_bytes,
)
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.db.telemetry_acceptance import AcceptanceFailure


_JOURNAL_REPLIES = TypeAdapter(list[JournalReceipt])


class RemoteLocalLeasePersistence:
    def __init__(
        self, transport: AccountingHttpTransport, *, generation: int, owner_id: str
    ) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("remote accounting generation is invalid")
        if type(owner_id) is not str or not 1 <= len(owner_id) <= 219:
            raise ValueError("remote accounting owner is invalid")
        self._transport = transport
        self._generation = generation
        self._owner = owner_id

    async def protocol_ready(self, generation: int) -> bool:
        if type(generation) is not int or generation != self._generation:
            return False
        return await self.observe_ready(expires_at=asyncio.get_running_loop().time() + 0.25)

    async def observe_ready(self, *, expires_at: float) -> bool:
        try:
            body = await self._transport.request("/health", b"", expires_at=expires_at)
            value = AccountingRpcHealth.model_validate_json(body)
            return value.generation == self._generation
        except (AccountingProtocolUnavailable, ValueError):
            return False

    async def allocate_batch(
        self, values: Sequence[PreissuedPermitAllocation], *, expires_at: float
    ) -> tuple[LocalPermitGrant | ReserveDecision, ...]:
        if not values:
            return ()
        payload = self._payload(values=rpc_batch_bytes(values), expires_at=expires_at, owner=True)
        values = LocalFundingRequest.model_validate_json(payload).values
        observed = asyncio.get_running_loop().time()
        body = await self._transport.request(
            "/allocate/local/batch", payload, expires_at=expires_at
        )
        try:
            replies = FUNDING_REPLIES.validate_json(body)
            if len(replies) != len(values):
                raise invalid_result()
            result = []
            for item, reply in zip(values, replies, strict=True):
                if (
                    item.reservation.protocol_generation != self._generation
                    or reply.allocation_fence_token != item.fence_token
                ):
                    raise invalid_result()
                grant = reply.grant
                if grant is None:
                    result.append(reply.decision)
                else:
                    if (
                        grant.protocol_generation != self._generation
                        or grant.grantee_id != f"{self._owner}:{item.fence_token}"
                        or grant.allowance != item.reservation.allowance
                        or grant.operation_limit > item.target_operations
                    ):
                        raise invalid_result()
                    result.append(grant.restore(observed_monotonic=observed))
            return tuple(result)
        except ValueError:
            raise invalid_result() from None

    async def return_batch(
        self, values: Sequence[LocalPermitReturn], *, expires_at: float
    ) -> tuple[int, ...]:
        if not values:
            return ()
        wire = tuple(
            WireLocalReturn(
                grant=WireLocalGrant(**value.grant.model_dump(exclude={"observed_monotonic"})),
                first_unused_ordinal=value.first_unused_ordinal,
            )
            for value in values
        )
        payload = self._payload(values=rpc_batch_bytes(wire), expires_at=expires_at, owner=True)
        request = LocalReturnRequest.model_validate_json(payload)
        values = tuple(
            value.restore(observed_monotonic=asyncio.get_running_loop().time())
            for value in request.values
        )
        body = await self._transport.request("/return/local/batch", payload, expires_at=expires_at)
        try:
            replies = RETURN_REPLIES.validate_json(body)
            if len(replies) != len(values):
                raise invalid_result()
            result = []
            for value, reply in zip(values, replies, strict=True):
                grant = value.grant
                expected = grant.operation_limit - value.first_unused_ordinal
                if (
                    reply.grant_id != grant.grant_id
                    or reply.fence_token != grant.fence_token
                    or reply.first_unused_ordinal != value.first_unused_ordinal
                    or reply.returned_operations != expected
                ):
                    raise invalid_result()
                result.append(expected)
            return tuple(result)
        except ValueError:
            raise invalid_result() from None

    async def finalize_batch(
        self, values: Sequence[LocalTerminalValue], *, expires_at: float
    ) -> tuple[JournalReceipt, ...]:
        if not values:
            return ()
        values = freeze_terminal_snapshots(values, generation=self._generation)
        payload = self._payload(
            values=wire_local_terminals(values, generation=self._generation),
            expires_at=expires_at,
            owner=False,
        )
        body = await self._transport.request(
            "/finalize/local/batch", payload, expires_at=expires_at
        )
        try:
            replies = _JOURNAL_REPLIES.validate_json(body)
        except ValueError:
            raise invalid_result() from None
        return validated_terminal_acks(values, replies, receipt_type=JournalReceipt)

    def _payload(self, *, values: bytes, expires_at: float, owner: bool) -> bytes:
        remaining = expires_at - asyncio.get_running_loop().time()
        if not math.isfinite(remaining) or remaining <= 0:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)
        return rpc_request_bytes(
            generation=self._generation,
            budget_ms=max(1, min(5000, math.floor(remaining * 1000))),
            owner_id=self._owner if owner else None,
            values=values,
        )
