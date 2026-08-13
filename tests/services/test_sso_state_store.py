from __future__ import annotations

import pytest

from src.services.sso_state_store import SSOStateStore


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


@pytest.mark.asyncio
async def test_sso_state_round_trip_preserves_return_target_and_is_single_use() -> None:
    store = SSOStateStore(redis_client=_Redis(), ttl_seconds=600)

    await store.store_login_state(
        state="state-1",
        code_verifier="verifier-1",
        return_to="/models/deployment-1?tab=usage#cost",
    )

    login_state = await store.pop_login_state(state="state-1")
    assert login_state is not None
    assert login_state.code_verifier == "verifier-1"
    assert login_state.return_to == "/models/deployment-1?tab=usage#cost"
    assert await store.pop_login_state(state="state-1") is None


@pytest.mark.asyncio
async def test_sso_state_reads_legacy_verifier_values() -> None:
    redis = _Redis()
    redis.values["auth:sso:state:legacy"] = "legacy-verifier"
    store = SSOStateStore(redis_client=redis, ttl_seconds=600)

    login_state = await store.pop_login_state(state="legacy")

    assert login_state is not None
    assert login_state.code_verifier == "legacy-verifier"
    assert login_state.return_to == "/"
