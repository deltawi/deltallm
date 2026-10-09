from __future__ import annotations

import pytest

from src.db.organization_deletion_repository import OrganizationDeletionRepository


class _LifecyclePrisma:
    def __init__(self, row: dict[str, object]) -> None:
        self.row = row
        self.sql = ""

    async def query_raw(self, sql: str, *params: object) -> list[dict[str, object]]:
        del params
        self.sql = sql
        return [self.row]


@pytest.mark.asyncio
async def test_team_scope_marks_missing_referenced_organization() -> None:
    prisma = _LifecyclePrisma({"organization_id": "missing-org", "lifecycle_state": None})

    lifecycle = await OrganizationDeletionRepository(prisma).team_organization_lifecycle("team-1")

    assert lifecycle is not None
    assert lifecycle.organization_id == "missing-org"
    assert lifecycle.lifecycle_state == "missing"
    assert "WHEN o.organization_id IS NULL THEN 'missing'" in prisma.sql


@pytest.mark.asyncio
async def test_team_scope_keeps_explicitly_unowned_team_active() -> None:
    prisma = _LifecyclePrisma({"organization_id": None, "lifecycle_state": None})

    lifecycle = await OrganizationDeletionRepository(prisma).team_organization_lifecycle("team-1")

    assert lifecycle is not None
    assert lifecycle.organization_id is None
    assert lifecycle.lifecycle_state == "active"
