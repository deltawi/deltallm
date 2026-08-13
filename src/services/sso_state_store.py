from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


class SSOStateStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SSOLoginState:
    code_verifier: str
    return_to: str = "/"


class SSOStateStore:
    def __init__(self, *, redis_client: Any, ttl_seconds: int = 600) -> None:
        self.redis = redis_client
        self.ttl_seconds = max(60, int(ttl_seconds or 600))

    def _key(self, state: str) -> str:
        return f"auth:sso:state:{state}"

    async def store_code_verifier(self, *, state: str, code_verifier: str) -> None:
        await self.store_login_state(state=state, code_verifier=code_verifier, return_to="/")

    async def store_login_state(self, *, state: str, code_verifier: str, return_to: str) -> None:
        if not state or not code_verifier:
            raise SSOStateStoreError("state and code_verifier are required")
        if self.redis is None:
            raise SSOStateStoreError("sso state storage unavailable")
        value = json.dumps(
            {"code_verifier": code_verifier, "return_to": return_to or "/"},
            separators=(",", ":"),
        )
        try:
            stored = await self.redis.set(
                self._key(state),
                value,
                ex=self.ttl_seconds,
                nx=True,
            )
        except Exception as exc:  # pragma: no cover - defensive path
            raise SSOStateStoreError("failed to store SSO state") from exc
        if not stored:
            raise SSOStateStoreError("duplicate SSO state")

    async def pop_code_verifier(self, *, state: str) -> str | None:
        login_state = await self.pop_login_state(state=state)
        return login_state.code_verifier if login_state is not None else None

    async def pop_login_state(self, *, state: str) -> SSOLoginState | None:
        if not state:
            return None
        if self.redis is None:
            raise SSOStateStoreError("sso state storage unavailable")
        key = self._key(state)
        try:
            if hasattr(self.redis, "getdel"):
                value = await self.redis.getdel(key)
            elif hasattr(self.redis, "eval"):
                value = await self.redis.eval(
                    """
                    local value = redis.call('GET', KEYS[1])
                    if value then
                      redis.call('DEL', KEYS[1])
                    end
                    return value
                    """,
                    1,
                    key,
                )
            else:
                value = await self.redis.get(key)
                if value is not None and hasattr(self.redis, "delete"):
                    await self.redis.delete(key)
        except Exception as exc:  # pragma: no cover - defensive path
            raise SSOStateStoreError("failed to load SSO state") from exc
        if value is None:
            return None

        raw_value = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        try:
            payload = json.loads(raw_value)
        except json.JSONDecodeError:
            # Preserve compatibility with states issued before return targets were stored.
            return SSOLoginState(code_verifier=raw_value)
        if not isinstance(payload, dict):
            return None
        code_verifier = str(payload.get("code_verifier") or "").strip()
        if not code_verifier:
            return None
        return SSOLoginState(
            code_verifier=code_verifier,
            return_to=str(payload.get("return_to") or "/"),
        )
