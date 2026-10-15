from collections.abc import AsyncIterator
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.db.identity.platform_sessions import PlatformSessionRepository
from src.services.identity.platform_session_service import PlatformSessionService

pytestmark = pytest.mark.postgres


class _RollbackFixture(Exception):
    pass


@pytest.fixture
async def session_db() -> AsyncIterator[tuple[Prisma, str]]:
    url = os.getenv("DATABASE_URL")
    if not url:
        if os.getenv("CI"):
            pytest.fail("The PostgreSQL lane requires DATABASE_URL")
        pytest.skip("DATABASE_URL is required")
    db = Prisma(datasource={"url": url})
    await db.connect()
    account_id = "auth-method-test-" + uuid4().hex
    try:
        async with db.tx(timeout=timedelta(seconds=30)) as tx:
            await tx.execute_raw(
                "INSERT INTO deltallm_platformaccount (account_id, email, updated_at) "
                "VALUES ($1, $1 || '@example.com', NOW())",
                account_id,
            )
            yield tx, account_id
            # Roll back all fixture rows, including when a test fails.
            raise _RollbackFixture()
    except _RollbackFixture:
        pass
    finally:
        await db.disconnect()


async def test_platform_session_stores_auth_method_and_suggests_mfa_for_password_only(
    session_db,
) -> None:
    db, account_id = session_db
    service = PlatformSessionService(
        PlatformSessionRepository(db), salt="auth-method-salt", lifetime=timedelta(hours=1)
    )

    password_token = await service.create(account_id=account_id, mfa_verified=False)
    sso_token = await service.create(account_id=account_id, mfa_verified=True, auth_method="sso")

    password = await service.get_context(password_token)
    sso = await service.get_context(sso_token)
    assert password is not None and sso is not None
    assert (password.auth_method, password.mfa_enrollment_suggested) == ("password", True)
    assert (sso.auth_method, sso.mfa_enrollment_suggested, sso.mfa_verified) == ("sso", False, True)


async def test_platform_session_rejects_unknown_auth_method(session_db) -> None:
    db, account_id = session_db
    with pytest.raises(Exception, match="deltallm_platformsession_auth_method_check"):
        await db.execute_raw(
            "INSERT INTO deltallm_platformsession (session_id, account_id, session_token_hash, "
            "expires_at, auth_method, updated_at) "
            "VALUES ($1, $2, $1, NOW() + INTERVAL '1 hour', 'magic_link', NOW())",
            uuid4().hex,
            account_id,
        )
