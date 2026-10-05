"""Freeze complete local terminals and acknowledge all issued proofs together."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
import json
import math
from typing import Protocol

from src.billing.accounting_local_leases import LocalPermitFinalization
from src.billing.accounting_local_receipts import LocalReceiptStore, RetainedLocalReceipt
from src.billing.accounting_protocol import AccountingFinalization, FinalizationReceipt
from src.billing.accounting_snapshots import finalization_bytes
from src.billing.durable_microbatch import DurableBatchFull
from src.db.accounting_calls import AccountingProtocolUnavailable
from src.db.accounting_permit_results import invalid_result
from src.db.telemetry_acceptance import AcceptanceFailure


class LocalTerminalPersistence(Protocol):
    async def finalize_batch(
        self, values: Sequence[LocalPermitFinalization], *, expires_at: float
    ) -> Sequence[FinalizationReceipt]: ...


class LocalTerminalOwner:
    def __init__(
        self, persistence: LocalTerminalPersistence, receipts: LocalReceiptStore, *, generation: int
    ) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("local terminal generation is invalid")
        self._persistence = persistence
        self._receipts = receipts
        self._generation = generation

    @property
    def generation(self) -> int:
        return self._generation

    def owns_receipts(self, receipts: LocalReceiptStore) -> bool:
        return self._receipts is receipts

    async def finalize_batch(
        self, values: Sequence[LocalPermitFinalization], *, expires_at: float
    ) -> tuple[FinalizationReceipt, ...]:
        frozen = freeze_local_terminals(values, generation=self._generation)
        if not frozen:
            return ()
        _caller_deadline(expires_at)
        async with asyncio.timeout_at(expires_at):
            results = await self._persistence.finalize_batch(frozen, expires_at=expires_at)
        acknowledgements = validated_terminal_acks(frozen, results)
        prepared = self._receipts.prepare_acknowledgements(
            tuple((value.receipt, ack) for value, ack in zip(frozen, acknowledgements, strict=True))
        )
        _caller_deadline(expires_at)
        self._receipts._commit_acknowledgements(prepared)
        return acknowledgements


def _caller_deadline(expires_at: float) -> None:
    if not math.isfinite(expires_at) or expires_at <= asyncio.get_running_loop().time():
        raise AccountingProtocolUnavailable(AcceptanceFailure.DEADLINE)


def freeze_local_terminals(
    values: Sequence[LocalPermitFinalization], *, generation: int
) -> tuple[LocalPermitFinalization, ...]:
    if len(values) > 256:
        raise ValueError("local terminal batch exceeds its entry limit")
    copies, size = [], 2
    for value in values:
        receipt = RetainedLocalReceipt.freeze(value.receipt).restore()
        finalization = AccountingFinalization.model_validate_json(
            finalization_bytes(value.finalization)
        )
        copy = LocalPermitFinalization(receipt=receipt, finalization=finalization)
        if finalization.protocol_generation != generation:
            raise ValueError("local terminal uses a stale generation")
        encoded = json.dumps(
            copy.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        size += len(encoded) + bool(copies)
        if size > 1_048_576:
            raise DurableBatchFull("local terminal batch exceeds its byte limit")
        copies.append(copy)
    if len({copy.finalization.operation_id for copy in copies}) != len(copies):
        raise ValueError("local terminal batch repeats an operation")
    return tuple(copies)


def local_terminal_bytes(value: LocalPermitFinalization, *, generation: int) -> bytes:
    frozen = freeze_local_terminals((value,), generation=generation)[0]
    return json.dumps(
        frozen.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def validated_terminal_acks(
    values: Sequence[LocalPermitFinalization], results: Sequence[FinalizationReceipt]
) -> tuple[FinalizationReceipt, ...]:
    if len(results) != len(values):
        raise invalid_result()
    copies = []
    for value, result in zip(values, results, strict=True):
        if not isinstance(result, FinalizationReceipt):
            raise invalid_result()
        try:
            copy = FinalizationReceipt.model_validate(result.model_dump())
        except ValueError:
            raise invalid_result() from None
        finalization = value.finalization
        if (
            copy.protocol_generation != finalization.protocol_generation
            or copy.operation_id != finalization.operation_id
            or copy.outcome is not finalization.outcome
        ):
            raise invalid_result()
        copies.append(copy)
    return tuple(copies)
