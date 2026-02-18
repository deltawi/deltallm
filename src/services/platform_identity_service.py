from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import base64
import hashlib
import hmac
import secrets
import struct
from typing import Any
import urllib.parse

from src.auth.roles import PLATFORM_ROLE_PERMISSIONS, Permission, PlatformRole
from src.models.platform_auth import PlatformAuthContext


@dataclass
class LoginResult:
    context: PlatformAuthContext
    session_token: str
    mfa_required: bool
    mfa_prompt: bool


class PlatformIdentityService:
    def __init__(self, db_client: Any, salt: str, session_ttl_hours: int = 12) -> None:
        self.db = db_client
        self.salt = salt or "change-me"
        self.session_ttl_hours = session_ttl_hours

    async def ensure_bootstrap_admin(self, email: str | None, password: str | None) -> None:
        if self.db is None or not email or not password:
            return

        existing = await self.db.query_raw(
            "SELECT account_id, password_hash FROM litellm_platformaccount WHERE lower(email) = lower($1) LIMIT 1",
            email,
        )
        if existing:
            row = existing[0]
            if not row.get("password_hash"):
                await self.db.execute_raw(
                    "UPDATE litellm_platformaccount SET password_hash = $1, role = $2, updated_at = NOW() WHERE account_id = $3",
                    self._hash_password(password),
                    PlatformRole.ADMIN,
                    row["account_id"],
                )
            return

        await self.db.execute_raw(
            """
            INSERT INTO litellm_platformaccount (
                account_id, email, password_hash, role, is_active, force_password_change,
                mfa_enabled, created_at, updated_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3, true, true, false, NOW(), NOW())
            """,
            email,
            self._hash_password(password),
            PlatformRole.ADMIN,
        )

    async def login_internal(self, email: str, password: str, mfa_code: str | None = None) -> LoginResult | None:
        if self.db is None:
            return None

        rows = await self.db.query_raw(
            """
            SELECT account_id, email, password_hash, role, is_active, force_password_change,
                   mfa_enabled, mfa_secret
            FROM litellm_platformaccount
            WHERE lower(email) = lower($1)
            LIMIT 1
            """,
            email,
        )
        if not rows:
            return None

        row = rows[0]
        if not bool(row.get("is_active", True)):
            return None

        password_hash = row.get("password_hash")
        if not isinstance(password_hash, str) or not self._verify_password(password, password_hash):
            return None

        mfa_enabled = bool(row.get("mfa_enabled", False))
        mfa_secret = row.get("mfa_secret")
        if mfa_enabled:
            if not mfa_code or not isinstance(mfa_secret, str) or not self._verify_totp(mfa_secret, mfa_code):
                return None

        token = await self._create_session(account_id=row["account_id"], mfa_verified=mfa_enabled)
        context = await self.get_context_for_session(token)
        if context is None:
            return None

        await self.db.execute_raw(
            "UPDATE litellm_platformaccount SET last_login_at = NOW(), updated_at = NOW() WHERE account_id = $1",
            row["account_id"],
        )

        return LoginResult(
            context=context,
            session_token=token,
            mfa_required=mfa_enabled,
            mfa_prompt=not mfa_enabled,
        )

    async def upsert_sso_account(
        self,
        email: str,
        is_platform_admin: bool,
        provider: str = "sso",
        subject: str | None = None,
    ) -> LoginResult | None:
        if self.db is None:
            return None

        role = PlatformRole.ADMIN if is_platform_admin else "org_user"
        await self.db.execute_raw(
            """
            INSERT INTO litellm_platformaccount (
                account_id, email, role, is_active, force_password_change,
                mfa_enabled, created_at, updated_at
            )
            VALUES (gen_random_uuid(), $1, $2, true, false, false, NOW(), NOW())
            ON CONFLICT (email)
            DO UPDATE SET role = EXCLUDED.role, is_active = true, updated_at = NOW()
            """,
            email,
            role,
        )
        await self.db.execute_raw(
            """
            INSERT INTO litellm_platformidentity (
                identity_id, account_id, provider, subject, email, created_at, updated_at
            )
            SELECT gen_random_uuid(), account_id, $2, $3, $1, NOW(), NOW()
            FROM litellm_platformaccount
            WHERE lower(email) = lower($1)
            ON CONFLICT (provider, subject)
            DO UPDATE SET email = EXCLUDED.email, updated_at = NOW()
            """,
            email,
            provider,
            subject or email.lower(),
        )

        token = await self._create_session_from_email(email=email, mfa_verified=False)
        context = await self.get_context_for_session(token)
        if context is None:
            return None
        return LoginResult(
            context=context,
            session_token=token,
            mfa_required=False,
            mfa_prompt=not context.mfa_enabled,
        )

    async def get_context_for_session(self, session_token: str) -> PlatformAuthContext | None:
        if self.db is None or not session_token:
            return None

        token_hash = self._hash_session_token(session_token)
        rows = await self.db.query_raw(
            """
            SELECT
                s.account_id,
                s.mfa_verified,
                s.expires_at,
                a.email,
                a.role,
                a.force_password_change,
                a.mfa_enabled,
                a.is_active
            FROM litellm_platformsession s
            JOIN litellm_platformaccount a ON a.account_id = s.account_id
            WHERE s.session_token_hash = $1
              AND s.revoked_at IS NULL
              AND s.expires_at > NOW()
            LIMIT 1
            """,
            token_hash,
        )
        if not rows:
            return None

        row = rows[0]
        if not bool(row.get("is_active", True)):
            return None

        await self.db.execute_raw(
            "UPDATE litellm_platformsession SET last_seen_at = NOW() WHERE session_token_hash = $1",
            token_hash,
        )

        role = str(row.get("role") or "org_user")
        permissions = sorted(PLATFORM_ROLE_PERMISSIONS.get(role, set()))

        org_rows = await self.db.query_raw(
            """
            SELECT organization_id, role
            FROM litellm_organizationmembership
            WHERE account_id = $1
            """,
            row["account_id"],
        )
        team_rows = await self.db.query_raw(
            """
            SELECT team_id, role
            FROM litellm_teammembership
            WHERE account_id = $1
            """,
            row["account_id"],
        )

        expires_at = row.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).astimezone(UTC)

        return PlatformAuthContext(
            account_id=str(row["account_id"]),
            email=str(row.get("email") or ""),
            role=role,
            mfa_enabled=bool(row.get("mfa_enabled", False)),
            mfa_verified=bool(row.get("mfa_verified", False)),
            force_password_change=bool(row.get("force_password_change", False)),
            permissions=permissions,
            organization_memberships=[dict(r) for r in org_rows],
            team_memberships=[dict(r) for r in team_rows],
            session_expires_at=expires_at if isinstance(expires_at, datetime) else None,
        )

    async def revoke_session(self, session_token: str) -> None:
        if self.db is None:
            return
        token_hash = self._hash_session_token(session_token)
        await self.db.execute_raw(
            "UPDATE litellm_platformsession SET revoked_at = NOW(), updated_at = NOW() WHERE session_token_hash = $1",
            token_hash,
        )

    async def start_mfa_enrollment(self, account_id: str) -> tuple[str, str] | None:
        if self.db is None:
            return None

        secret = self._generate_totp_secret()
        await self.db.execute_raw(
            "UPDATE litellm_platformaccount SET mfa_pending_secret = $1, updated_at = NOW() WHERE account_id = $2",
            secret,
            account_id,
        )

        uri = self._totp_uri(secret=secret, account_name=account_id)
        return secret, uri

    async def confirm_mfa_enrollment(self, account_id: str, code: str) -> bool:
        if self.db is None:
            return False
        rows = await self.db.query_raw(
            "SELECT mfa_pending_secret FROM litellm_platformaccount WHERE account_id = $1 LIMIT 1",
            account_id,
        )
        if not rows:
            return False

        secret = rows[0].get("mfa_pending_secret")
        if not isinstance(secret, str) or not self._verify_totp(secret, code):
            return False

        await self.db.execute_raw(
            """
            UPDATE litellm_platformaccount
            SET mfa_secret = $1,
                mfa_enabled = true,
                mfa_pending_secret = NULL,
                updated_at = NOW()
            WHERE account_id = $2
            """,
            secret,
            account_id,
        )

        return True

    async def change_password(self, account_id: str, new_password: str, current_password: str | None = None) -> bool:
        if self.db is None:
            return False
        rows = await self.db.query_raw(
            "SELECT password_hash FROM litellm_platformaccount WHERE account_id = $1 LIMIT 1",
            account_id,
        )
        if not rows:
            return False

        existing_hash = rows[0].get("password_hash")
        if isinstance(existing_hash, str) and existing_hash:
            if not current_password or not self._verify_password(current_password, existing_hash):
                return False

        await self.db.execute_raw(
            """
            UPDATE litellm_platformaccount
            SET password_hash = $1,
                force_password_change = false,
                updated_at = NOW()
            WHERE account_id = $2
            """,
            self._hash_password(new_password),
            account_id,
        )
        return True

    async def _create_session_from_email(self, email: str, mfa_verified: bool) -> str:
        rows = await self.db.query_raw(
            "SELECT account_id FROM litellm_platformaccount WHERE lower(email) = lower($1) LIMIT 1",
            email,
        )
        if not rows:
            raise ValueError("account not found")
        return await self._create_session(account_id=rows[0]["account_id"], mfa_verified=mfa_verified)

    async def _create_session(self, account_id: str, mfa_verified: bool) -> str:
        token = f"psk_{secrets.token_urlsafe(32)}"
        token_hash = self._hash_session_token(token)
        expires_at = datetime.now(UTC) + timedelta(hours=self.session_ttl_hours)

        await self.db.execute_raw(
            """
            INSERT INTO litellm_platformsession (
                session_id, account_id, session_token_hash, mfa_verified,
                expires_at, created_at, updated_at, last_seen_at
            )
            VALUES (gen_random_uuid(), $1, $2, $3, $4::timestamptz, NOW(), NOW(), NOW())
            """,
            account_id,
            token_hash,
            mfa_verified,
            expires_at,
        )

        return token

    def _hash_session_token(self, token: str) -> str:
        return hashlib.sha256(f"{self.salt}:session:{token}".encode("utf-8")).hexdigest()

    def _hash_password(self, raw_password: str) -> str:
        salt = secrets.token_bytes(16)
        rounds = 210_000
        digest = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt, rounds)
        return "pbkdf2_sha256${}${}${}".format(
            rounds,
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )

    def _verify_password(self, raw_password: str, password_hash: str) -> bool:
        try:
            algo, rounds_str, salt_b64, digest_b64 = password_hash.split("$", 3)
            if algo != "pbkdf2_sha256":
                return False
            rounds = int(rounds_str)
            salt = base64.b64decode(salt_b64.encode("ascii"))
            expected = base64.b64decode(digest_b64.encode("ascii"))
        except (ValueError, TypeError):
            return False

        actual = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt, rounds)
        return hmac.compare_digest(actual, expected)

    def _generate_totp_secret(self) -> str:
        return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")

    def _totp_uri(self, secret: str, account_name: str) -> str:
        issuer = "DeltaLLM"
        label = urllib.parse.quote(f"{issuer}:{account_name}")
        return f"otpauth://totp/{label}?secret={secret}&issuer={urllib.parse.quote(issuer)}&algorithm=SHA1&digits=6&period=30"

    def _verify_totp(self, secret: str, code: str) -> bool:
        if not code.isdigit() or len(code) != 6:
            return False
        for offset in (-1, 0, 1):
            if self._totp_code(secret, step_offset=offset) == code:
                return True
        return False

    def _totp_code(self, secret: str, step_offset: int = 0) -> str:
        normalized = secret.upper() + "=" * ((8 - len(secret) % 8) % 8)
        key = base64.b32decode(normalized.encode("ascii"), casefold=True)
        counter = int(datetime.now(UTC).timestamp() // 30) + step_offset
        msg = struct.pack(">Q", counter)
        digest = hmac.new(key, msg, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        binary = ((digest[offset] & 0x7F) << 24) | ((digest[offset + 1] & 0xFF) << 16) | ((digest[offset + 2] & 0xFF) << 8) | (digest[offset + 3] & 0xFF)
        otp = binary % 1_000_000
        return f"{otp:06d}"


def is_platform_admin(context: PlatformAuthContext | None) -> bool:
    if context is None:
        return False
    return Permission.PLATFORM_ADMIN in context.permissions
