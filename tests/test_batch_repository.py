from __future__ import annotations

import pytest

from src.batch.repository import BatchRepository


class _PrismaSpy:
    def __init__(self) -> None:
        self.sql = ""
        self.params = ()

    async def query_raw(self, sql: str, *params):
        self.sql = sql
        self.params = params
        return []


@pytest.mark.asyncio
async def test_list_jobs_uses_or_scope_for_api_key_and_team():
    prisma = _PrismaSpy()
    repository = BatchRepository(prisma_client=prisma)

    jobs = await repository.list_jobs(
        limit=20,
        created_by_api_key="key-1",
        created_by_team_id="team-1",
    )

    assert jobs == []
    assert "created_by_api_key" in prisma.sql
    assert "created_by_team_id" in prisma.sql
    assert " OR " in prisma.sql
