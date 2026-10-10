from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pydantic import BaseModel
from typing import Literal

from src.auth.external_errors import ExternalAuthError
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.db.identity.key_revocation_status import KeyRevocationStatusRepository


class KeyRevocationStatus(BaseModel):
    invalidation_id: str
    enforcement: Literal["enforced", "pending"]
    maximum_enforcement_delay_seconds: int


class KeyRevocationStatusService:
    def __init__(self, transactions: ExternalAuthTransactions, cache_ttl_seconds: int) -> None:
        self.transactions = transactions
        self.maximum_delay = cache_ttl_seconds + 1

    async def read(
        self, invalidation_id: str, actor_id: str, *, administrator: bool = False
    ) -> KeyRevocationStatus:
        async with self.transactions.transaction("validation") as db:
            row = await KeyRevocationStatusRepository(db).read(
                invalidation_id, actor_id, administrator=administrator
            )
        if row is None:
            raise ExternalAuthError()
        enforced = row.status == "completed" or row.created_at + timedelta(
            seconds=self.maximum_delay
        ) <= datetime.now(UTC)
        return KeyRevocationStatus(
            invalidation_id=invalidation_id,
            enforcement="enforced" if enforced else "pending",
            maximum_enforcement_delay_seconds=self.maximum_delay,
        )
