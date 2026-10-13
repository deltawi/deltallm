from collections.abc import AsyncIterator
from datetime import timedelta
import os
from uuid import uuid4

import pytest
from prisma import Prisma

from src.db.identity.platform_sessions import PlatformSessionRepository
from src.services.identity.master_session_service import (
    MasterSessionService,
    MasterSessionStatus,
)
from src.services.identity.platform_session_service import PlatformSessionService

pytestmark = pytest.mark.postgres

IDLE = 3600


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
    account_id = "idle-test-" + uuid4().hex
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


async def _set_platform_idle(db: Prisma, service: PlatformSessionService, token: str, seconds: int):
    await db.execute_raw(
        "UPDATE deltallm_platformsession SET last_seen_at = NOW() - ($2::int * INTERVAL '1 second') "
        "WHERE session_token_hash = $1",
        service.hash_token(token),
        seconds,
    )


async def _platform_last_seen_age(db: Prisma, service: PlatformSessionService, token: str) -> float:
    rows = await db.query_raw(
        "SELECT EXTRACT(EPOCH FROM (NOW() - last_seen_at))::float8 AS age "
        "FROM deltallm_platformsession WHERE session_token_hash = $1",
        service.hash_token(token),
    )
    return float(rows[0]["age"])


async def test_platform_session_idle_timeout_renewal_and_background_checks(session_db) -> None:
    db, account_id = session_db
    service = PlatformSessionService(
        PlatformSessionRepository(db), salt="idle-salt", lifetime=timedelta(hours=12)
    )
    token = await service.create(account_id=account_id, mfa_verified=False)

    fresh = await service.get_context(token, idle_timeout_seconds=IDLE)
    assert fresh is not None and fresh.session_idle_expires_at is not None
    assert fresh.session_idle_expires_at <= fresh.session_expires_at

    # A background check sees the session but does not count as activity.
    await _set_platform_idle(db, service, token, 1800)
    assert await service.get_context(token, idle_timeout_seconds=IDLE, renew=False) is not None
    assert await _platform_last_seen_age(db, service, token) >= 1790

    # A normal request renews it.
    renewed = await service.get_context(token, idle_timeout_seconds=IDLE)
    assert renewed is not None
    assert await _platform_last_seen_age(db, service, token) < 60

    # Unused for longer than the idle timeout: rejected, even by a renewing request.
    await _set_platform_idle(db, service, token, IDLE + 1)
    assert await service.get_context(token, idle_timeout_seconds=IDLE) is None
    assert await _platform_last_seen_age(db, service, token) > IDLE

    # Idle timeout 0 keeps the old fixed-lifetime behaviour.
    assert await service.get_context(token, idle_timeout_seconds=0) is not None


async def test_platform_session_skips_last_seen_write_within_a_minute(session_db) -> None:
    db, account_id = session_db
    repository = PlatformSessionRepository(db)
    service = PlatformSessionService(repository, salt="idle-salt", lifetime=timedelta(hours=1))
    token = await service.create(account_id=account_id, mfa_verified=False)
    token_hash = service.hash_token(token)

    await _set_platform_idle(db, service, token, 30)
    assert await repository.touch(token_hash, min_interval_seconds=60) is False
    await _set_platform_idle(db, service, token, 90)
    assert await repository.touch(token_hash, min_interval_seconds=60) is True


async def test_absolute_lifetime_still_applies_to_active_platform_sessions(session_db) -> None:
    db, account_id = session_db
    service = PlatformSessionService(
        PlatformSessionRepository(db), salt="idle-salt", lifetime=timedelta(hours=12)
    )
    token = await service.create(account_id=account_id, mfa_verified=False)
    await db.execute_raw(
        "UPDATE deltallm_platformsession SET expires_at = NOW() - INTERVAL '1 second' "
        "WHERE session_token_hash = $1",
        service.hash_token(token),
    )
    assert await service.get_context(token, idle_timeout_seconds=IDLE) is None


async def test_master_session_idle_timeout_renewal_and_background_checks(session_db) -> None:
    db, _ = session_db
    service = MasterSessionService(db_client=db, salt="idle-salt")
    token = await service.create_session(master_key="mk-idle", ttl_seconds=4 * 3600)

    async def set_idle(seconds: int) -> None:
        await db.execute_raw(
            "UPDATE deltallm_mastersession SET last_seen_at = NOW() - ($2::int * INTERVAL '1 second') "
            "WHERE token_hash = $1",
            service._hash_token(token),
            seconds,
        )

    async def last_seen_age() -> float:
        rows = await db.query_raw(
            "SELECT EXTRACT(EPOCH FROM (NOW() - last_seen_at))::float8 AS age "
            "FROM deltallm_mastersession WHERE token_hash = $1",
            service._hash_token(token),
        )
        return float(rows[0]["age"])

    check = await service.check_session(token, master_key="mk-idle", idle_timeout_seconds=IDLE)
    assert check.status == MasterSessionStatus.ACTIVE
    assert check.expires_at is not None and check.idle_expires_at is not None
    assert check.idle_expires_at <= check.expires_at

    await set_idle(1800)
    background = await service.check_session(
        token, master_key="mk-idle", idle_timeout_seconds=IDLE, renew=False
    )
    assert background.status == MasterSessionStatus.ACTIVE
    assert await last_seen_age() >= 1790

    renewed = await service.check_session(token, master_key="mk-idle", idle_timeout_seconds=IDLE)
    assert renewed.status == MasterSessionStatus.ACTIVE
    assert await last_seen_age() < 60

    await set_idle(IDLE + 1)
    expired = await service.check_session(token, master_key="mk-idle", idle_timeout_seconds=IDLE)
    assert expired.status == MasterSessionStatus.INVALID
    assert (
        await service.validate_session(token, master_key="mk-idle", idle_timeout_seconds=0)
        == MasterSessionStatus.ACTIVE
    )
