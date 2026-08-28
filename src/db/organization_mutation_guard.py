from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.models.organization_lifecycle import OrganizationLifecycleState


class OrganizationMutationGuardDatabase(Protocol):
    async def query_raw(
        self,
        query: str,
        *params: object,
    ) -> list[dict[str, object]]: ...


@dataclass(frozen=True, slots=True)
class OrganizationMutationSnapshot:
    organization_id: str
    lifecycle_state: OrganizationLifecycleState


class OrganizationMutationGuardRepository:
    """Locks the durable organization lifecycle row for a tenant mutation."""

    def __init__(self, db: OrganizationMutationGuardDatabase) -> None:
        self.db = db

    async def lock_organization(
        self,
        organization_id: str,
    ) -> OrganizationMutationSnapshot | None:
        rows = await self.db.query_raw(
            """
            SELECT organization_id, lifecycle_state
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            FOR SHARE
            """,
            organization_id,
        )
        if not rows:
            return None
        row = rows[0]
        return OrganizationMutationSnapshot(
            organization_id=str(row.get("organization_id") or ""),
            lifecycle_state=OrganizationLifecycleState(
                str(row.get("lifecycle_state") or "").strip().lower()
            ),
        )


__all__ = [
    "OrganizationMutationGuardDatabase",
    "OrganizationMutationGuardRepository",
    "OrganizationMutationSnapshot",
]
