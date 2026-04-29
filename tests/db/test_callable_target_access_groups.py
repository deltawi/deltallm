from __future__ import annotations

import pytest

from src.db.callable_target_access_groups import CallableTargetAccessGroupBindingRepository
from src.governance.access_groups import InvalidAccessGroupError


class _FakePrisma:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, sql: str, *params: object) -> list[dict[str, object]]:
        self.calls.append((sql, params))
        if "INSERT INTO deltallm_callabletargetaccessgroupbinding" not in sql:
            return []
        return [
            {
                "callable_target_access_group_binding_id": "ctagb-1",
                "group_key": params[0],
                "scope_type": params[1],
                "scope_id": params[2],
                "enabled": params[3],
                "metadata": None,
                "created_at": None,
                "updated_at": None,
            }
        ]


@pytest.mark.asyncio
async def test_access_group_binding_upsert_normalizes_group_and_scope() -> None:
    prisma = _FakePrisma()
    repository = CallableTargetAccessGroupBindingRepository(prisma)

    record = await repository.upsert_binding(
        group_key="Beta",
        scope_type="Organization",
        scope_id=" org-1 ",
        enabled=True,
        metadata=None,
    )

    assert record is not None
    assert record.group_key == "beta"
    assert record.scope_type == "organization"
    assert record.scope_id == "org-1"
    assert prisma.calls[0][1][:3] == ("beta", "organization", "org-1")


@pytest.mark.asyncio
async def test_access_group_binding_upsert_rejects_invalid_group_key() -> None:
    repository = CallableTargetAccessGroupBindingRepository(_FakePrisma())

    with pytest.raises(InvalidAccessGroupError, match="access group key"):
        await repository.upsert_binding(
            group_key="bad group",
            scope_type="organization",
            scope_id="org-1",
            enabled=True,
            metadata=None,
        )
