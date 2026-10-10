"""Preview by default. Stop Console exchange and traffic before --apply."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prisma import Prisma
from redis.asyncio import Redis
from src.db.identity.external.external_auth_rollback import ExternalAuthRollbackRepository
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.services.key_auth_cache import KeyAuthCache
from src.config import AppConfig, Settings, resolve_external_auth_database_settings
from src.audit.actions import AuditAction
from src.db.audit.repository import AuditRepository
from src.services.audit_service import AuditIngestionConfig, AuditService
from src.services.external_auth_audit import ExternalAuditEvent, ExternalAuthAudit


async def main(apply: bool, approval_reference: str | None) -> None:
    database_url = os.environ["DATABASE_URL"]
    database = resolve_external_auth_database_settings(
        AppConfig(), Settings(database_url=database_url)
    )
    assert database is not None
    db = Prisma(datasource={"url": database.url})
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    await db.connect()
    try:
        await redis.ping()
        transactions = ExternalAuthTransactions(db)
        audit = ExternalAuthAudit(
            AuditService(
                AuditRepository(db),
                db_client=db,
                ingestion_config=AuditIngestionConfig(enabled=True),
            )
        )

        async def record(tx, phase: str) -> None:
            await audit.write(
                tx,
                ExternalAuditEvent(
                    AuditAction.EXTERNAL_AUTH_INTEGRATION_UPDATE,
                    "operational-rollback",
                    "external-auth-rollback",
                    "success",
                    reason=phase,
                    approval_reference=approval_reference,
                    approved_by="operational-rollback",
                ),
            )

        async with transactions.transaction() as tx:
            repository = ExternalAuthRollbackRepository(tx)
            before = await repository.counts()
            if apply:
                await repository.disable()
                await record(tx, "disable_integrations")
        reconciled = 0
        if apply:
            for parents in [False, True]:
                while True:
                    async with transactions.transaction("maintenance") as tx:
                        removed = await ExternalAuthRollbackRepository(tx).revoke_page(
                            parents=parents
                        )
                        if removed:
                            await record(
                                tx, "revoke_parents" if parents else "revoke_legacy_children"
                            )
                    if not removed:
                        break
            after = ""
            while True:
                async with transactions.transaction("maintenance") as tx:
                    page = await ExternalAuthRollbackRepository(tx).revoked_hashes(after)
                if not page:
                    break
                for identifier, token_hash in page:
                    # The cache owner reconciles all supported versions atomically.
                    await KeyAuthCache(redis).revoke(token_hash, ttl_seconds=60)
                    after = identifier
                    reconciled += 1
        async with transactions.transaction("validation") as tx:
            remaining = await ExternalAuthRollbackRepository(tx).counts()
        print(
            json.dumps(
                {
                    "applied": apply,
                    "before": before,
                    "remaining": remaining,
                    "sample_limit": 1001,
                    "reconciled_revocations": reconciled,
                },
                sort_keys=True,
            )
        )
        if apply and any(
            remaining[key] for key in ["enabled_integrations", "live_parents", "live_children"]
        ):
            raise RuntimeError("Rollback is incomplete; do not restore old traffic")
    finally:
        await redis.aclose()
        await db.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Disable all external integrations, revoke all children, and reconcile exact hashes",
    )
    parser.add_argument(
        "--approval-reference",
        help="Bounded operator approval or incident reference for the required audit",
    )
    arguments = parser.parse_args()
    if arguments.apply and (
        not arguments.approval_reference or len(arguments.approval_reference) > 200
    ):
        parser.error("--apply requires --approval-reference with at most 200 characters")
    asyncio.run(main(arguments.apply, arguments.approval_reference))
