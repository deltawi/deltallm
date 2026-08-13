from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.services.master_session_service import (
    MasterSessionService,
    MasterSessionStatus,
    MasterSessionStoreUnavailable,
)


class _FakeMasterSessionDB:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.fail_reads = False
        self.fail_writes = False
        self.read_calls = 0
        self.write_calls = 0

    async def execute_raw(self, query: str, *args: object) -> int:
        self.write_calls += 1
        if self.fail_writes:
            raise RuntimeError("database unavailable")

        if "INSERT INTO deltallm_mastersession" in query:
            token_hash, fingerprint, expires_at = args
            self.records[str(token_hash)] = {
                "master_key_fingerprint": str(fingerprint),
                "expires_at": expires_at,
                "revoked": False,
            }
            return 1
        if "SET revoked_at" in query:
            record = self.records.get(str(args[0]))
            if record is not None:
                record["revoked"] = True
                return 1
            return 0
        if "DELETE FROM deltallm_mastersession" in query:
            cutoff = args[0]
            expired_hashes = [
                token_hash
                for token_hash, record in self.records.items()
                if record["expires_at"] < cutoff or record["revoked"]
            ]
            for token_hash in expired_hashes:
                self.records.pop(token_hash)
            return len(expired_hashes)
        raise AssertionError(f"unexpected write query: {query}")

    async def query_raw(self, query: str, *args: object) -> list[dict[str, str]]:
        self.read_calls += 1
        if self.fail_reads:
            raise RuntimeError("database unavailable")
        if "UPDATE deltallm_mastersession" not in query:
            raise AssertionError(f"unexpected read query: {query}")

        token_hash, fingerprint = (str(value) for value in args)
        record = self.records.get(token_hash)
        if (
            record is None
            or record["master_key_fingerprint"] != fingerprint
            or bool(record["revoked"])
            or record["expires_at"] <= datetime.now(UTC)
        ):
            return []
        return [{"session_id": "session-1"}]


@pytest.mark.asyncio
async def test_master_session_is_opaque_key_bound_and_revocable() -> None:
    db = _FakeMasterSessionDB()
    service = MasterSessionService(db_client=db, salt="installation-salt")

    token = await service.create_session(master_key="mk-secret", ttl_seconds=3600)

    assert token.startswith("dms_")
    assert token not in repr(db.records)
    assert "mk-secret" not in repr(db.records)
    assert await service.validate_session(token, master_key="mk-secret") == MasterSessionStatus.ACTIVE
    assert await service.validate_session(token, master_key="mk-rotated") == MasterSessionStatus.INVALID

    await service.revoke_session(token)

    assert await service.validate_session(token, master_key="mk-secret") == MasterSessionStatus.INVALID


@pytest.mark.asyncio
async def test_master_session_rejects_missing_malformed_and_expired_tokens_without_lookup() -> None:
    db = _FakeMasterSessionDB()
    service = MasterSessionService(db_client=db, salt="installation-salt")

    assert await service.validate_session(None, master_key="mk-secret") == MasterSessionStatus.MISSING
    assert await service.validate_session("not-a-master-session", master_key="mk-secret") == MasterSessionStatus.INVALID
    assert await service.validate_session(f"dms_{'x' * 300}", master_key="mk-secret") == MasterSessionStatus.INVALID
    assert db.read_calls == 0

    token = await service.create_session(master_key="mk-secret", ttl_seconds=60)
    record = next(iter(db.records.values()))
    record["expires_at"] = datetime.now(UTC) - timedelta(seconds=1)

    assert await service.validate_session(token, master_key="mk-secret") == MasterSessionStatus.INVALID


@pytest.mark.asyncio
async def test_master_session_store_failures_are_distinguishable_from_invalid_authentication() -> None:
    db = _FakeMasterSessionDB()
    service = MasterSessionService(db_client=db, salt="installation-salt")
    token = await service.create_session(master_key="mk-secret", ttl_seconds=3600)

    db.fail_reads = True
    assert await service.validate_session(token, master_key="mk-secret") == MasterSessionStatus.UNAVAILABLE

    db.fail_writes = True
    with pytest.raises(MasterSessionStoreUnavailable):
        await service.create_session(master_key="mk-secret", ttl_seconds=3600)
    with pytest.raises(MasterSessionStoreUnavailable):
        await service.revoke_session(token)


@pytest.mark.asyncio
async def test_master_session_requires_database_and_installation_salt() -> None:
    without_db = MasterSessionService(db_client=None, salt="installation-salt")
    without_salt = MasterSessionService(db_client=_FakeMasterSessionDB(), salt="")

    with pytest.raises(MasterSessionStoreUnavailable):
        await without_db.create_session(master_key="mk-secret", ttl_seconds=3600)
    with pytest.raises(MasterSessionStoreUnavailable):
        await without_salt.create_session(master_key="mk-secret", ttl_seconds=3600)
    assert (
        await without_db.validate_session("dms_valid-looking", master_key="mk-secret")
        == MasterSessionStatus.UNAVAILABLE
    )
