"""Dedicated bounded PostgreSQL pool for the accounting request path."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from src.config import DatabaseConnectionSettings
from src.db.telemetry_acceptance import DatabasePoolAcquisitionTimeout

if TYPE_CHECKING:
    from asyncpg import Connection, Pool


_PRISMA_QUERY_PARAMETERS = frozenset(
    {
        "application_name",
        "connection_limit",
        "options",
        "pool_timeout",
        "schema",
        "socket_timeout",
    }
)


def _encode_json(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


async def _initialize_connection(connection: Connection) -> None:
    for type_name in ("json", "jsonb"):
        await connection.set_type_codec(
            type_name,
            schema="pg_catalog",
            encoder=_encode_json,
            decoder=json.loads,
            format="text",
        )


def accounting_postgres_dsn(url: str) -> str:
    """Remove Prisma-only URL options while retaining transport/TLS options."""

    parsed = urlsplit(url)
    query = [
        (name, value)
        for name, value in parse_qsl(parsed.query, keep_blank_values=True)
        if name not in _PRISMA_QUERY_PARAMETERS
    ]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _accounting_schema(url: str) -> str:
    schema = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True)).get("schema", "public")
    if not schema or "\x00" in schema or len(schema) > 63:
        raise ValueError("accounting PostgreSQL schema must be a valid bounded identifier")
    return '"' + schema.replace('"', '""') + '"'


class AccountingPostgresClient:
    """Small query surface used by :class:`AccountingProtocolRepository`."""

    def __init__(
        self,
        pool: Pool,
        *,
        acquisition_seconds: float,
        statement_seconds: float,
    ) -> None:
        self._pool = pool
        self._acquisition_seconds = acquisition_seconds
        self._statement_seconds = statement_seconds

    async def query_raw(self, query: str, *parameters: object) -> list[Mapping[str, object]]:
        try:
            connection = await self._pool.acquire(timeout=self._acquisition_seconds)
        except TimeoutError:
            raise DatabasePoolAcquisitionTimeout() from None
        try:
            rows = await connection.fetch(
                query,
                *parameters,
                timeout=self._statement_seconds,
            )
            return [dict(row) for row in rows]
        finally:
            await self._pool.release(connection, timeout=self._statement_seconds)


class AccountingPostgresManager:
    """Own the direct pool and expose one lifecycle-safe accounting client."""

    def __init__(self) -> None:
        self.client: AccountingPostgresClient | None = None
        self._pool: Pool | None = None
        self._close_seconds = 1.0

    async def connect(
        self,
        database_settings: DatabaseConnectionSettings,
        *,
        pool_size: int,
        acquisition_seconds: float,
        statement_seconds: float,
        lock_seconds: float,
    ) -> None:
        if self._pool is not None:
            raise RuntimeError("accounting PostgreSQL pool is already connected")
        if not 1 <= pool_size < database_settings.pool_size:
            raise RuntimeError(
                "accounting hot-path pool must leave at least one telemetry connection"
            )
        self._close_seconds = acquisition_seconds + statement_seconds + 1.0
        server_settings = {
            "application_name": "deltallm_accounting_hot_path",
            "search_path": _accounting_schema(database_settings.url),
            "statement_timeout": str(max(1, round(statement_seconds * 1000))),
            "lock_timeout": str(max(1, round(lock_seconds * 1000))),
            "idle_in_transaction_session_timeout": str(max(1, round(statement_seconds * 1000))),
        }
        pool = await asyncpg.create_pool(
            dsn=accounting_postgres_dsn(database_settings.url),
            min_size=1,
            max_size=pool_size,
            timeout=acquisition_seconds,
            command_timeout=statement_seconds,
            server_settings=server_settings,
            init=_initialize_connection,
        )
        if pool is None:  # pragma: no cover - asyncpg's annotation permits None
            raise RuntimeError("accounting PostgreSQL pool was not created")
        try:
            async with pool.acquire(timeout=acquisition_seconds) as connection:
                row = await connection.fetchrow(
                    """
                    SELECT
                      EXTRACT(EPOCH FROM current_setting('statement_timeout')::interval)::double precision AS statement_seconds,
                      EXTRACT(EPOCH FROM current_setting('lock_timeout')::interval)::double precision AS lock_seconds
                    """,
                    timeout=statement_seconds,
                )
            if row is None or any(
                abs(float(row[name]) - expected) > 0.001
                for name, expected in (
                    ("statement_seconds", statement_seconds),
                    ("lock_seconds", lock_seconds),
                )
            ):
                raise RuntimeError("accounting PostgreSQL pool did not apply native deadlines")
        except BaseException:
            await pool.close()
            raise
        self._pool = pool
        self.client = AccountingPostgresClient(
            pool,
            acquisition_seconds=acquisition_seconds,
            statement_seconds=statement_seconds,
        )

    async def disconnect(self) -> None:
        pool, self._pool, self.client = self._pool, None, None
        if pool is None:
            return
        try:
            async with asyncio.timeout(self._close_seconds):
                await pool.close()
        except TimeoutError:
            pool.terminate()


accounting_postgres_manager = AccountingPostgresManager()
