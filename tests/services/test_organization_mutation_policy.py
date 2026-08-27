from __future__ import annotations

import pytest

from src.db.organization_mutation_guard import OrganizationMutationGuardRepository
from src.models.organization_lifecycle import OrganizationLifecycleState
from src.services.organization_mutation_policy import (
    OrganizationMutationInactiveError,
    OrganizationMutationNotFoundError,
    OrganizationMutationPolicy,
    OrganizationMutationUnavailableError,
)


class _LifecycleDatabase:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]:
        self.queries.append((query, params))
        return [self.row] if self.row is not None else []


@pytest.mark.asyncio
async def test_active_organization_mutation_locks_authoritative_row() -> None:
    db = _LifecycleDatabase({"organization_id": "org-1", "lifecycle_state": "active"})

    await OrganizationMutationPolicy(OrganizationMutationGuardRepository(db)).require_active(
        "org-1"
    )

    assert db.queries[0][1] == ("org-1",)
    assert "FOR SHARE" in db.queries[0][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        OrganizationLifecycleState.DELETION_PENDING,
        OrganizationLifecycleState.PURGING,
        OrganizationLifecycleState.DELETION_FAILED,
    ],
)
async def test_inactive_organization_mutation_fails_closed(
    state: OrganizationLifecycleState,
) -> None:
    db = _LifecycleDatabase({"organization_id": "org-1", "lifecycle_state": state.value})

    with pytest.raises(OrganizationMutationInactiveError) as exc_info:
        await OrganizationMutationPolicy.for_database(db).require_active("org-1")

    assert exc_info.value.detail() == {
        "code": "organization_inactive",
        "message": "Organization administrative changes are disabled",
        "lifecycle_state": state.value,
    }


@pytest.mark.asyncio
async def test_missing_organization_mutation_is_not_found() -> None:
    with pytest.raises(OrganizationMutationNotFoundError):
        await OrganizationMutationPolicy.for_database(_LifecycleDatabase(None)).require_active(
            "org-1"
        )


@pytest.mark.asyncio
async def test_unknown_lifecycle_state_is_unavailable() -> None:
    db = _LifecycleDatabase({"organization_id": "org-1", "lifecycle_state": "future_state"})

    with pytest.raises(OrganizationMutationUnavailableError):
        await OrganizationMutationPolicy.for_database(db).require_active("org-1")
