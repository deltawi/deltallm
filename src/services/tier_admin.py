from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.db.tiers import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierRecord,
    TierVersionRecord,
)
from src.services.tier_admin_errors import (
    TierAdminConflictError,
    TierAdminError,
    TierAdminNotFoundError,
    TierAdminValidationError,
)
from src.services.tier_admin_payloads import (
    normalize_capacity_pool_records,
    normalize_model_policy_records,
    normalize_tier_create,
    normalize_tier_update,
    normalize_tier_version_create,
)
from src.services.tier_admin_serialization import (
    serialize_capacity_pool,
    serialize_model_policy,
    serialize_tier,
    serialize_tier_version,
)


class TierAdminService:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    async def list_tiers(
        self,
        *,
        search: str | None,
        enabled: bool | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        search = search.strip() if search else None
        records, total = await self.repository.list_tiers(
            search=search or None,
            enabled=enabled,
            limit=limit,
            offset=offset,
        )
        return {
            "data": [serialize_tier(record) for record in records],
            "pagination": {
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + limit < total,
            },
        }

    async def get_tier_detail(self, tier_id: str) -> dict[str, Any]:
        tier = await self.require_tier(tier_id)
        versions = await self.repository.list_tier_versions(tier_id)
        return {
            "tier": serialize_tier(tier),
            "versions": [serialize_tier_version(version) for version in versions],
        }

    async def create_tier(self, payload: Mapping[str, Any]) -> TierRecord:
        fields = normalize_tier_create(payload)
        existing = await self.repository.get_tier_by_key(fields["tier_key"])
        if existing is not None:
            raise TierAdminConflictError("A tier with this tier_key already exists")

        try:
            return await self.repository.create_tier(**fields)
        except Exception as exc:
            if _looks_like_unique_violation(exc):
                raise TierAdminConflictError("A tier with this tier_key already exists") from exc
            raise

    async def update_tier(self, tier_id: str, payload: Mapping[str, Any]) -> TierRecord:
        existing = await self.require_tier(tier_id)
        fields = normalize_tier_update(payload, existing=existing)

        if fields["tier_key"] != existing.tier_key:
            by_key = await self.repository.get_tier_by_key(fields["tier_key"])
            if by_key is not None and by_key.tier_id != tier_id:
                raise TierAdminConflictError("A tier with this tier_key already exists")

        try:
            updated = await self.repository.update_tier(tier_id, **fields)
        except Exception as exc:
            if _looks_like_unique_violation(exc):
                raise TierAdminConflictError("A tier with this tier_key already exists") from exc
            raise
        if updated is None:
            raise TierAdminNotFoundError("Tier not found")
        return updated

    async def delete_tier(self, tier_id: str) -> dict[str, Any]:
        existing = await self.require_tier(tier_id)
        active_assignments = await self.repository.count_active_tier_assignments(existing.tier_id)
        if active_assignments:
            raise TierAdminConflictError("Tier has active organization assignments")

        try:
            deleted = await self.repository.delete_tier(tier_id)
        except Exception as exc:
            if _looks_like_restrict_violation(exc):
                raise TierAdminConflictError("Tier has assignment or version history") from exc
            raise
        if not deleted:
            raise TierAdminNotFoundError("Tier not found")
        return {"deleted": True, "tier_id": tier_id}

    async def create_tier_version(
        self,
        tier_id: str,
        payload: Mapping[str, Any],
    ) -> TierVersionRecord:
        await self.require_tier(tier_id)
        default_version_number = None
        if payload.get("version_number") is None:
            default_version_number = await self._next_version_number(tier_id)
        fields = normalize_tier_version_create(
            payload,
            default_version_number=default_version_number,
        )

        try:
            return await self.repository.create_tier_version(
                tier_id=tier_id,
                version_number=fields["version_number"],
                status="draft",
                metadata=fields["metadata"],
            )
        except ValueError as exc:
            raise TierAdminValidationError(str(exc)) from exc
        except Exception as exc:
            if _looks_like_unique_violation(exc):
                raise TierAdminConflictError(
                    "A tier version with this number already exists"
                ) from exc
            raise

    async def clone_tier_version(
        self,
        tier_id: str,
        source_tier_version_id: str,
    ) -> TierVersionRecord:
        await self.require_version_for_tier(tier_id, source_tier_version_id)
        try:
            cloned = await self.repository.clone_tier_version(
                tier_id=tier_id,
                source_tier_version_id=source_tier_version_id,
            )
        except Exception as exc:
            if _looks_like_unique_violation(exc):
                raise TierAdminConflictError(
                    "A tier version with this number already exists"
                ) from exc
            raise
        if cloned is None:
            raise TierAdminNotFoundError("Tier version not found")
        return cloned

    async def get_tier_version_detail(self, tier_id: str, tier_version_id: str) -> dict[str, Any]:
        version = await self.require_version_for_tier(tier_id, tier_version_id)
        policies = await self.repository.list_model_policies(tier_version_id)
        pools = await self.repository.list_capacity_pools(tier_version_id)
        return {
            "tier_version": serialize_tier_version(version),
            "model_policies": [serialize_model_policy(policy) for policy in policies],
            "capacity_pools": [serialize_capacity_pool(pool) for pool in pools],
        }

    async def replace_model_policies(
        self,
        tier_id: str,
        tier_version_id: str,
        policies: Sequence[Mapping[str, Any]],
    ) -> list[TierModelPolicyRecord]:
        version = await self.require_version_for_tier(tier_id, tier_version_id)
        _require_draft_version(version)
        records = normalize_model_policy_records(tier_version_id, policies)
        await self._validate_model_policy_pool_references(tier_version_id, records)

        try:
            return await self.repository.replace_model_policies(tier_version_id, records)
        except ValueError as exc:
            raise TierAdminConflictError(str(exc)) from exc

    async def replace_capacity_pools(
        self,
        tier_id: str,
        tier_version_id: str,
        pools: Sequence[Mapping[str, Any]],
    ) -> list[TierCapacityPoolRecord]:
        version = await self.require_version_for_tier(tier_id, tier_version_id)
        _require_draft_version(version)
        records = normalize_capacity_pool_records(tier_version_id, pools)
        await self._validate_referenced_capacity_pools_are_preserved(tier_version_id, records)

        try:
            return await self.repository.replace_capacity_pools(tier_version_id, records)
        except ValueError as exc:
            raise TierAdminConflictError(str(exc)) from exc

    async def publish_tier_version(
        self,
        tier_id: str,
        tier_version_id: str,
        *,
        published_by_account_id: str | None,
    ) -> TierVersionRecord:
        await self.require_version_for_tier(tier_id, tier_version_id)
        try:
            version = await self.repository.publish_tier_version(
                tier_version_id,
                published_by_account_id=published_by_account_id,
            )
        except ValueError as exc:
            raise TierAdminConflictError(str(exc)) from exc
        if version is None:
            raise TierAdminNotFoundError("Tier version not found")
        return version

    async def archive_tier_version(
        self,
        tier_id: str,
        tier_version_id: str,
    ) -> TierVersionRecord:
        await self.require_version_for_tier(tier_id, tier_version_id)
        try:
            version = await self.repository.archive_tier_version(tier_version_id)
        except ValueError as exc:
            raise TierAdminConflictError(str(exc)) from exc
        if version is None:
            raise TierAdminNotFoundError("Tier version not found")
        return version

    async def require_tier(self, tier_id: str) -> TierRecord:
        tier = await self.repository.get_tier(tier_id)
        if tier is None:
            raise TierAdminNotFoundError("Tier not found")
        return tier

    async def require_version_for_tier(
        self,
        tier_id: str,
        tier_version_id: str,
    ) -> TierVersionRecord:
        version = await self.repository.get_tier_version(tier_version_id)
        if version is None or version.tier_id != tier_id:
            raise TierAdminNotFoundError("Tier version not found")
        return version

    async def _next_version_number(self, tier_id: str) -> int:
        versions = await self.repository.list_tier_versions(tier_id)
        return max((version.version_number for version in versions), default=0) + 1

    async def _validate_model_policy_pool_references(
        self,
        tier_version_id: str,
        policies: Sequence[TierModelPolicyRecord],
    ) -> None:
        pools = await self.repository.list_capacity_pools(tier_version_id)
        pool_refs = {(pool.pool_key, pool.callable_key) for pool in pools}
        for policy in policies:
            if policy.capacity_pool_key is None:
                continue
            if (policy.capacity_pool_key, policy.callable_key) not in pool_refs:
                raise TierAdminValidationError(
                    "capacity_pool_key must reference a pool for the same callable_key"
                )

    async def _validate_referenced_capacity_pools_are_preserved(
        self,
        tier_version_id: str,
        pools: Sequence[TierCapacityPoolRecord],
    ) -> None:
        policies = await self.repository.list_model_policies(tier_version_id)
        referenced = {
            (policy.capacity_pool_key, policy.callable_key)
            for policy in policies
            if policy.capacity_pool_key is not None
        }
        incoming = {(pool.pool_key, pool.callable_key) for pool in pools}
        if not referenced.issubset(incoming):
            raise TierAdminConflictError(
                "Cannot remove a capacity pool referenced by draft model policies"
            )


def _require_draft_version(version: TierVersionRecord) -> None:
    if version.status != "draft":
        raise TierAdminConflictError("Tier version policies can only be changed while draft")


def _looks_like_unique_violation(exc: Exception) -> bool:
    message = str(exc).lower()
    return "duplicate key" in message or "unique constraint" in message


def _looks_like_restrict_violation(exc: Exception) -> bool:
    message = str(exc).lower()
    return "foreign key" in message or "restrict" in message or "violates" in message


__all__ = [
    "TierAdminConflictError",
    "TierAdminError",
    "TierAdminNotFoundError",
    "TierAdminService",
    "TierAdminValidationError",
    "serialize_capacity_pool",
    "serialize_model_policy",
    "serialize_tier",
    "serialize_tier_version",
]
