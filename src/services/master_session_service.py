from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import Enum
import hashlib
import hmac
import secrets
from typing import Any


MASTER_SESSION_COOKIE_NAME = "deltallm_master_session"
_TOKEN_PREFIX = "dms_"
_MAX_TOKEN_LENGTH = 256


class MasterSessionStatus(str, Enum):
    MISSING = "missing"
    ACTIVE = "active"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


class MasterSessionStoreUnavailable(RuntimeError):
    pass


class MasterSessionService:
    """Durable, revocable browser sessions authenticated by the master key."""

    def __init__(self, *, db_client: Any, salt: str) -> None:
        self.db = db_client
        self.salt = str(salt or "").strip()

    async def create_session(
        self,
        *,
        master_key: str,
        ttl_seconds: int,
    ) -> str:
        if self.db is None or not self.salt:
            raise MasterSessionStoreUnavailable("master session storage unavailable")

        token = f"{_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        expires_at = datetime.now(UTC) + timedelta(seconds=max(1, int(ttl_seconds)))
        try:
            await self.db.execute_raw(
                """
                INSERT INTO deltallm_mastersession (
                    session_id, token_hash, master_key_fingerprint,
                    expires_at, created_at, updated_at, last_seen_at
                )
                VALUES (gen_random_uuid(), $1, $2, $3::timestamptz, NOW(), NOW(), NOW())
                """,
                self._hash_token(token),
                self._fingerprint_master_key(master_key),
                expires_at,
            )
        except Exception as exc:
            raise MasterSessionStoreUnavailable("failed to create master session") from exc
        return token

    async def validate_session(
        self,
        token: str | None,
        *,
        master_key: str | None,
    ) -> MasterSessionStatus:
        if not token:
            return MasterSessionStatus.MISSING
        if not self._valid_token_shape(token) or not master_key:
            return MasterSessionStatus.INVALID
        if self.db is None or not self.salt:
            return MasterSessionStatus.UNAVAILABLE

        try:
            rows = await self.db.query_raw(
                """
                UPDATE deltallm_mastersession
                SET last_seen_at = NOW(), updated_at = NOW()
                WHERE token_hash = $1
                  AND master_key_fingerprint = $2
                  AND revoked_at IS NULL
                  AND expires_at > NOW()
                RETURNING session_id
                """,
                self._hash_token(token),
                self._fingerprint_master_key(master_key),
            )
        except Exception:
            return MasterSessionStatus.UNAVAILABLE
        return MasterSessionStatus.ACTIVE if rows else MasterSessionStatus.INVALID

    async def revoke_session(self, token: str | None) -> None:
        if not token or not self._valid_token_shape(token):
            return
        if self.db is None or not self.salt:
            raise MasterSessionStoreUnavailable("master session storage unavailable")

        try:
            await self.db.execute_raw(
                """
                UPDATE deltallm_mastersession
                SET revoked_at = COALESCE(revoked_at, NOW()), updated_at = NOW()
                WHERE token_hash = $1
                """,
                self._hash_token(token),
            )
        except Exception as exc:
            raise MasterSessionStoreUnavailable("failed to revoke master session") from exc

    async def purge_expired_sessions(self, *, retention_days: int = 7) -> int:
        if self.db is None:
            raise MasterSessionStoreUnavailable("master session storage unavailable")
        cutoff = datetime.now(UTC) - timedelta(days=max(0, int(retention_days)))
        try:
            result = await self.db.execute_raw(
                """
                DELETE FROM deltallm_mastersession
                WHERE expires_at < $1::timestamptz
                   OR (revoked_at IS NOT NULL AND revoked_at < $1::timestamptz)
                """,
                cutoff,
            )
        except Exception as exc:
            raise MasterSessionStoreUnavailable("failed to purge master sessions") from exc
        return int(result or 0)

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(f"{self.salt}:master-browser-session:{token}".encode("utf-8")).hexdigest()

    def _fingerprint_master_key(self, master_key: str) -> str:
        fingerprint_key = hashlib.sha256(f"{self.salt}:master-key-fingerprint".encode("utf-8")).digest()
        return hmac.new(fingerprint_key, master_key.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _valid_token_shape(token: str) -> bool:
        return token.startswith(_TOKEN_PREFIX) and len(token) <= _MAX_TOKEN_LENGTH
