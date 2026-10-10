from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from src.models.errors import AuthenticationError, ServiceUnavailableError
from src.models.responses import UserAPIKeyAuth

# Older Console replicas replace v5 entries on revocation. Pair allow entries
# so even an expired, unobserved v5 tombstone cannot leave a valid v7 snapshot.
_CACHED_VALUE = """
local function decode(value)
    if not value then return nil end
    local ok, decoded = pcall(cjson.decode, value)
    if ok and type(decoded) == 'table' then return decoded end
end
local function cached_value()
    local legacy = redis.call('GET', KEYS[2])
    local guard = decode(legacy)
    if guard and guard.cache_version == 5 and guard.cache_kind == 'revoked' then
        return legacy
    end
    local value = redis.call('GET', KEYS[1])
    local current = decode(value)
    if current and current.cache_version == 7 and current.cache_kind == 'allow' then
        if type(current.cache_guard) ~= 'string' or current.cache_guard == ''
            or not guard or guard.cache_version ~= 5 or guard.cache_kind ~= 'allow'
            or guard.cache_guard ~= current.cache_guard then
            redis.call('DEL', KEYS[1])
            return ''
        end
    end
    return value or ''
end
"""
AUTH_CACHE_LOOKUP = (
    """
-- deltallm_key_auth_lookup_v7
local now = redis.call('TIME')
"""
    + _CACHED_VALUE
    + """
return {cached_value(), tonumber(now[1])*1000 + math.floor(tonumber(now[2])/1000)}
"""
)
AUTH_CACHE_FILL = (
    """
-- deltallm_key_auth_fill_v7
"""
    + _CACHED_VALUE
    + """
local cached = cached_value()
if cached ~= '' then return cached end
local now = redis.call('TIME')
local clock = tonumber(now[1])*1000 + math.floor(tonumber(now[2])/1000)
if clock > tonumber(ARGV[4]) then return '' end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
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
redis.call('DEL', KEYS[3], KEYS[4])
return 1
"""


class KeyCacheRedis(Protocol):
    async def eval(self, script: str, numkeys: int, *args: str | int) -> object: ...


@dataclass(frozen=True, slots=True)
class KeyCacheLookup:
    auth: UserAPIKeyAuth | None
    fill_deadline_ms: int


class KeyAuthCache:
    def __init__(self, redis: KeyCacheRedis, *, max_bytes: int = 131_072) -> None:
        self.redis = redis
        self.max_bytes = max_bytes

    @staticmethod
    def key(token_hash: str) -> str:
        return "key:v7:" + token_hash

    def decode(self, value: object) -> UserAPIKeyAuth | None:
        if value in (None, "", b""):
            return None
        if not isinstance(value, (str, bytes)):
            raise ServiceUnavailableError(code="key_auth_cache_unavailable")
        try:
            if len(value) > self.max_bytes or (
                isinstance(value, str) and len(value.encode("utf-8")) > self.max_bytes
            ):
                raise ValueError("Key auth cache record exceeds its size bound")
            payload = json.loads(value)
            if not isinstance(payload, dict):
                raise ValueError("Invalid key auth cache record")
            if payload.get("cache_version") in (5, 7) and payload.get("cache_kind") == "revoked":
                raise AuthenticationError(code="invalid_api_key")
            if payload.get("cache_version") != 7 or payload.get("cache_kind") != "allow":
                raise ValueError("Invalid key auth cache record")
            auth = UserAPIKeyAuth.model_validate(payload["auth"])
            if not UserAPIKeyAuth.model_fields.keys() <= auth.model_fields_set:
                raise ValueError("Incomplete key auth cache record")
            if auth.expires is not None and datetime.fromisoformat(
                auth.expires.replace("Z", "+00:00")
            ) <= datetime.now(UTC):
                raise AuthenticationError(message="API key expired", code="invalid_api_key")
            return auth
        except (ValueError, KeyError, TypeError, ValidationError, RecursionError) as exc:
            raise ServiceUnavailableError(code="key_auth_cache_unavailable") from exc

    async def lookup(self, token_hash: str) -> KeyCacheLookup:
        result = await self.redis.eval(
            AUTH_CACHE_LOOKUP, 2, self.key(token_hash), f"key:v5:{token_hash}"
        )
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise ServiceUnavailableError(code="key_auth_cache_unavailable")
        auth = self.decode(result[0])
        if auth is not None and auth.api_key != token_hash:
            raise ServiceUnavailableError(code="key_auth_cache_unavailable")
        return KeyCacheLookup(auth, int(result[1]) + 1000)

    async def fill(
        self,
        token_hash: str,
        auth: UserAPIKeyAuth,
        *,
        ttl_seconds: int,
        deadline_ms: int,
    ) -> UserAPIKeyAuth:
        snapshot = {
            "cache_kind": "allow",
            "cache_guard": uuid4().hex,
            "auth": auth.model_dump(mode="json"),
        }
        # The v5 reader predates output policy. Do not copy large output maps
        # into its guard; those fields are read only from the v7 snapshot.
        legacy_snapshot = {
            **snapshot,
            "auth": {
                name: value
                for name, value in snapshot["auth"].items()
                if not name.endswith("_output_tpm_limit")
            },
        }
        payload = json.dumps({"cache_version": 7, **snapshot})
        if len(payload.encode("utf-8")) > self.max_bytes:
            return auth
        result = await self.redis.eval(
            AUTH_CACHE_FILL,
            2,
            self.key(token_hash),
            f"key:v5:{token_hash}",
            payload,
            json.dumps({"cache_version": 5, **legacy_snapshot}),
            ttl_seconds,
            deadline_ms,
        )
        resolved = self.decode(result)
        if resolved is None:
            raise ServiceUnavailableError(code="key_auth_fill_deadline_exceeded")
        if resolved.api_key != token_hash:
            raise ServiceUnavailableError(code="key_auth_cache_unavailable")
        return resolved

    async def revoke(self, token_hash: str, *, ttl_seconds: int) -> None:
        # Publish tombstones and clear older raw snapshots in one operation.
        await self.redis.eval(
            AUTH_CACHE_REVOKE,
            4,
            self.key(token_hash),
            f"key:v5:{token_hash}",
            f"key:v4:{token_hash}",
            f"key:v6:{token_hash}",
            json.dumps({"cache_version": 7, "cache_kind": "revoked"}),
            json.dumps({"cache_version": 5, "cache_kind": "revoked"}),
            ttl_seconds + 2,
        )

    async def invalidate(self, keys: list[str]) -> None:
        if keys:
            await self.redis.eval(AUTH_CACHE_DROP, len(keys), *keys)
