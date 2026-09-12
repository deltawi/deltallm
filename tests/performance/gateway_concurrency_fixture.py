"""Seed only the named disposable database, using the production key hashing contract."""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

from prisma import Prisma

from src.db.repositories import KeyRepository
from src.services.key_service import KeyService

MODEL = "concurrency-fixture"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def require_local_url(value: str, *, schemes: set[str]) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in schemes or parsed.hostname not in LOOPBACK_HOSTS:
        raise ValueError("The concurrency fixture requires explicit loopback dependencies")
    if parsed.scheme == "http" and (
        parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise ValueError("HTTP fixture URLs must not include credentials, queries, or fragments")
    return value


def fixture_database_url() -> str:
    value = require_local_url(os.environ["DATABASE_URL"], schemes={"postgresql", "postgres"})
    if urlparse(value).path != "/deltallm_concurrency":
        raise ValueError("The fixture database must be named deltallm_concurrency")
    return value


def fixture_key() -> str:
    value = os.environ["DELTALLM_LOAD_API_KEY"]
    if not value.startswith("sk-concurrency-") or value == os.environ.get("DELTALLM_MASTER_KEY"):
        raise ValueError(
            "Use a dedicated sk-concurrency- test API key, distinct from the master key"
        )
    return value


async def seed() -> None:
    key = fixture_key()
    salt = os.environ["DELTALLM_SALT_KEY"]
    token = KeyService(KeyRepository(None), salt=salt).hash_key(key)
    db = Prisma(datasource={"url": fixture_database_url()})
    await db.connect()
    try:
        if await db.deltallm_organizationtable.find_unique(
            where={"organization_id": "concurrency-org"}
        ):
            raise ValueError(
                "Fixture already exists; reuse it without reseeding or use a fresh test database"
            )
        async with db.tx() as tx:
            await tx.deltallm_organizationtable.create(
                data={
                    "organization_id": "concurrency-org",
                    "organization_name": "Concurrency fixture",
                    "max_budget": 1000,
                    "audit_content_storage_enabled": False,
                }
            )
            await tx.deltallm_teamtable.create(
                data={
                    "team_id": "concurrency-team",
                    "organization_id": "concurrency-org",
                    "models": [MODEL],
                    "max_budget": 1000,
                }
            )
            await tx.deltallm_usertable.create(
                data={
                    "user_id": "concurrency-user",
                    "team_id": "concurrency-team",
                    "models": [MODEL],
                    "max_budget": 1000,
                }
            )
            await tx.deltallm_verificationtoken.create(
                data={
                    "token": token,
                    "key_name": "concurrency-fixture",
                    "user_id": "concurrency-user",
                    "team_id": "concurrency-team",
                    "models": [MODEL],
                    "max_budget": 1000,
                }
            )
    finally:
        await db.disconnect()


if __name__ == "__main__":
    asyncio.run(seed())
