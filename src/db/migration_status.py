"""Read-only proof that this image's required Prisma history is complete."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Protocol

MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[2] / "prisma" / "migrations"
MAX_MIGRATION_RECORDS = 4096


class MigrationQuery(Protocol):
    async def query_raw(self, query: str) -> list[dict[str, object]]: ...


class MigrationVerificationError(RuntimeError):
    """Safe failure reason; raw SQL/connection errors are not exposed."""


def required_migrations(directory: Path = MIGRATIONS_DIRECTORY) -> dict[str, str]:
    files = sorted(directory.glob("*/migration.sql"))
    if not files or len(files) > MAX_MIGRATION_RECORDS:
        raise MigrationVerificationError("image migration manifest is missing or exceeds its bound")
    return {file.parent.name: sha256(file.read_bytes()).hexdigest() for file in files}


def verify_history(required: Mapping[str, str], rows: Sequence[Mapping[str, object]]) -> None:
    if not required or len(rows) > MAX_MIGRATION_RECORDS:
        raise MigrationVerificationError(
            "migration history exceeds its bound or manifest is missing"
        )
    completed = set()
    for row in rows:
        if row.get("rolled_back") is True:
            continue
        if row.get("finished") is not True:
            raise MigrationVerificationError("migration history contains an unfinished migration")
        name = row.get("migration_name")
        if not isinstance(name, str) or not isinstance(row.get("checksum"), str):
            raise MigrationVerificationError("migration history contains an invalid record")
        if name in required:
            if row["checksum"] != required[name]:
                raise MigrationVerificationError(
                    "required migration checksum does not match this image"
                )
            completed.add(name)
    if completed != set(required):
        raise MigrationVerificationError("required migrations have not completed for this image")


async def verify_migration_status(
    client: MigrationQuery,
    *,
    timeout_seconds: float,
    directory: Path = MIGRATIONS_DIRECTORY,
) -> None:
    manifest = required_migrations(directory)
    try:
        async with asyncio.timeout(timeout_seconds):
            rows = await client.query_raw(
                "SELECT migration_name, checksum, finished_at IS NOT NULL AS finished, "
                'rolled_back_at IS NOT NULL AS rolled_back FROM "_prisma_migrations" '
                f"ORDER BY started_at LIMIT {MAX_MIGRATION_RECORDS + 1}"
            )
    except TimeoutError:
        raise MigrationVerificationError("migration history verification timed out") from None
    except Exception:
        raise MigrationVerificationError("migration history is unavailable") from None
    verify_history(manifest, rows)
