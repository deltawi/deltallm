from __future__ import annotations

from typing import Any


_CALLABLE_KEY_LOCK_NAMESPACE = 0x43414C4C


async def lock_callable_keys(prisma: Any, *callable_keys: str) -> None:
    """Serialize durable ownership transitions for public callable keys."""

    normalized = sorted({str(key or "").strip() for key in callable_keys if str(key or "").strip()})
    for callable_key in normalized:
        await prisma.query_raw(
            """
            SELECT pg_advisory_xact_lock(
                $1::integer,
                hashtext($2)::integer
            )::text AS locked
            """,
            _CALLABLE_KEY_LOCK_NAMESPACE,
            callable_key,
        )
