from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from src.db.repositories import KeyRepository
from src.models.errors import AuthenticationError
from src.models.responses import UserAPIKeyAuth

logger = logging.getLogger(__name__)


class KeyService:
    def __init__(self, repository: KeyRepository, redis_client: Any | None = None, salt: str = "") -> None:
        self.repository = repository
        self.redis = redis_client
        self.salt = salt

    def hash_key(self, raw_key: str) -> str:
        return hashlib.sha256(f"{self.salt}:{raw_key}".encode("utf-8")).hexdigest()

    async def validate_key(self, raw_key: str) -> UserAPIKeyAuth:
        token_hash = self.hash_key(raw_key)
        cache_key = f"key:{token_hash}"

        if self.redis is not None:
            cached = await self.redis.get(cache_key)
            if cached:
                logger.info("key validation cache hit", extra={"token_hash": token_hash})
                payload = json.loads(cached if isinstance(cached, str) else cached.decode("utf-8"))
                return UserAPIKeyAuth.model_validate(payload)

        record = await self.repository.get_by_token(token_hash)
        if record is None:
            logger.warning("invalid api key", extra={"token_hash": token_hash})
            raise AuthenticationError(message="Invalid API key", code="invalid_api_key")

        now = datetime.now(tz=UTC)
        if record.expires and record.expires < now:
            logger.warning("expired api key", extra={"token_hash": token_hash})
            raise AuthenticationError(message="API key expired", code="invalid_api_key")

        auth = UserAPIKeyAuth(
            api_key=record.token,
            user_id=record.user_id,
            team_id=record.team_id,
            organization_id=record.organization_id,
            models=record.models or [],
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
            max_parallel_requests=record.max_parallel_requests,
            guardrails=self._extract_guardrails(record),
            metadata=record.metadata,
            expires=record.expires.isoformat() if record.expires else None,
        )

        if self.redis is not None:
            ttl = 3600
            if record.expires is not None:
                ttl = max(1, int((record.expires - now).total_seconds()))
            await self.redis.setex(cache_key, ttl, auth.model_dump_json())

        return auth

    @staticmethod
    def _extract_guardrails(record: Any) -> list[str]:
        if isinstance(getattr(record, "guardrails", None), list):
            return [str(name) for name in record.guardrails]

        metadata = getattr(record, "metadata", None)
        if isinstance(metadata, dict) and isinstance(metadata.get("guardrails"), list):
            return [str(name) for name in metadata["guardrails"]]
        return []
