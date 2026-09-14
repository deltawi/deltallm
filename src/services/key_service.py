from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

from src.db.repositories import KeyRepository
from src.models.errors import AuthenticationError, AuthenticationUnavailableError
from src.metrics.admission import auth_events
from src.services.auth_fallback import AuthFallback, AuthFallbackLimits, AuthLookup
from src.models.responses import UserAPIKeyAuth
from src.services.cache_invalidation_errors import CacheInvalidationBackendUnavailable
from src.services.runtime_scopes import annotate_auth_metadata

from .organization_lifecycle import (
    OrganizationLifecycleAuthorizer,
    OrganizationLifecycleUnavailable,
)

_CACHE_DELETE_BATCH_SIZE = 500
_SCOPES_REQUIRING_TOKEN_DISCOVERY = {"organization", "team", "user"}


class KeyService:
    def __init__(
        self,
        repository: KeyRepository,
        redis_client: Any | None = None,
        salt: str = "",
        auth_cache_ttl_seconds: int = 300,
        lifecycle_authorizer: OrganizationLifecycleAuthorizer | None = None,
        invalidation_repository: KeyRepository | None = None,
        fallback_limits: AuthFallbackLimits | None = None,
    ) -> None:
        self.repository = repository
        self.invalidation_repository = (
            invalidation_repository if invalidation_repository is not None else repository
        )
        self.redis = redis_client
        self.salt = salt
        self.auth_cache_ttl_seconds = max(1, int(auth_cache_ttl_seconds))
        self.lifecycle_authorizer = lifecycle_authorizer
        self.fallback = AuthFallback(fallback_limits or AuthFallbackLimits())

    def hash_key(self, raw_key: str) -> str:
        return hashlib.sha256(f"{self.salt}:{raw_key}".encode("utf-8")).hexdigest()

    async def validate_key(self, raw_key: str) -> UserAPIKeyAuth:
        if not raw_key or len(raw_key) > 8192:
            raise AuthenticationError(message="Invalid API key", code="invalid_api_key")
        return await self.get_auth_by_token_hash(self.hash_key(raw_key))

    async def get_auth_by_token_hash(self, token_hash: str) -> UserAPIKeyAuth:
        token_hash = str(token_hash or "").strip()
        if not token_hash or len(token_hash) > 128:
            raise AuthenticationError(message="Invalid API key", code="invalid_api_key")
        if self.fallback.closed:
            raise AuthenticationUnavailableError()
        cached = await self._read_cache(token_hash)
        if cached is not None:
            return cached
        auth = await self.fallback.run(
            token_hash, lambda lookup: self._load_auth(token_hash, lookup)
        )
        self._require_unexpired(auth)
        return auth

    async def _read_cache(self, token_hash: str) -> UserAPIKeyAuth | None:
        if self.redis is None:
            return None
        try:
            async with asyncio.timeout(self.fallback.limits.cache_timeout_seconds):
                cached = await self.redis.get(self._cache_key(token_hash))
            if not cached:
                auth_events.labels("cache_read", "miss").inc()
                return None
            if (
                not isinstance(cached, (str, bytes))
                or len(cached) > self.fallback.limits.cache_max_bytes
            ):
                raise ValueError("oversized auth cache entry")
            if (
                isinstance(cached, str)
                and len(cached.encode("utf-8")) > self.fallback.limits.cache_max_bytes
            ):
                raise ValueError("oversized auth cache entry")
            auth = UserAPIKeyAuth.model_validate_json(cached)
            if not UserAPIKeyAuth.model_fields.keys() <= auth.model_fields_set:
                raise ValueError("incomplete auth cache entry")
            if auth.api_key != token_hash:
                raise ValueError("auth cache identity mismatch")
            self._require_unexpired(auth)
        except AuthenticationError:
            raise
        except Exception:
            # A cache is fallible/untrusted. SQL fallback has a separate bound;
            # malformed cache entries never authorize or fail otherwise valid auth.
            auth_events.labels("cache_read", "unavailable_or_invalid").inc()
            return None
        auth_events.labels("cache_read", "hit").inc()
        return self._mark_cache_source(auth, "redis")

    async def _load_auth(self, token_hash: str, lookup: AuthLookup) -> UserAPIKeyAuth:
        try:
            record = await self.repository.get_by_token(token_hash)
            lookup.check()
            if record is None:
                raise AuthenticationError(message="Invalid API key", code="invalid_api_key")
            auth = self._auth_from_record(record)
            self._require_unexpired(auth)
            await self._validate_organization(record)
            lookup.check()
            await self._write_cache(token_hash, auth, lookup)
            lookup.check()
            self._require_unexpired(auth)
            return auth
        except (
            AuthenticationError,
            AuthenticationUnavailableError,
            OrganizationLifecycleUnavailable,
        ):
            raise
        except Exception:
            auth_events.labels("lookup", "unavailable").inc()
            raise AuthenticationUnavailableError() from None

    async def _write_cache(self, token_hash: str, auth: UserAPIKeyAuth, lookup: AuthLookup) -> None:
        if self.redis is None:
            return
        try:
            payload = auth.model_dump_json()
            if len(payload.encode("utf-8")) > self.fallback.limits.cache_max_bytes:
                auth_events.labels("cache_write", "oversized").inc()
                return
            ttl = self.auth_cache_ttl_seconds
            if auth.expires is not None:
                expires = datetime.fromisoformat(auth.expires.replace("Z", "+00:00"))
                ttl = min(ttl, int((expires - datetime.now(tz=UTC)).total_seconds()))
            if ttl <= 0:
                return
            lookup.check()
            async with asyncio.timeout(
                min(
                    self.fallback.limits.cache_timeout_seconds,
                    max(0.001, (lookup.deadline - asyncio.get_running_loop().time()) / 2),
                )
            ):
                await self.redis.setex(self._cache_key(token_hash), ttl, payload)
        except AuthenticationUnavailableError:
            raise
        except Exception:
            auth_events.labels("cache_write", "unavailable").inc()
            return
        auth_events.labels("cache_write", "stored").inc()

    @staticmethod
    def _require_unexpired(auth: UserAPIKeyAuth) -> None:
        if auth.expires is None:
            return
        expires = datetime.fromisoformat(auth.expires.replace("Z", "+00:00"))
        if expires.tzinfo is None:
            raise ValueError("authentication expiry must be timezone aware")
        if expires <= datetime.now(tz=UTC):
            raise AuthenticationError(message="API key expired", code="invalid_api_key")

    async def close(self) -> None:
        await self.fallback.close()

    async def invalidate_key_cache_by_hash(self, token_hash: str) -> None:
        self.fallback.invalidate(token_hash)
        if self.redis is None:
            return
        cache_key = self._cache_key(token_hash)
        await self.redis.delete(cache_key)

    async def invalidate_keys_for_team(self, team_id: str) -> int:
        return await self._invalidate_keys_by_scope("team_id", team_id)

    async def invalidate_keys_for_org(self, organization_id: str) -> int:
        return await self._invalidate_keys_by_scope("organization_id", organization_id)

    async def invalidate_keys_for_user(self, user_id: str) -> int:
        return await self._invalidate_keys_by_scope("user_id", user_id)

    def require_cache_invalidation_backend(self, *, scope_type: str) -> None:
        if self.redis is None:
            raise CacheInvalidationBackendUnavailable("redis unavailable")
        normalized_scope_type = str(scope_type or "").strip().lower()
        if (
            normalized_scope_type in _SCOPES_REQUIRING_TOKEN_DISCOVERY
            and getattr(self.invalidation_repository, "prisma", None) is None
        ):
            raise CacheInvalidationBackendUnavailable("database unavailable")

    async def _invalidate_keys_by_scope(self, scope_column: str, scope_value: str) -> int:
        self.fallback.invalidate()
        prisma = getattr(self.invalidation_repository, "prisma", None)
        if self.redis is None or prisma is None:
            return 0
        if scope_column == "organization_id":
            rows = await prisma.query_raw(
                """
                SELECT v.token FROM deltallm_verificationtoken v
                LEFT JOIN deltallm_usertable u ON u.user_id = v.user_id
                LEFT JOIN deltallm_teamtable t ON t.team_id = COALESCE(v.team_id, u.team_id)
                WHERE t.organization_id = $1
                """,
                scope_value,
            )
        elif scope_column == "team_id":
            rows = await prisma.query_raw(
                """
                SELECT v.token FROM deltallm_verificationtoken v
                LEFT JOIN deltallm_usertable u ON u.user_id = v.user_id
                WHERE COALESCE(v.team_id, u.team_id) = $1
                """,
                scope_value,
            )
        else:
            rows = await prisma.query_raw(
                f"SELECT token FROM deltallm_verificationtoken WHERE {scope_column} = $1",
                scope_value,
            )
        cache_keys: list[str] = []
        for row in rows or []:
            token_hash = row.get("token")
            if token_hash:
                cache_keys.append(self._cache_key(token_hash))
        return await self._delete_cache_keys(cache_keys)

    async def _delete_cache_keys(self, cache_keys: list[str]) -> int:
        if self.redis is None or not cache_keys:
            return 0
        count = 0
        for start in range(0, len(cache_keys), _CACHE_DELETE_BATCH_SIZE):
            batch = cache_keys[start : start + _CACHE_DELETE_BATCH_SIZE]
            await self.redis.delete(*batch)
            count += len(batch)
        return count

    @staticmethod
    def _cache_key(token_hash: str) -> str:
        # Version the serialized auth contract so entries without lifecycle
        # state cannot silently authenticate an inactive organization.
        return f"key:v4:{token_hash}"

    def _auth_from_record(self, record: Any) -> UserAPIKeyAuth:
        auth = UserAPIKeyAuth(
            api_key=record.token,
            user_id=record.user_id,
            team_id=record.team_id,
            organization_id=record.organization_id,
            owner_account_id=record.owner_account_id,
            models=record.models or [],
            team_models=record.team_models or [],
            max_budget=record.max_budget,
            spend=record.spend,
            tpm_limit=record.tpm_limit,
            rpm_limit=record.rpm_limit,
            key_tpm_limit=record.tpm_limit,
            key_rpm_limit=record.rpm_limit,
            user_tpm_limit=record.user_tpm_limit,
            user_rpm_limit=record.user_rpm_limit,
            team_tpm_limit=record.team_tpm_limit,
            team_rpm_limit=record.team_rpm_limit,
            org_tpm_limit=record.org_tpm_limit,
            org_rpm_limit=record.org_rpm_limit,
            team_model_rpm_limit=record.team_model_rpm_limit,
            team_model_tpm_limit=record.team_model_tpm_limit,
            org_model_rpm_limit=record.org_model_rpm_limit,
            org_model_tpm_limit=record.org_model_tpm_limit,
            max_parallel_requests=record.max_parallel_requests,
            key_rph_limit=record.key_rph_limit,
            key_rpd_limit=record.key_rpd_limit,
            key_tpd_limit=record.key_tpd_limit,
            user_rph_limit=record.user_rph_limit,
            user_rpd_limit=record.user_rpd_limit,
            user_tpd_limit=record.user_tpd_limit,
            team_rph_limit=record.team_rph_limit,
            team_rpd_limit=record.team_rpd_limit,
            team_tpd_limit=record.team_tpd_limit,
            org_rph_limit=record.org_rph_limit,
            org_rpd_limit=record.org_rpd_limit,
            org_tpd_limit=record.org_tpd_limit,
            guardrails=self._extract_guardrails(record),
            metadata={
                **(record.metadata or {}),
                "auth_cache_source": "database",
                "organization_lifecycle_state": record.organization_lifecycle_state,
                "organization_lifecycle_version": record.organization_lifecycle_version,
                "organization_lifecycle_generation": record.organization_lifecycle_generation,
            },
            team_metadata=record.team_metadata,
            org_metadata=record.org_metadata,
            expires=record.expires.isoformat() if record.expires else None,
        )
        return annotate_auth_metadata(
            auth,
            auth_source="api_key",
            api_key_scope_id=record.token,
        )

    async def _validate_organization(self, record: Any) -> None:
        organization_id = str(getattr(record, "organization_id", None) or "").strip()
        if not organization_id:
            return
        lifecycle_state = (
            str(getattr(record, "organization_lifecycle_state", "active") or "active")
            .strip()
            .lower()
        )
        if lifecycle_state != "active":
            raise AuthenticationError(
                message="Organization is not active",
                code="organization_inactive",
            )
        if self.lifecycle_authorizer is not None:
            await self.lifecycle_authorizer.remember_state(
                organization_id,
                lifecycle_state,
                generation=int(getattr(record, "organization_lifecycle_generation", 0) or 0),
            )

    @staticmethod
    def _mark_cache_source(auth: UserAPIKeyAuth, source: str) -> UserAPIKeyAuth:
        metadata = dict(auth.metadata or {})
        metadata["auth_cache_source"] = source
        auth.metadata = metadata
        return auth

    @staticmethod
    def _extract_guardrails(record: Any) -> list[str]:
        if isinstance(getattr(record, "guardrails", None), list):
            return [str(name) for name in record.guardrails]

        metadata = getattr(record, "metadata", None)
        if isinstance(metadata, dict) and isinstance(metadata.get("guardrails"), list):
            return [str(name) for name in metadata["guardrails"]]
        return []
