from __future__ import annotations

from fastapi import HTTPException, status

from src.db.organization_mutation_guard import OrganizationMutationGuardDatabase
from src.services.organization_mutation_policy import (
    OrganizationMutationError,
    OrganizationMutationInactiveError,
    OrganizationMutationNotFoundError,
    OrganizationMutationPolicy,
)


async def require_active_organization_mutation(
    db: OrganizationMutationGuardDatabase,
    organization_id: str,
) -> None:
    try:
        await OrganizationMutationPolicy.for_database(db).require_active(organization_id)
    except OrganizationMutationError as exc:
        raise organization_mutation_http_error(exc) from exc


async def require_active_organization_mutations(
    db: OrganizationMutationGuardDatabase,
    organization_ids: list[str] | tuple[str, ...] | set[str],
) -> None:
    for organization_id in sorted(
        {str(value or "").strip() for value in organization_ids if str(value or "").strip()}
    ):
        await require_active_organization_mutation(db, organization_id)


def organization_mutation_http_error(exc: OrganizationMutationError) -> HTTPException:
    if isinstance(exc, OrganizationMutationInactiveError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.detail())
    if isinstance(exc, OrganizationMutationNotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": exc.code,
                "message": "Organization not found",
            },
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": exc.code,
            "message": "Organization lifecycle state could not be checked",
        },
    )


__all__ = [
    "organization_mutation_http_error",
    "require_active_organization_mutation",
    "require_active_organization_mutations",
]
