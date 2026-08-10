from __future__ import annotations

import asyncio
from typing import Any


class RedisLuaScript:
    """Run a Redis Lua script through SCRIPT LOAD/EVALSHA with NOSCRIPT recovery."""

    def __init__(self, script: str) -> None:
        self.script = script
        self._sha: str | None = None
        self._load_lock = asyncio.Lock()

    async def eval(self, redis_client: Any, numkeys: int, *args: Any) -> Any:
        if not _supports_evalsha(redis_client):
            return await redis_client.eval(self.script, numkeys, *args)

        if self._sha is None:
            await self._load(redis_client)

        try:
            return await redis_client.evalsha(self._sha, numkeys, *args)
        except Exception as exc:
            if not _is_no_script_error(exc):
                raise

        await self._load(redis_client, force=True)
        return await redis_client.evalsha(self._sha, numkeys, *args)

    async def _load(self, redis_client: Any, *, force: bool = False) -> None:
        async with self._load_lock:
            if self._sha is not None and not force:
                return
            self._sha = str(await redis_client.script_load(self.script))


def _supports_evalsha(redis_client: Any) -> bool:
    return callable(getattr(redis_client, "script_load", None)) and callable(getattr(redis_client, "evalsha", None))


def _is_no_script_error(exc: Exception) -> bool:
    exc_name = exc.__class__.__name__.lower()
    if "noscript" in exc_name:
        return True
    message = str(exc).lower()
    return "noscript" in message or "no matching script" in message
