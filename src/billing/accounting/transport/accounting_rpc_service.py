"""Accept signed persistence batches without taking ownership of local issues."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter

from src.billing.accounting.health.accounting_health import AccountingBacklogProbe
from src.billing.accounting.journal.accounting_terminal_snapshots import (
    FrozenLocalTerminal,
    freeze_terminal_snapshots,
)
from src.billing.accounting.transport.accounting_rpc_contracts import (
    AccountingRpcHealth,
    LocalFundingRequest,
    LocalReturnReply,
    LocalReturnRequest,
    LocalTerminalRequest,
    funding_reply,
    rpc_batch_bytes,
)
from src.billing.accounting.journal.accounting_terminal_receipts import JournalReceipt
from src.billing.accounting.durable_microbatch import DurableMicrobatcher
from src.db.accounting_calls import AccountingProtocolUnavailable, AccountingQueryClient
from src.db.accounting_journal import AccountingJournalRepository
from src.db.accounting_local_leases import AccountingLocalLeaseRepository
from src.db.telemetry_acceptance import AcceptanceFailure
from src.metrics.accounting import (
    observe_accounting_batch,
    observe_accounting_queue_wait,
    set_accounting_queue_depth,
    set_accounting_queue_retained_bytes,
)
from src.telemetry.lifecycle import (
    WorkerHealth,
    WorkerHealthSource,
    WorkerState,
    task_failure_detail,
)


@dataclass(frozen=True, slots=True)
class QueuedTerminal:
    snapshot: FrozenLocalTerminal
    expires_at: float


class AccountingRpcService:
    """One terminal queue owns journal writes; API replicas own issued proofs."""

    def __init__(
        self,
        client: AccountingQueryClient,
        *,
        generation: int,
        probe: AccountingBacklogProbe,
        projection_health: WorkerHealthSource,
        statement_seconds: float = 0.25,
        grant_ttl_seconds: int = 30,
        max_batch_size: int = 128,
        dwell_seconds: float = 0.002,
        max_pending: int = 8192,
        max_retained_bytes: int = 8 * 1024 * 1024,
        terminal_ack_seconds: float = 2.0,
    ) -> None:
        if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
            raise ValueError("accounting RPC generation is invalid")
        if not 0.01 <= statement_seconds <= 2 or not 1 <= grant_ttl_seconds <= 300:
            raise ValueError("accounting RPC database bounds are invalid")
        if not 2 * statement_seconds <= terminal_ack_seconds <= 5:
            raise ValueError("accounting RPC ACK must fit a statement and recovery")
        self._client = client
        self._generation = generation
        self._probe = probe
        self._projection_health = projection_health
        self._statement = statement_seconds
        self._ttl = grant_ttl_seconds
        self._ack = terminal_ack_seconds
        self._journal = AccountingJournalRepository(
            client, statement_budget_seconds=statement_seconds
        )
        self._closed = False
        self._admitting = True
        self.terminals = DurableMicrobatcher(
            self._append,
            name="accounting-rpc-terminal",
            max_batch_size=max_batch_size,
            max_pending=max_pending,
            dwell_seconds=dwell_seconds,
            payload_size=lambda value: value.snapshot.retained_bytes,
            max_batch_bytes=1_048_576,
            max_retained_bytes=max_retained_bytes,
            observe_queue_wait=lambda seconds: observe_accounting_queue_wait(
                "rpc_terminal", seconds
            ),
            set_queue_depth=lambda depth: set_accounting_queue_depth("rpc_terminal", depth),
            set_retained_bytes=lambda size: set_accounting_queue_retained_bytes(
                "rpc_terminal", size
            ),
        )

    @property
    def worker_health(self) -> WorkerHealth:
        if self._closed:
            return WorkerHealth(WorkerState.STOPPING, "rpc_closed")
        if not self._admitting:
            return WorkerHealth(WorkerState.STOPPING, "rpc_admission_stopped")
        if self.terminals.task is None:
            return WorkerHealth(WorkerState.STARTING, "terminal_unknown")
        if task_failure_detail(self.terminals.task) is not None:
            return WorkerHealth(WorkerState.FAILED, "terminal_task_failed")
        if self._projection_health.worker_health.state is not WorkerState.READY:
            return WorkerHealth(WorkerState.DEGRADED, "projection_unready")
        if not self._probe.worker_health.ready:
            return self._probe.worker_health
        value = self._probe.snapshot
        if (
            value is None
            or value.generation != self._generation
            or value.protocol_state != "active"
        ):
            return WorkerHealth(WorkerState.FAILED, "generation_inactive")
        return WorkerHealth(WorkerState.READY)

    def start(self) -> asyncio.Task[None]:
        if self._closed:
            raise RuntimeError("accounting RPC cannot restart after close")
        return self.terminals.start()

    async def close(self, *, timeout_seconds: float) -> None:
        self.stop_admission()
        self._closed = True
        await self.terminals.close(timeout_seconds=timeout_seconds)

    def stop_admission(self) -> None:
        self._admitting = False

    def health(self) -> AccountingRpcHealth:
        self._require_ready(self._generation)
        return AccountingRpcHealth(generation=self._generation, status="ready")

    async def allocate(self, request: LocalFundingRequest, *, expires_at: float) -> bytes:
        self._require_ready(request.generation)
        repository = self._leases(request.owner_id)
        results = await repository.allocate_batch(request.values, expires_at=expires_at)
        now = asyncio.get_running_loop().time()
        return rpc_batch_bytes(
            tuple(
                funding_reply(item, value, now=now)
                for item, value in zip(request.values, results, strict=True)
            )
        )

    async def return_suffixes(self, request: LocalReturnRequest, *, expires_at: float) -> bytes:
        # Returns and terminal acceptance remain available during drain. They
        # never grant new provider dispatch and keep the database's fences.
        self._require_generation(request.generation)
        values = tuple(
            value.restore(observed_monotonic=asyncio.get_running_loop().time())
            for value in request.values
        )
        counts = await self._leases(request.owner_id).return_batch(values, expires_at=expires_at)
        return rpc_batch_bytes(
            tuple(
                LocalReturnReply(
                    grant_id=value.grant.grant_id,
                    fence_token=value.grant.fence_token,
                    first_unused_ordinal=value.first_unused_ordinal,
                    returned_operations=count,
                )
                for value, count in zip(values, counts, strict=True)
            )
        )

    async def finalize(self, request: LocalTerminalRequest, *, expires_at: float) -> bytes:
        self._require_generation(request.generation)
        values = tuple(
            value.restore(observed_monotonic=asyncio.get_running_loop().time())
            for value in request.values
        )
        snapshots = freeze_terminal_snapshots(values, generation=self._generation)
        async with asyncio.timeout_at(expires_at):
            results = await asyncio.gather(
                *(self.terminals.submit(QueuedTerminal(value, expires_at)) for value in snapshots)
            )
        return rpc_batch_bytes(tuple(results))

    async def _append(self, values: Sequence[QueuedTerminal]) -> list[JournalReceipt]:
        started = perf_counter()
        deadline = min(
            asyncio.get_running_loop().time() + self._ack,
            *(value.expires_at for value in values),
        )
        try:
            result = await self._journal.append_batch(
                tuple(value.snapshot for value in values),
                expires_at=deadline,
            )
        except BaseException as exc:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            observe_accounting_batch("rpc_terminal", len(values), perf_counter() - started, outcome)
            raise
        observe_accounting_batch("rpc_terminal", len(values), perf_counter() - started, "success")
        return list(result)

    def _leases(self, owner_id: str) -> AccountingLocalLeaseRepository:
        return AccountingLocalLeaseRepository(
            self._client,
            owner_id=owner_id,
            statement_budget_seconds=self._statement,
            grant_ttl_seconds=self._ttl,
        )

    def _require_ready(self, generation: int) -> None:
        self._require_generation(generation)
        if self.worker_health.state is not WorkerState.READY:
            raise AccountingProtocolUnavailable(AcceptanceFailure.DATABASE_UNAVAILABLE)

    def _require_generation(self, generation: int) -> None:
        if type(generation) is not int or generation != self._generation:
            raise ValueError("accounting RPC uses another generation")
