"""Validate complete terminal proofs once and retain only immutable facts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
from uuid import UUID

from src.billing.accounting_local_leases import LocalPermitFinalization, LocalPermitReceipt
from src.billing.accounting_local_receipts import RetainedLocalReceipt
from src.billing.accounting_protocol import AccountingFinalization, AccountingOutcome
from src.billing.durable_microbatch import DurableBatchFull
from src.billing.money import money_string

MAX_TERMINAL_BATCH_BYTES = 1_048_576


@dataclass(frozen=True, slots=True, init=False, repr=False)
class FrozenLocalTerminal:
    document: bytes
    reservation_json: bytes
    finalization_json: bytes
    journal_identity_json: bytes
    retained_receipt: RetainedLocalReceipt
    operation_id: UUID
    generation: int
    outcome: AccountingOutcome

    def __init__(self, value: LocalPermitFinalization | bytes, *, generation: int) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("local terminal generation is invalid")
        if isinstance(value, bytes):
            if len(value) + 2 > MAX_TERMINAL_BATCH_BYTES:
                raise DurableBatchFull("local terminal batch exceeds its byte limit")
            copy = LocalPermitFinalization.model_validate_json(value)
            retained, _ = RetainedLocalReceipt.prepare(copy.receipt)
        else:
            if not isinstance(value.finalization, AccountingFinalization):
                raise ValueError("local terminal model has invalid finalization fields")
            # Check each mutable graph through its normal validator. prepare
            # already checks the complete receipt, including scalar model_copy
            # fields. Do not check and copy that same graph a second time.
            retained, receipt = RetainedLocalReceipt.prepare(value.receipt)
            finalization = AccountingFinalization.model_validate(value.finalization.model_dump())
            copy = LocalPermitFinalization(receipt=receipt, finalization=finalization)
        if copy.finalization.protocol_generation != generation:
            raise ValueError("local terminal uses a stale generation")
        document = _json_bytes(copy.model_dump(mode="json"))
        if len(document) + 2 > MAX_TERMINAL_BATCH_BYTES:
            raise DurableBatchFull("local terminal batch exceeds its byte limit")
        finalization = copy.finalization
        reservation_json = retained.reservation_json
        finalization_json = _json_bytes(finalization.model_dump(mode="json"))
        object.__setattr__(self, "document", document)
        object.__setattr__(self, "reservation_json", reservation_json)
        object.__setattr__(self, "finalization_json", finalization_json)
        object.__setattr__(
            self,
            "journal_identity_json",
            _journal_identity(copy, reservation_json, finalization_json),
        )
        object.__setattr__(
            self,
            "retained_receipt",
            retained,
        )
        object.__setattr__(self, "operation_id", finalization.operation_id)
        object.__setattr__(self, "generation", generation)
        object.__setattr__(self, "outcome", finalization.outcome)

    @property
    def retained_bytes(self) -> int:
        return (
            8192
            + sum(
                len(value)
                for value in (
                    self.document,
                    self.reservation_json,
                    self.finalization_json,
                    self.journal_identity_json,
                )
            )
            + 4
            * (
                len(self.retained_receipt.grant.grant_id)
                + len(self.retained_receipt.grant.grantee_id)
            )
        )

    @property
    def receipt(self) -> LocalPermitReceipt:
        return self.retained_receipt.restore()

    @property
    def finalization(self) -> AccountingFinalization:
        return AccountingFinalization.model_validate_json(self.finalization_json)

    def restore(self) -> LocalPermitFinalization:
        return LocalPermitFinalization.model_validate_json(self.document)

    def wire_document(self) -> bytes:
        proof = self.retained_receipt
        grant = _json_bytes(proof.grant.model_dump(mode="json", exclude={"observed_monotonic"}))
        return (
            b'{"finalization":'
            + self.finalization_json
            + b',"grant":'
            + grant
            + b',"permit_ordinal":'
            + str(proof.permit_ordinal).encode()
            + b',"reservation":'
            + self.reservation_json
            + b"}"
        )


LocalTerminalValue = LocalPermitFinalization | FrozenLocalTerminal


def freeze_terminal_snapshots(
    values: Sequence[LocalTerminalValue], *, generation: int
) -> tuple[FrozenLocalTerminal, ...]:
    if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
        raise ValueError("local terminal generation is invalid")
    if len(values) > 256:
        raise ValueError("local terminal batch exceeds its entry limit")
    result, size = [], 2
    for value in values:
        snapshot = (
            value
            if isinstance(value, FrozenLocalTerminal)
            else FrozenLocalTerminal(value, generation=generation)
        )
        if snapshot.generation != generation:
            raise ValueError("local terminal uses a stale generation")
        size += len(snapshot.document) + bool(result)
        if size > MAX_TERMINAL_BATCH_BYTES:
            raise DurableBatchFull("local terminal batch exceeds its byte limit")
        result.append(snapshot)
    if len({value.operation_id for value in result}) != len(result):
        raise ValueError("local terminal batch repeats an operation")
    return tuple(result)


def _json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode()


def _journal_identity(
    value: LocalPermitFinalization, reservation: bytes, finalization: bytes
) -> bytes:
    proof, terminal = value.receipt, value.finalization
    item = proof.reservation
    return _json_bytes(
        {
            "operation_id": str(terminal.operation_id),
            "grant_id": proof.grant.grant_id,
            "grantee_id": proof.grant.grantee_id,
            "fence_token": str(proof.grant.fence_token),
            "permit_ordinal": proof.permit_ordinal,
            "allowance_exact": money_string(item.allowance),
            "outcome": terminal.outcome.value,
            "expires_at": item.expires_at.isoformat(),
            "subject": {
                "attribution": {
                    "api_key": item.attribution.api_key,
                    "user_id": item.attribution.user_id,
                    "team_id": item.attribution.team_id,
                    "organization_id": item.attribution.organization_id,
                    "model": item.attribution.model,
                },
                "windows": [window.model_dump(mode="json") for window in item.windows],
                "allowance": str(item.allowance),
            },
            "reservation_sha256": hashlib.sha256(reservation).hexdigest(),
            "finalization_sha256": hashlib.sha256(finalization).hexdigest(),
        }
    )
