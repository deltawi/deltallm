from __future__ import annotations

from collections.abc import Callable

from src.db.identity.platform_passwords import PlatformPasswordRepository


class PlatformPasswordChangeService:
    def __init__(
        self,
        repository: PlatformPasswordRepository,
        *,
        hash_password: Callable[[str], str],
        verify_password: Callable[[str, str], bool],
    ) -> None:
        self.repository = repository
        self.hash_password = hash_password
        self.verify_password = verify_password

    async def change(
        self,
        *,
        account_id: str,
        new_password: str,
        current_password: str | None,
        require_existing_password: bool,
    ) -> bool:
        record = await self.repository.get(account_id)
        if record is None:
            return False
        if record.password_hash:
            if not current_password or not self.verify_password(
                current_password, record.password_hash
            ):
                return False
        elif require_existing_password:
            # Console authentication cannot establish another login method.
            return False
        return await self.repository.replace(
            account_id,
            expected_hash=record.password_hash,
            password_hash=self.hash_password(new_password),
        )
