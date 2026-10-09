from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from src.db.tiers import (
    TierActivationActiveVersionChangedError,
    TierActivationConfigurationChangedError,
    TierBootstrapIdempotencyConflictError,
    TierBootstrapResult,
    TierCapacityPoolRecord,
    TierCapacityPoolMutationResult,
    TierConfigurationChildNotFoundError,
    TierConfigurationIdentityImmutableError,
    TierConfigurationMutationError,
    TierConfigurationPoolInUseError,
    TierConfigurationPoolReferenceError,
    TierConfigurationStaleError,
    TierConfigurationVersionNotDraftError,
    TierConfigurationVersionNotFoundError,
    TierModelPolicyRecord,
    TierModelPolicyMutationResult,
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

    async def get_tier_detail(
        self,
        tier_id: str,
        *,
        include_versions: bool = True,
    ) -> dict[str, Any]:
        tier = await self.require_tier(tier_id)
        versions = (
            await self.repository.list_tier_versions(tier_id) if include_versions else []
        )
        return {
            "tier": serialize_tier(tier),
            "versions": [serialize_tier_version(version) for version in versions],
        }

    async def list_tier_versions_page(
        self,
        tier_id: str,
        *,
        statuses: tuple[str, ...],
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        await self.require_tier(tier_id)
        try:
            records, total = await self.repository.list_tier_versions_page(
                tier_id,
                statuses=statuses,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            raise TierAdminValidationError(str(exc)) from exc
        return {
            "data": [serialize_tier_version(record) for record in records],
            "pagination": _pagination(total=total, limit=limit, offset=offset),
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

    async def create_tier_with_initial_draft(
        self,
        payload: Mapping[str, Any],
        *,
        principal_scope: str,
        idempotency_key: str,
        created_by_account_id: str | None,
        created_by_kind: str,
    ) -> TierBootstrapResult:
        fields = normalize_tier_create(payload)
        idempotency_key = str(idempotency_key or "").strip()
        if not idempotency_key:
            raise TierAdminValidationError("Idempotency-Key header is required")
        if len(idempotency_key) > 200:
            raise TierAdminValidationError(
                "Idempotency-Key header must be at most 200 characters"
            )
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "tier": fields,
                    "initial_version": {"status": "draft", "version_number": 1},
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            return await self.repository.create_tier_with_initial_draft(
                principal_scope=principal_scope,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                **fields,
                created_by_account_id=created_by_account_id,
                created_by_kind=created_by_kind,
            )
        except TierBootstrapIdempotencyConflictError as exc:
            raise TierAdminConflictError(
                {
                    "code": "tier_bootstrap_idempotency_conflict",
                    "message": "This idempotency key was already used for different tier input.",
                }
            ) from exc
        except ValueError as exc:
            raise TierAdminValidationError(str(exc)) from exc
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

        if existing.enabled and not fields["enabled"]:
            live_assignments = (
                await self.repository.count_live_or_scheduled_tier_assignments(tier_id)
            )
            if live_assignments:
                raise TierAdminConflictError(
                    "Tier has enabled live or scheduled organization assignments"
                )

        try:
            updated = await self.repository.update_tier(tier_id, **fields)
        except Exception as exc:
            if _looks_like_unique_violation(exc):
                raise TierAdminConflictError("A tier with this tier_key already exists") from exc
            if _looks_like_enabled_assignment_violation(exc):
                raise TierAdminConflictError(
                    "Tier has enabled live or scheduled organization assignments"
                ) from exc
            raise
        if updated is None:
            raise TierAdminNotFoundError("Tier not found")
        return updated

    async def delete_tier(self, tier_id: str) -> dict[str, Any]:
        existing = await self.require_tier(tier_id)
        live_assignments = await self.repository.count_live_or_scheduled_tier_assignments(
            existing.tier_id
        )
        if live_assignments:
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
        *,
        created_by_account_id: str | None = None,
        created_by_kind: str = "unknown",
    ) -> TierVersionRecord:
        await self.require_tier(tier_id)
        if payload.get("version_number") is None:
            fields = normalize_tier_version_create(payload, default_version_number=1)
            try:
                created = await self.repository.create_next_tier_version(
                    tier_id=tier_id,
                    created_by_account_id=created_by_account_id,
                    created_by_kind=created_by_kind,
                    metadata=fields["metadata"],
                )
            except ValueError as exc:
                raise TierAdminValidationError(str(exc)) from exc
            if created is None:
                raise TierAdminNotFoundError("Tier not found")
            return created
        fields = normalize_tier_version_create(
            payload,
            default_version_number=None,
        )

        try:
            return await self.repository.create_tier_version(
                tier_id=tier_id,
                version_number=fields["version_number"],
                status="draft",
                created_by_account_id=created_by_account_id,
                created_by_kind=created_by_kind,
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
        *,
        created_by_account_id: str | None = None,
        created_by_kind: str = "unknown",
    ) -> TierVersionRecord:
        await self.require_version_for_tier(tier_id, source_tier_version_id)
        try:
            cloned = await self.repository.clone_tier_version(
                tier_id=tier_id,
                source_tier_version_id=source_tier_version_id,
                created_by_account_id=created_by_account_id,
                created_by_kind=created_by_kind,
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

    async def get_activation_preview(
        self,
        tier_id: str,
        tier_version_id: str,
    ) -> dict[str, Any]:
        draft = await self.require_version_for_tier(tier_id, tier_version_id)
        if draft.status != "draft":
            raise TierAdminConflictError(
                {
                    "code": "tier_version_not_draft",
                    "message": "Only a draft version can be activated.",
                }
            )
        active = await self.repository.get_active_tier_version(tier_id)
        draft_policies = await self.repository.list_model_policies(tier_version_id)
        draft_pools = await self.repository.list_capacity_pools(tier_version_id)
        active_policies = (
            await self.repository.list_model_policies(active.tier_version_id) if active else []
        )
        active_pools = (
            await self.repository.list_capacity_pools(active.tier_version_id) if active else []
        )
        assignment_count = await self.repository.count_live_or_scheduled_tier_assignments(
            tier_id
        )
        organization_count = (
            await self.repository.count_live_or_scheduled_tier_organizations(tier_id)
        )
        pinned_assignment_count = (
            await self.repository.count_non_expired_enabled_assignments_pinned_to_version(
                active.tier_version_id
            )
            if active
            else 0
        )
        changes = _activation_changes(
            active_policies=active_policies,
            draft_policies=draft_policies,
            active_pools=active_pools,
            draft_pools=draft_pools,
        )
        warnings: list[dict[str, str]] = []
        if not any(
            policy.enabled and policy.access_mode == "allow" for policy in draft_policies
        ):
            warnings.append(
                {
                    "code": "tier_activation_no_enabled_allow_policies",
                    "message": "This draft has no enabled allow policies.",
                }
            )
        blockers: list[dict[str, Any]] = []
        if pinned_assignment_count:
            blockers.append(
                {
                    "code": "tier_activation_pinned_assignments",
                    "message": "Assignments pinned to the current live version must be moved first.",
                    "assignment_count": pinned_assignment_count,
                }
            )
        return {
            "draft": serialize_tier_version(draft),
            "draft_configuration_revision": draft.configuration_revision,
            "current_active_version": serialize_tier_version(active) if active else None,
            "expected_active_version_id": active.tier_version_id if active else None,
            "affected_assignment_count": assignment_count,
            "affected_organization_count": organization_count,
            "pinned_assignment_count": pinned_assignment_count,
            "changes": changes,
            "warnings": warnings,
            "blockers": blockers,
            "can_activate": not blockers,
        }

    async def activate_tier_version(
        self,
        tier_id: str,
        tier_version_id: str,
        *,
        expected_revision: int,
        expected_active_version_id: str | None,
        published_by_account_id: str | None,
    ) -> TierVersionRecord:
        try:
            version = await self.repository.activate_tier_version(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                expected_revision=expected_revision,
                expected_active_version_id=expected_active_version_id,
                published_by_account_id=published_by_account_id,
            )
        except TierActivationConfigurationChangedError as exc:
            raise TierAdminConflictError(
                {
                    "code": "tier_configuration_stale",
                    "message": "This draft changed after the activation preview. Review it again.",
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                }
            ) from exc
        except TierActivationActiveVersionChangedError as exc:
            raise TierAdminConflictError(
                {
                    "code": "tier_activation_active_changed",
                    "message": "The live version changed after the preview. Review activation again.",
                    "expected_active_version_id": exc.expected_active_version_id,
                    "current_active_version_id": exc.current_active_version_id,
                }
            ) from exc
        except ValueError as exc:
            if "pinned" in str(exc).lower():
                raise TierAdminConflictError(
                    {
                        "code": "tier_activation_pinned_assignments",
                        "message": str(exc),
                    }
                ) from exc
            raise TierAdminConflictError(
                {
                    "code": "tier_version_not_draft",
                    "message": str(exc),
                }
            ) from exc
        if version is None:
            raise TierAdminNotFoundError("Tier version not found")
        return version

    async def list_model_policies_page(
        self,
        tier_id: str,
        tier_version_id: str,
        **filters: Any,
    ) -> dict[str, Any]:
        try:
            page = await self.repository.list_model_policies_page(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                **filters,
            )
        except ValueError as exc:
            raise TierAdminValidationError(str(exc)) from exc
        if page is None:
            raise TierAdminNotFoundError("Tier version not found")
        return {
            "data": [serialize_model_policy(record) for record in page.records],
            "configuration_revision": page.configuration_revision,
            "version_updated_at": (
                page.version_updated_at.isoformat() if page.version_updated_at else None
            ),
            "pagination": _pagination(
                total=page.total,
                limit=int(filters["limit"]),
                offset=int(filters["offset"]),
            ),
        }

    async def list_capacity_pools_page(
        self,
        tier_id: str,
        tier_version_id: str,
        **filters: Any,
    ) -> dict[str, Any]:
        try:
            page = await self.repository.list_capacity_pools_page(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                **filters,
            )
        except ValueError as exc:
            raise TierAdminValidationError(str(exc)) from exc
        if page is None:
            raise TierAdminNotFoundError("Tier version not found")
        return {
            "data": [serialize_capacity_pool(record) for record in page.records],
            "configuration_revision": page.configuration_revision,
            "version_updated_at": (
                page.version_updated_at.isoformat() if page.version_updated_at else None
            ),
            "pagination": _pagination(
                total=page.total,
                limit=int(filters["limit"]),
                offset=int(filters["offset"]),
            ),
        }

    async def create_model_policy(
        self,
        tier_id: str,
        tier_version_id: str,
        payload: Mapping[str, Any],
    ) -> TierModelPolicyMutationResult:
        expected_revision = _expected_revision(payload)
        policy_payload = {key: value for key, value in payload.items() if key != "expected_revision"}
        policy = normalize_model_policy_records(tier_version_id, [policy_payload])[0]
        try:
            return await self.repository.create_model_policy(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                expected_revision=expected_revision,
                policy=policy,
            )
        except TierConfigurationMutationError as exc:
            raise _configuration_admin_error(exc) from exc
        except Exception as exc:
            if _looks_like_unique_violation(exc):
                raise TierAdminConflictError(
                    {
                        "code": "tier_policy_duplicate_callable",
                        "message": "This draft already has a policy for that callable.",
                    }
                ) from exc
            raise

    async def update_model_policy(
        self,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
        payload: Mapping[str, Any],
    ) -> TierModelPolicyMutationResult:
        expected_revision = _expected_revision(payload)
        existing = await self.repository.get_model_policy_for_version(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            tier_model_policy_id=tier_model_policy_id,
        )
        if existing is None:
            raise TierAdminNotFoundError("Model policy not found")
        if "callable_key" in payload and payload["callable_key"] != existing.callable_key:
            raise TierAdminValidationError("callable_key cannot be changed")
        merged = serialize_model_policy(existing)
        merged.update({key: value for key, value in payload.items() if key != "expected_revision"})
        merged["callable_key"] = existing.callable_key
        policy = normalize_model_policy_records(tier_version_id, [merged])[0]
        try:
            return await self.repository.update_model_policy(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                tier_model_policy_id=tier_model_policy_id,
                expected_revision=expected_revision,
                policy=policy,
            )
        except TierConfigurationMutationError as exc:
            raise _configuration_admin_error(exc) from exc

    async def delete_model_policy(
        self,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
        *,
        expected_revision: int,
    ) -> TierModelPolicyMutationResult:
        try:
            return await self.repository.delete_model_policy(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                tier_model_policy_id=tier_model_policy_id,
                expected_revision=expected_revision,
            )
        except TierConfigurationMutationError as exc:
            raise _configuration_admin_error(exc) from exc

    async def bulk_update_model_policy_limits(
        self,
        tier_id: str,
        tier_version_id: str,
        payload: Mapping[str, Any],
    ) -> Any:
        try:
            return await self.repository.bulk_update_model_policy_limits(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                expected_revision=_expected_revision(payload),
                update_rpm_limit="rpm_limit" in payload,
                rpm_limit=payload.get("rpm_limit"),
                update_tpm_limit="tpm_limit" in payload,
                tpm_limit=payload.get("tpm_limit"),
                tier_model_policy_ids=(
                    tuple(str(value) for value in payload["policy_ids"])
                    if payload.get("policy_ids")
                    else None
                ),
                search=payload.get("search"),
                enabled=payload.get("enabled"),
                access_mode=payload.get("access_mode"),
                capacity_pool_key=payload.get("capacity_pool_key"),
            )
        except ValueError as exc:
            if isinstance(exc, TierConfigurationMutationError):
                raise _configuration_admin_error(exc) from exc
            raise TierAdminValidationError(str(exc)) from exc

    async def create_capacity_pool(
        self,
        tier_id: str,
        tier_version_id: str,
        payload: Mapping[str, Any],
    ) -> TierCapacityPoolMutationResult:
        expected_revision = _expected_revision(payload)
        pool_payload = {key: value for key, value in payload.items() if key != "expected_revision"}
        pool = normalize_capacity_pool_records(tier_version_id, [pool_payload])[0]
        try:
            return await self.repository.create_capacity_pool(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                expected_revision=expected_revision,
                pool=pool,
            )
        except TierConfigurationMutationError as exc:
            raise _configuration_admin_error(exc) from exc
        except Exception as exc:
            if _looks_like_unique_violation(exc):
                raise TierAdminConflictError(
                    {
                        "code": "tier_pool_duplicate_identity",
                        "message": "This draft already has that pool and callable combination.",
                    }
                ) from exc
            raise

    async def update_capacity_pool(
        self,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
        payload: Mapping[str, Any],
    ) -> TierCapacityPoolMutationResult:
        expected_revision = _expected_revision(payload)
        existing = await self.repository.get_capacity_pool_for_version(
            tier_id=tier_id,
            tier_version_id=tier_version_id,
            tier_capacity_pool_id=tier_capacity_pool_id,
        )
        if existing is None:
            raise TierAdminNotFoundError("Capacity pool not found")
        for identity_field in ("pool_key", "callable_key"):
            if identity_field in payload and payload[identity_field] != getattr(
                existing, identity_field
            ):
                raise TierAdminValidationError(
                    "pool_key and callable_key cannot be changed"
                )
        merged = serialize_capacity_pool(existing)
        merged.update({key: value for key, value in payload.items() if key != "expected_revision"})
        merged["pool_key"] = existing.pool_key
        merged["callable_key"] = existing.callable_key
        pool = normalize_capacity_pool_records(tier_version_id, [merged])[0]
        try:
            return await self.repository.update_capacity_pool(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                tier_capacity_pool_id=tier_capacity_pool_id,
                expected_revision=expected_revision,
                pool=pool,
            )
        except TierConfigurationMutationError as exc:
            raise _configuration_admin_error(exc) from exc

    async def delete_capacity_pool(
        self,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
        *,
        expected_revision: int,
    ) -> TierCapacityPoolMutationResult:
        try:
            return await self.repository.delete_capacity_pool(
                tier_id=tier_id,
                tier_version_id=tier_version_id,
                tier_capacity_pool_id=tier_capacity_pool_id,
                expected_revision=expected_revision,
            )
        except TierConfigurationMutationError as exc:
            raise _configuration_admin_error(exc) from exc

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

def _activation_changes(
    *,
    active_policies: Sequence[TierModelPolicyRecord],
    draft_policies: Sequence[TierModelPolicyRecord],
    active_pools: Sequence[TierCapacityPoolRecord],
    draft_pools: Sequence[TierCapacityPoolRecord],
) -> dict[str, Any]:
    active_policy_map = {record.callable_key: record for record in active_policies}
    draft_policy_map = {record.callable_key: record for record in draft_policies}
    active_pool_map = {
        (record.pool_key, record.callable_key): record for record in active_pools
    }
    draft_pool_map = {
        (record.pool_key, record.callable_key): record for record in draft_pools
    }

    policy_added = sorted(draft_policy_map.keys() - active_policy_map.keys())
    policy_removed = sorted(active_policy_map.keys() - draft_policy_map.keys())
    policy_changed = sorted(
        key
        for key in active_policy_map.keys() & draft_policy_map.keys()
        if _policy_fingerprint(active_policy_map[key])
        != _policy_fingerprint(draft_policy_map[key])
    )
    pool_added_keys = sorted(draft_pool_map.keys() - active_pool_map.keys())
    pool_removed_keys = sorted(active_pool_map.keys() - draft_pool_map.keys())
    pool_changed_keys = sorted(
        key
        for key in active_pool_map.keys() & draft_pool_map.keys()
        if _pool_fingerprint(active_pool_map[key]) != _pool_fingerprint(draft_pool_map[key])
    )
    return {
        "policy_added": _bounded_change_items(policy_added),
        "policy_removed": _bounded_change_items(policy_removed),
        "policy_changed": _bounded_change_items(policy_changed),
        "pool_added": _bounded_change_items(
            [f"{pool_key} · {callable_key}" for pool_key, callable_key in pool_added_keys]
        ),
        "pool_removed": _bounded_change_items(
            [f"{pool_key} · {callable_key}" for pool_key, callable_key in pool_removed_keys]
        ),
        "pool_changed": _bounded_change_items(
            [f"{pool_key} · {callable_key}" for pool_key, callable_key in pool_changed_keys]
        ),
    }


def _bounded_change_items(items: Sequence[str], *, limit: int = 20) -> dict[str, Any]:
    return {
        "count": len(items),
        "items": list(items[:limit]),
        "truncated": len(items) > limit,
    }


def _policy_fingerprint(record: TierModelPolicyRecord) -> str:
    return json.dumps(
        {
            "enabled": record.enabled,
            "access_mode": record.access_mode,
            "rpm_limit": record.rpm_limit,
            "tpm_limit": record.tpm_limit,
            "rph_limit": record.rph_limit,
            "rpd_limit": record.rpd_limit,
            "tpd_limit": record.tpd_limit,
            "max_parallel_requests": record.max_parallel_requests,
            "batch_rpm_limit": record.batch_rpm_limit,
            "batch_tpm_limit": record.batch_tpm_limit,
            "pricing": record.pricing,
            "capacity_pool_key": record.capacity_pool_key,
            "priority": record.priority,
            "metadata": record.metadata,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _pool_fingerprint(record: TierCapacityPoolRecord) -> str:
    return json.dumps(
        {
            "rpm_capacity": record.rpm_capacity,
            "tpm_capacity": record.tpm_capacity,
            "max_parallel_requests": record.max_parallel_requests,
            "strategy": record.strategy,
            "saturation_threshold": record.saturation_threshold,
            "burst_multiplier": record.burst_multiplier,
            "metadata": record.metadata,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _expected_revision(payload: Mapping[str, Any]) -> int:
    value = payload.get("expected_revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TierAdminValidationError("expected_revision must be a non-negative integer")
    return value


def _pagination(*, total: int, limit: int, offset: int) -> dict[str, Any]:
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


def _configuration_admin_error(exc: TierConfigurationMutationError) -> TierAdminError:
    if isinstance(
        exc,
        (TierConfigurationVersionNotFoundError, TierConfigurationChildNotFoundError),
    ):
        return TierAdminNotFoundError(str(exc))
    if isinstance(exc, TierConfigurationStaleError):
        return TierAdminConflictError(
            {
                "code": "tier_configuration_stale",
                "message": "This draft changed after you loaded it. Refresh before saving.",
                "expected_revision": exc.expected_revision,
                "current_revision": exc.current_revision,
            }
        )
    if isinstance(exc, TierConfigurationVersionNotDraftError):
        return TierAdminConflictError(
            {
                "code": "tier_version_not_draft",
                "message": "This version is no longer an editable draft.",
            }
        )
    if isinstance(exc, TierConfigurationPoolInUseError):
        return TierAdminConflictError(
            {
                "code": "tier_pool_in_use",
                "message": "Remove policy references before deleting this capacity pool.",
            }
        )
    if isinstance(exc, TierConfigurationPoolReferenceError):
        return TierAdminValidationError(str(exc))
    if isinstance(exc, TierConfigurationIdentityImmutableError):
        return TierAdminValidationError(str(exc))
    return TierAdminConflictError(str(exc))


def _looks_like_unique_violation(exc: Exception) -> bool:
    message = str(exc).lower()
    return "duplicate key" in message or "unique constraint" in message


def _looks_like_restrict_violation(exc: Exception) -> bool:
    message = str(exc).lower()
    return "foreign key" in message or "restrict" in message or "violates" in message


def _looks_like_enabled_assignment_violation(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "cannot disable tier while enabled organization assignments exist" in message
        or "deltallm_tier_disable_assignment_guard" in message
    )


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
