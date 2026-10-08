from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Protocol

from pydantic import ValidationError

from src.models.errors import AuthenticationError, ServiceUnavailableError
from src.models.responses import UserAPIKeyAuth

# Older Console replicas use v5 tombstones. Read only their denials, never
# their allow records: those records do not contain output TPM policy fields.
_LEGACY_REVOCATION = """
local legacy = redis.call('GET', KEYS[2])
if legacy then
    local ok, decoded = pcall(cjson.decode, legacy)
    if ok and type(decoded) == 'table' and decoded.cache_version == 5
        and decoded.cache_kind == 'revoked' then return legacy end
end
"""
AUTH_CACHE_LOOKUP = (
    """
-- deltallm_key_auth_lookup_v7
local now = redis.call('TIME')
local function cached_value()
"""
    + _LEGACY_REVOCATION
    + """
    return redis.call('GET', KEYS[1]) or ''
end
return {cached_value(), tonumber(now[1])*1000 + math.floor(tonumber(now[2])/1000)}
"""
)
AUTH_CACHE_FILL = (
    """
-- deltallm_key_auth_fill_v7
"""
    + _LEGACY_REVOCATION
    + """
local now = redis.call('TIME')
local clock = tonumber(now[1])*1000 + math.floor(tonumber(now[2])/1000)
if clock > tonumber(ARGV[3]) then return '' end
local cached = redis.call('GET', KEYS[1])
if cached then return cached end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2], 'NX')
return ARGV[1]
"""
)
AUTH_CACHE_DROP = """
-- deltallm_key_auth_drop_v7
local removed = 0
for _, key in ipairs(KEYS) do
    local value = redis.call('GET', key)
    if value then
        local ok, decoded = pcall(cjson.decode, value)
        if not ok or type(decoded) ~= 'table' or decoded.cache_kind ~= 'revoked' then
            removed = removed + redis.call('DEL', key)
        end
    end
end
return removed
"""
AUTH_CACHE_REVOKE = """
-- deltallm_key_auth_revoke_v7
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
return 1
"""


class KeyCacheRedis(Protocol):
    async def eval(self, script: str, numkeys: int, *args: str | int) -> object: ...


@dataclass(frozen=True, slots=True)
class KeyCacheLookup:
    auth: UserAPIKeyAuth | None
    fill_deadline_ms: int


class KeyAuthCache:
    def __init__(self, redis: KeyCacheRedis) -> None:
        self.redis = redis

    @staticmethod
    def key(token_hash: str) -> str:
        return "key:v7:" + token_hash

    @staticmethod
    def decode(value: object) -> UserAPIKeyAuth | None:
        if value in (None, "", b""):
            return None
        if not isinstance(value, (str, bytes)):
            raise ServiceUnavailableError(code="key_auth_cache_unavailable")
        try:
            payload = json.loads(value)
            if not isinstance(payload, dict):
                raise ValueError("Invalid key auth cache record")
            if payload.get("cache_version") in (5, 7) and payload.get("cache_kind") == "revoked":
                raise AuthenticationError(code="invalid_api_key")
            if payload.get("cache_version") != 7 or payload.get("cache_kind") != "allow":
                raise ValueError("Invalid key auth cache record")
            auth = UserAPIKeyAuth.model_validate(payload["auth"])
            if auth.expires is not None and datetime.fromisoformat(
                auth.expires.replace("Z", "+00:00")
            ) <= datetime.now(UTC):
                raise AuthenticationError(code="invalid_api_key")
            return auth
        except (ValueError, KeyError, TypeError, ValidationError, RecursionError) as exc:
            raise ServiceUnavailableError(code="key_auth_cache_unavailable") from exc

    async def lookup(self, token_hash: str) -> KeyCacheLookup:
        result = await self.redis.eval(
            AUTH_CACHE_LOOKUP, 2, self.key(token_hash), f"key:v5:{token_hash}"
        )
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise ServiceUnavailableError(code="key_auth_cache_unavailable")
        return KeyCacheLookup(self.decode(result[0]), int(result[1]) + 1000)

    async def fill(
        self,
        token_hash: str,
        auth: UserAPIKeyAuth,
        *,
        ttl_seconds: int,
        deadline_ms: int,
    ) -> UserAPIKeyAuth:
        payload = json.dumps(
            {"cache_version": 7, "cache_kind": "allow", "auth": auth.model_dump(mode="json")}
        )
        result = await self.redis.eval(
            AUTH_CACHE_FILL,
            2,
            self.key(token_hash),
            f"key:v5:{token_hash}",
            payload,
            ttl_seconds,
            deadline_ms,
        )
        resolved = self.decode(result)
        if resolved is None:
            raise ServiceUnavailableError(code="key_auth_fill_deadline_exceeded")
        return resolved

    async def revoke(self, token_hash: str, *, ttl_seconds: int) -> None:
        # Publish both tombstones in one operation, including for rollback.
        await self.redis.eval(
            AUTH_CACHE_REVOKE,
            2,
            self.key(token_hash),
            f"key:v5:{token_hash}",
            json.dumps({"cache_version": 7, "cache_kind": "revoked"}),
            json.dumps({"cache_version": 5, "cache_kind": "revoked"}),
            ttl_seconds + 2,
        )

    async def invalidate(self, keys: list[str]) -> None:
        if keys:
            await self.redis.eval(AUTH_CACHE_DROP, len(keys), *keys)
