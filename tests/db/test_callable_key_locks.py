from __future__ import annotations

import pytest

from src.db.callable_key_locks import lock_callable_keys


class _CapturePrisma:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, query: str, *params: object) -> list[dict[str, str]]:
        self.calls.append((query, params))
        return [{"locked": ""}]


@pytest.mark.asyncio
async def test_callable_key_locks_are_sorted_deduplicated_and_prisma_compatible() -> None:
    prisma = _CapturePrisma()

    await lock_callable_keys(prisma, "route-b", "route-a", "route-b", "")

    assert [call[1][1] for call in prisma.calls] == ["route-a", "route-b"]
    assert all("$1::integer" in call[0] for call in prisma.calls)
    assert all("hashtext($2)::integer" in call[0] for call in prisma.calls)
    assert all("::text AS locked" in call[0] for call in prisma.calls)
