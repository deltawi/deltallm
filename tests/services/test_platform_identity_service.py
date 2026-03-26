from __future__ import annotations

import pytest

from src.services.platform_identity_service import PlatformIdentityService


class FakePlatformIdentityDB:
    def __init__(self) -> None:
        self.accounts: dict[str, dict[str, object]] = {}

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        if "WHERE lower(email) = lower($1)" in query:
            email = str(params[0]).strip().lower()
            for row in self.accounts.values():
                if str(row.get("email") or "").lower() == email:
                    return [dict(row)]
            return []
        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        if "INSERT INTO deltallm_platformaccount" in query:
            email = str(params[0]).strip().lower()
            account_id = f"acct-{len(self.accounts) + 1}"
            self.accounts[account_id] = {
                "account_id": account_id,
                "email": email,
                "role": str(params[1]),
                "is_active": bool(params[2]),
                "force_password_change": False,
                "mfa_enabled": False,
                "password_hash": None,
                "created_at": None,
                "updated_at": None,
                "last_login_at": None,
            }
            return 1
        if "SET password_hash = $1" in query:
            password_hash = str(params[0])
            account_id = str(params[1])
            row = self.accounts.get(account_id)
            if row is None:
                return 0
            row["password_hash"] = password_hash
            return 1
        return 0


class PasswordWriteDroppingIdentityService(PlatformIdentityService):
    async def set_password(self, *, account_id: str, new_password: str) -> None:
        self.validate_password_policy(new_password)
        del account_id


@pytest.mark.asyncio
async def test_create_account_rejects_missing_password_write() -> None:
    db = FakePlatformIdentityDB()
    service = PasswordWriteDroppingIdentityService(db_client=db, salt="salt-key")

    with pytest.raises(RuntimeError, match="failed to set account password"):
        await service.create_account(
            email="user@example.com",
            password="very-secure-password",
        )
