"""Seed only the named disposable database, using the production key hashing contract."""

from __future__ import annotations

import asyncio
import os

from src.db.repositories import KeyRepository
from src.services.key_service import KeyService
from tests.performance.gateway_concurrency_dependencies import local_database

MODEL = "concurrency-fixture"


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
    async with local_database() as db:
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
            # Model lists alone do not grant organization access under the
            # enforced callable-target policy. Keep authorization enabled.
            await tx.deltallm_callabletargetbinding.create(
                data={
                    "callable_key": MODEL,
                    "scope_type": "organization",
                    "scope_id": "concurrency-org",
                    "enabled": True,
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


if __name__ == "__main__":
    asyncio.run(seed())
