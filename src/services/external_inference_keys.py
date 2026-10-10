from __future__ import annotations

import hashlib

from src.auth.external_errors import ExternalAuthError
from src.db.identity.external.external_auth_transactions import ExternalAuthTransactions
from src.db.identity.external.external_inference_keys import ExternalInferenceKeyRepository
from src.models.external_auth import ExternalInferenceKeyResponse
from src.models.platform_auth import PlatformAuthContext


class ExternalInferenceKeyService:
    def __init__(self, transactions: ExternalAuthTransactions, salt: str) -> None:
        self.transactions = transactions
        self.salt = salt

    async def select(
        self, raw_key: str, context: PlatformAuthContext
    ) -> ExternalInferenceKeyResponse:
        workspace = context.external_workspace
        if (
            workspace is None
            or (context.mfa_enabled and not context.mfa_verified)
            or context.force_password_change
        ):
            raise ExternalAuthError()
        token_hash = hashlib.sha256(f"{self.salt}:{raw_key}".encode()).hexdigest()
        async with self.transactions.transaction("validation") as db:
            selected = await ExternalInferenceKeyRepository(db).select_owned(
                token_hash,
                account_id=context.account_id,
                workspace=workspace,
            )
        if selected is None:
            raise ExternalAuthError()
        return selected
