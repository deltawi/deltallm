from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from redis.exceptions import RedisError

from src.audit.actions import AuditAction
from src.auth.external_errors import ExternalAuthUnavailable
from src.db.runtime.cache_invalidation_outbox import CacheInvalidationOutboxRepository
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.db.identity.key_removal import KeyRemovalRepository
from src.db.identity.platform_accounts import PlatformAccountDatabase
from src.services.identity.external.external_auth_audit import ExternalAuditEvent, ExternalAuthAudit
from src.services.identity.keys.key_service import KeyService
from src.services.invalidation.cache_invalidation_errors import CacheInvalidationBackendUnavailable


@dataclass(frozen=True, slots=True)
class KeyRemovalResult:
    removed: bool
    enforcement: Literal["enforced", "pending"]
    invalidation_id: str


class KeyRemovalService:
    def __init__(
        self, transactions: ExternalAuthTransactions, audit: ExternalAuthAudit, keys: KeyService
    ) -> None:
        self.transactions = transactions
        self.audit = audit
        self.keys = keys

    async def remove(
        self,
        token_hash: str,
        *,
        actor_id: str,
        correlation_id: str,
        deleted: bool,
        approve: Callable[[PlatformAccountDatabase], Awaitable[Literal["admin", "self_service"]]],
    ) -> KeyRemovalResult:
        async with self.transactions.transaction() as db:
            mode = await approve(db)
            removed = await KeyRemovalRepository(db).remove(token_hash)
            outbox = await CacheInvalidationOutboxRepository(db).enqueue(
                scope_type="key_hash",
                scope_id=token_hash,
                reason="api_key_deleted" if deleted else "api_key_revoked",
                metadata={"auth_revocation": True, "revocation_schema": 1, "actor_id": actor_id},
            )
            if outbox is None:
                raise ExternalAuthUnavailable()
            await self.audit.write(
                db,
                ExternalAuditEvent(
                    AuditAction.ADMIN_KEY_SELF_REVOKE
                    if mode == "self_service"
                    else AuditAction.ADMIN_KEY_DELETE
                    if deleted
                    else AuditAction.ADMIN_KEY_REVOKE,
                    actor_id,
                    correlation_id,
                    "success",
                    reason="api_key_deleted" if deleted else "api_key_revoked",
                    resource_type="api_key",
                    resource_id=token_hash,
                    actor_type="platform_account",
                ),
            )
        enforcement: Literal["enforced", "pending"] = "pending"
        try:
            async with asyncio.timeout(0.1):
                await self.keys.mark_key_revoked_by_hash(token_hash)
            enforcement = "enforced"
        except (RedisError, TimeoutError, OSError, CacheInvalidationBackendUnavailable):
            pass
        return KeyRemovalResult(removed, enforcement, outbox.invalidation_id)
