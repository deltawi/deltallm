"""One owner for Prisma allocation URLs and their native server deadlines."""

from dataclasses import dataclass
import math
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.database_settings import DatabaseAllocationSettings
from src.database_pooler import pooler_allocation_url

DatabaseAllocation = Literal[
    "control", "foreground", "telemetry", "telemetry_worker", "telemetry_settlement"
]
_WAITING_ALLOCATIONS = frozenset({"control", "foreground"})


@dataclass(frozen=True)
class DatabasePolicy:
    allocation: DatabaseAllocation
    connections: int
    acquisition_seconds: float
    statement_seconds: float
    lock_seconds: float
    transaction_seconds: float
    # In-process wait for a free slot, before the native acquisition deadline.
    queue_seconds: float = 0.0

    @classmethod
    def build(cls, config: DatabaseAllocationSettings, allocation: DatabaseAllocation, size: int):
        background = allocation in {"control", "telemetry_worker"}
        return cls(
            allocation,
            size,
            config.db_acquisition_timeout_seconds,
            config.db_background_statement_timeout_seconds
            if background
            else config.db_statement_timeout_seconds,
            config.db_background_lock_timeout_seconds
            if background
            else config.db_lock_timeout_seconds,
            config.db_background_transaction_timeout_seconds
            if background
            else config.db_transaction_timeout_seconds,
            config.db_capacity_wait_seconds if allocation in _WAITING_ALLOCATIONS else 0.0,
        )

    @property
    def admission_seconds(self) -> float:
        """Longest a caller may wait for a slot: the queue plus native acquisition."""
        return self.queue_seconds + self.acquisition_seconds

    @property
    def native_query_seconds(self) -> float:
        # Prisma's URL connect/pool timeouts use whole seconds. Retain a slot
        # after caller cancellation until native work has finished or expired.
        return 2 * math.ceil(self.acquisition_seconds) + math.ceil(self.statement_seconds) + 2

    def server_deadlines(self) -> tuple[tuple[str, int], ...]:
        """PostgreSQL settings (milliseconds) that bound this allocation's work."""
        return tuple(
            (name, max(1, math.ceil(seconds * 1000)))
            for name, seconds in (
                ("statement_timeout", self.statement_seconds),
                ("lock_timeout", self.lock_seconds),
                ("idle_in_transaction_session_timeout", self.transaction_seconds),
            )
        )

    def connection_url(self, url: str, *, pooler_url: str | None = None) -> str:
        if pooler_url is not None:
            # A transaction pooler does not pass startup options. The pooler
            # alias sets the same deadlines with connect_query, and startup
            # checks that they apply.
            url = pooler_allocation_url(pooler_url, self.allocation)
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(
            connection_limit=str(self.connections),
            pool_timeout=str(math.ceil(self.acquisition_seconds)),
            connect_timeout=str(math.ceil(self.acquisition_seconds)),
            socket_timeout=str(math.ceil(self.statement_seconds) + 1),
            application_name="deltallm_" + self.allocation,
        )
        if pooler_url is not None:
            # Prisma then uses no named prepared statements, which a
            # transaction pooler cannot keep across transactions.
            query["pgbouncer"] = "true"
        else:
            # Last occurrence wins at PostgreSQL startup. Preserve unrelated
            # TLS, schema and operator options, but do not allow URL overrides
            # of bounds.
            query["options"] = (
                query.get("options", "")
                + " "
                + " ".join(f"-c {name}={value}" for name, value in self.server_deadlines())
            ).strip()
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
        )


def resolve_allocation_settings(
    general: DatabaseAllocationSettings, settings: DatabaseAllocationSettings
):
    return DatabaseAllocationSettings.model_validate(
        {
            name: getattr(general if name in general.model_fields_set else settings, name)
            for name in DatabaseAllocationSettings.model_fields
        }
    )
