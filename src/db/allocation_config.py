"""One owner for Prisma allocation URLs and their native server deadlines."""

from dataclasses import dataclass
import math
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.database_settings import DatabaseAllocationSettings

DatabaseAllocation = Literal["control", "foreground", "telemetry", "telemetry_worker"]


@dataclass(frozen=True)
class DatabasePolicy:
    allocation: DatabaseAllocation
    connections: int
    acquisition_seconds: float
    statement_seconds: float
    lock_seconds: float
    transaction_seconds: float

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
        )

    @property
    def native_query_seconds(self) -> float:
        # Prisma's URL connect/pool timeouts use whole seconds. Retain a slot
        # after caller cancellation until native work has finished or expired.
        return 2 * math.ceil(self.acquisition_seconds) + math.ceil(self.statement_seconds) + 2

    def connection_url(self, url: str) -> str:
        parsed = urlsplit(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update(
            connection_limit=str(self.connections),
            pool_timeout=str(math.ceil(self.acquisition_seconds)),
            connect_timeout=str(math.ceil(self.acquisition_seconds)),
            socket_timeout=str(math.ceil(self.statement_seconds) + 1),
            application_name="deltallm_" + self.allocation,
        )
        # Last occurrence wins at PostgreSQL startup. Preserve unrelated TLS,
        # schema and operator options, but do not allow URL overrides of bounds.
        query["options"] = (
            query.get("options", "")
            + " "
            + " ".join(
                f"-c {name}={max(1, math.ceil(seconds * 1000))}"
                for name, seconds in (
                    ("statement_timeout", self.statement_seconds),
                    ("lock_timeout", self.lock_seconds),
                    ("idle_in_transaction_session_timeout", self.transaction_seconds),
                )
            )
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
