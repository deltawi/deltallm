"""Keep bounded immutable turn proofs until the shared terminal owner replies."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from src.billing.accounting_protocol import AccountingFinalization, AccountingOperationHandle
from src.billing.accounting_snapshots import finalization_bytes
from src.billing.spend_operations import SpendPersistenceUnavailable

MAX_HANDLE_BYTES = 24 * 1024
MAX_TERMINAL_BYTES = 24 * 1024
TURN_RESERVED_BYTES = 64 * 1024
ACKNOWLEDGED_BYTES = 2048


@dataclass(slots=True, repr=False)
class AccountingTurnProof:
    session_id: str
    context_fingerprint: str
    started_at: datetime
    handle_json: bytes | None = None
    terminal_json: bytes | None = None
    receipt_fingerprint: str | None = None
    acknowledged: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class AccountingTurnProofs:
    """Admission reserves replay space; no pending proof can be evicted."""

    def __init__(self, *, max_entries: int = 8192, max_bytes: int = 8 * 1024 * 1024) -> None:
        if (
            not 1 <= max_entries <= 100_000
            or not TURN_RESERVED_BYTES <= max_bytes <= 64 * 1024 * 1024
        ):
            raise ValueError("Invalid turn proof capacity")
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._values: dict[str, AccountingTurnProof] = {}
        self._sessions: dict[str, set[str]] = {}
        self.retained_bytes = 0

    @property
    def entries(self) -> int:
        return len(self._values)

    def begin(self, operation_id: str, value: AccountingTurnProof) -> None:
        UUID(operation_id)
        UUID(value.session_id)
        if (
            len(value.session_id) != 36
            or len(operation_id) != 36
            or (len(value.context_fingerprint) != 64)
        ):
            raise SpendPersistenceUnavailable()
        if (
            operation_id in self._values
            or self.entries >= self.max_entries
            or self.retained_bytes + TURN_RESERVED_BYTES > self.max_bytes
        ):
            raise SpendPersistenceUnavailable()
        self._values[operation_id] = value
        self._sessions.setdefault(value.session_id, set()).add(operation_id)
        self.retained_bytes += TURN_RESERVED_BYTES

    def require(self, operation_id: str, fingerprint: str) -> AccountingTurnProof:
        value = self._values.get(operation_id)
        if value is None or value.context_fingerprint != fingerprint:
            raise SpendPersistenceUnavailable()
        return value

    def retain_handle(self, value: AccountingTurnProof, handle: AccountingOperationHandle) -> None:
        encoded = handle.model_dump_json().encode()
        if len(encoded) > MAX_HANDLE_BYTES or value.handle_json is not None:
            raise SpendPersistenceUnavailable()
        value.handle_json = encoded

    def freeze_terminal(
        self, value: AccountingTurnProof, terminal: AccountingFinalization, *, receipt: str | None
    ) -> None:
        encoded = finalization_bytes(terminal)
        if len(encoded) > MAX_TERMINAL_BYTES or value.terminal_json is not None:
            raise SpendPersistenceUnavailable()
        value.terminal_json, value.receipt_fingerprint = encoded, receipt

    def acknowledge(self, value: AccountingTurnProof) -> None:
        if not value.acknowledged:
            if value.handle_json is None or value.terminal_json is None:
                raise SpendPersistenceUnavailable()
            value.handle_json, value.terminal_json = None, None
            value.acknowledged = True
            self.retained_bytes -= TURN_RESERVED_BYTES - ACKNOWLEDGED_BYTES

    def session_operations(self, session_id: str) -> tuple[str, ...]:
        return tuple(self._sessions.get(session_id, ()))

    def session_proofs(self, session_id: str) -> tuple[tuple[str, AccountingTurnProof], ...]:
        return tuple((key, self._values[key]) for key in self.session_operations(session_id))

    def remove(self, operation_id: str) -> None:
        value = self._values.pop(operation_id, None)
        if value is None:
            return
        operations = self._sessions[value.session_id]
        operations.remove(operation_id)
        if not operations:
            del self._sessions[value.session_id]
        self.retained_bytes -= ACKNOWLEDGED_BYTES if value.acknowledged else TURN_RESERVED_BYTES
