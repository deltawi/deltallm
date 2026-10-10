"""Isolated migrated queue tables for native admission experiments and tests."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from prisma import Prisma

from src.db.allocated_client import AllocatedPrisma, DatabaseOwner
from src.db.allocation_config import DatabasePolicy

AUDIT = "deltallm_audit_ingestion_outbox"
SPEND = "deltallm_spend_ingestion_outbox"
CAPACITY = "deltallm_telemetry_ingestion_capacity"
ORGANIZATION = "deltallm_organizationtable"
TABLES = (AUDIT, SPEND, CAPACITY, ORGANIZATION)


@dataclass(frozen=True)
class IngestionDatabase:
    acceptance: AllocatedPrisma
    worker: AllocatedPrisma
    observer: Prisma
    schema: str


def schema_url(url: str, schema: str) -> str:
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["schema"] = schema
    query["connection_limit"] = "1"
    query["pool_timeout"] = "1"
    query["connect_timeout"] = "2"
    query["options"] = query.get("options", "") + f" -c search_path={schema}"
    return urlunsplit(parsed._replace(query=urlencode(query)))


@asynccontextmanager
async def ingestion_database(url: str, *, connections: int = 5) -> AsyncIterator[IngestionDatabase]:
    # A generated identifier, static table allowlist, and schema-local cleanup
    # leave the source database's migrated queue and organization data untouched.
    schema = "admission_" + uuid4().hex
    admin = Prisma(datasource={"url": schema_url(url, "public")})
    await admin.connect()
    try:
        await admin.execute_raw(f'CREATE SCHEMA "{schema}"')
        try:
            for table in TABLES:
                await admin.execute_raw(
                    f'CREATE TABLE "{schema}".{table} (LIKE public.{table} INCLUDING ALL)'
                )
            await admin.execute_raw(
                f"INSERT INTO \"{schema}\".{CAPACITY} (queue_name) VALUES ('audit'), ('spend')"
            )
            async with AsyncExitStack() as stack:
                clients = []
                for name in ("telemetry", "telemetry_worker"):
                    background = name == "telemetry_worker"
                    policy = DatabasePolicy(
                        name,
                        connections,
                        0.2,
                        5 if background else 1,
                        1 if background else 0.2,
                        10 if background else 2,
                    )
                    owner = DatabaseOwner(policy)
                    client = AllocatedPrisma(
                        datasource={"url": policy.connection_url(schema_url(url, schema))},
                        allocation=owner,
                    )
                    stack.push_async_callback(client.disconnect)
                    stack.push_async_callback(owner.close)
                    await client.connect()
                    clients.append(client)
                observer = Prisma(datasource={"url": schema_url(url, schema)})
                stack.push_async_callback(observer.disconnect)
                await observer.connect()
                yield IngestionDatabase(clients[0], clients[1], observer, schema)
        finally:
            await admin.execute_raw(f'DROP SCHEMA "{schema}" CASCADE')
    finally:
        await admin.disconnect()
