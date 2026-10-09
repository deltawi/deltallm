from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from src.audit.actions import AuditAction
from src.auth.roles import OrganizationRole, PlatformRole
from src.db.tiers import (
    TierActivationActiveVersionChangedError,
    TierActivationConfigurationChangedError,
    TierBootstrapIdempotencyConflictError,
    TierBootstrapResult,
    TierCapacityPoolMutationResult,
    TierCapacityPoolPage,
    TierCapacityPoolRecord,
    TierCatalogVersionSummaryRecord,
    TierConfigurationStaleError,
    TierConfigurationPoolInUseError,
    TierConfigurationVersionNotDraftError,
    TierConfigurationVersionNotFoundError,
    TierModelPolicyMutationResult,
    TierModelPolicyBulkMutationResult,
    TierModelPolicyPage,
    TierModelPolicyRecord,
    TierRecord,
    TierVersionRecord,
)
from src.models.platform_auth import PlatformAuthContext
from src.services.tier_capacity_fair_share import fair_share_boost_key
from src.services.tier_policy_models import (
    CompiledTierCapacityPoolMember,
    CompiledTierCapacityPoolPolicy,
    TierPolicySnapshot,
    empty_tier_policy_snapshot,
)
from tests.conftest import FakeRedis


class _RecordingAuditService:
    def __init__(self) -> None:
        self.sync_calls: list[tuple[object, list[object]]] = []

    async def record_event_sync(self, event, *, payloads=None):  # noqa: ANN001, ANN201
        self.sync_calls.append((event, list(payloads or [])))

    def record_event(self, event, *, payloads=None, critical=False):  # noqa: ANN001, ANN201
        del event, payloads, critical


class _RecordingGovernanceInvalidationService:
    def __init__(self, *, fail_local: bool = False, notify_result: bool = True) -> None:
        self.fail_local = fail_local
        self.notify_result = notify_result
        self.local_targets: list[tuple[str, ...]] = []
        self.notified_targets: list[tuple[str, ...]] = []

    async def invalidate_local(self, *targets: str) -> None:
        self.local_targets.append(tuple(targets))
        if self.fail_local:
            raise RuntimeError("tier policy reload unavailable")

    async def notify(self, *targets: str) -> bool:
        self.notified_targets.append(tuple(targets))
        return self.notify_result


class _FakeTierRepository:
    def __init__(self) -> None:
        self.tiers: dict[str, TierRecord] = {}
        self.versions: dict[str, TierVersionRecord] = {}
        self.model_policies: dict[str, list[TierModelPolicyRecord]] = {}
        self.capacity_pools: dict[str, list[TierCapacityPoolRecord]] = {}
        self.active_assignment_counts: dict[str, int] = {}
        self.update_error: str | None = None
        self.archive_error: str | None = None
        self.bootstrap_requests: dict[tuple[str, str], tuple[str, str, str]] = {}

    def seed_tier(
        self,
        *,
        tier_id: str = "tier-1",
        tier_key: str = "growth",
        name: str = "Growth",
    ) -> TierRecord:
        now = datetime.now(tz=UTC)
        record = TierRecord(
            tier_id=tier_id,
            tier_key=tier_key,
            name=name,
            enabled=True,
            created_at=now,
            updated_at=now,
        )
        self.tiers[tier_id] = record
        return self._tier_with_counts(record)

    def seed_version(
        self,
        *,
        tier_version_id: str = "version-1",
        tier_id: str = "tier-1",
        version_number: int = 1,
        status: str = "draft",
    ) -> TierVersionRecord:
        now = datetime.now(tz=UTC)
        record = TierVersionRecord(
            tier_version_id=tier_version_id,
            tier_id=tier_id,
            version_number=version_number,
            status=status,
            created_at=now,
            updated_at=now,
        )
        self.versions[tier_version_id] = record
        return record

    async def list_tiers(
        self,
        *,
        search: str | None = None,
        enabled: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TierRecord], int]:
        records = [self._tier_with_counts(record) for record in self.tiers.values()]
        if search:
            lowered = search.lower()
            records = [
                record
                for record in records
                if lowered in record.tier_key.lower()
                or lowered in record.name.lower()
                or lowered in str(record.description or "").lower()
            ]
        if enabled is not None:
            records = [record for record in records if record.enabled is enabled]
        records.sort(key=lambda item: item.tier_key)
        return records[offset : offset + limit], len(records)

    async def get_tier(self, tier_id: str) -> TierRecord | None:
        record = self.tiers.get(tier_id)
        return self._tier_with_counts(record) if record is not None else None

    async def get_tier_by_key(self, tier_key: str) -> TierRecord | None:
        for record in self.tiers.values():
            if record.tier_key == tier_key:
                return self._tier_with_counts(record)
        return None

    async def create_tier(
        self,
        *,
        tier_key: str,
        name: str,
        description: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> TierRecord:
        tier_id = f"tier-{len(self.tiers) + 1}"
        now = datetime.now(tz=UTC)
        record = TierRecord(
            tier_id=tier_id,
            tier_key=tier_key,
            name=name,
            description=description,
            enabled=enabled,
            metadata=metadata,
            created_at=now,
            updated_at=now,
        )
        self.tiers[tier_id] = record
        return self._tier_with_counts(record)

    async def create_tier_with_initial_draft(
        self,
        *,
        principal_scope: str,
        idempotency_key: str,
        request_hash: str,
        tier_key: str,
        name: str,
        description: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
        created_by_account_id: str | None,
        created_by_kind: str,
    ) -> TierBootstrapResult:
        request_key = (principal_scope, idempotency_key)
        replay = self.bootstrap_requests.get(request_key)
        if replay is not None:
            saved_hash, tier_id, version_id = replay
            if saved_hash != request_hash:
                raise TierBootstrapIdempotencyConflictError("mismatched replay")
            tier = await self.get_tier(tier_id)
            version = self.versions.get(version_id)
            assert tier is not None and version is not None
            return TierBootstrapResult(tier, version, "replayed")
        if await self.get_tier_by_key(tier_key) is not None:
            raise RuntimeError("duplicate key value violates unique constraint")
        tier = await self.create_tier(
            tier_key=tier_key,
            name=name,
            description=description,
            enabled=enabled,
            metadata=metadata,
        )
        version = await self.create_tier_version(
            tier_id=tier.tier_id,
            version_number=1,
            created_by_account_id=created_by_account_id,
            created_by_kind=created_by_kind,
        )
        self.bootstrap_requests[request_key] = (request_hash, tier.tier_id, version.tier_version_id)
        tier = self._tier_with_counts(self.tiers[tier.tier_id])
        return TierBootstrapResult(tier, version, "created")

    async def update_tier(
        self,
        tier_id: str,
        *,
        tier_key: str,
        name: str,
        description: str | None,
        enabled: bool,
        metadata: dict[str, Any] | None,
    ) -> TierRecord | None:
        existing = self.tiers.get(tier_id)
        if existing is None:
            return None
        if self.update_error is not None:
            raise RuntimeError(self.update_error)
        updated = replace(
            existing,
            tier_key=tier_key,
            name=name,
            description=description,
            enabled=enabled,
            metadata=metadata,
            updated_at=datetime.now(tz=UTC),
        )
        self.tiers[tier_id] = updated
        return self._tier_with_counts(updated)

    async def delete_tier(self, tier_id: str) -> bool:
        return self.tiers.pop(tier_id, None) is not None

    async def count_active_tier_assignments(self, tier_id: str) -> int:
        return int(self.active_assignment_counts.get(tier_id, 0))

    async def count_live_or_scheduled_tier_assignments(self, tier_id: str) -> int:
        return int(self.active_assignment_counts.get(tier_id, 0))

    async def count_live_or_scheduled_tier_organizations(self, tier_id: str) -> int:
        return int(self.active_assignment_counts.get(tier_id, 0))

    async def list_tier_versions(self, tier_id: str) -> list[TierVersionRecord]:
        records = [record for record in self.versions.values() if record.tier_id == tier_id]
        return sorted(records, key=lambda item: item.version_number, reverse=True)

    async def list_tier_versions_page(
        self,
        tier_id: str,
        *,
        statuses: tuple[str, ...],
        limit: int,
        offset: int,
    ) -> tuple[list[TierVersionRecord], int]:
        records = await self.list_tier_versions(tier_id)
        if statuses:
            records = [record for record in records if record.status in statuses]
        return records[offset : offset + limit], len(records)

    async def get_tier_version(self, tier_version_id: str) -> TierVersionRecord | None:
        return self.versions.get(tier_version_id)

    async def get_active_tier_version(self, tier_id: str) -> TierVersionRecord | None:
        versions = [
            version
            for version in self.versions.values()
            if version.tier_id == tier_id and version.status == "active"
        ]
        return max(versions, key=lambda version: version.version_number, default=None)

    async def count_non_expired_enabled_assignments_pinned_to_version(
        self,
        tier_version_id: str,
    ) -> int:
        return 0

    async def create_tier_version(
        self,
        *,
        tier_id: str,
        version_number: int,
        status: str = "draft",
        published_at=None,  # noqa: ANN001
        published_by_account_id: str | None = None,
        created_by_account_id: str | None = None,
        created_by_kind: str = "unknown",
        source_tier_version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TierVersionRecord:
        del published_at, published_by_account_id
        tier_version_id = f"version-{len(self.versions) + 1}"
        record = TierVersionRecord(
            tier_version_id=tier_version_id,
            tier_id=tier_id,
            version_number=version_number,
            status=status,
            created_by_account_id=created_by_account_id,
            created_by_kind=created_by_kind,
            source_tier_version_id=source_tier_version_id,
            metadata=metadata,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        self.versions[tier_version_id] = record
        return record

    async def create_next_tier_version(
        self,
        *,
        tier_id: str,
        created_by_account_id: str | None = None,
        created_by_kind: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> TierVersionRecord | None:
        if tier_id not in self.tiers:
            return None
        versions = [version for version in self.versions.values() if version.tier_id == tier_id]
        return await self.create_tier_version(
            tier_id=tier_id,
            version_number=max((version.version_number for version in versions), default=0) + 1,
            created_by_account_id=created_by_account_id,
            created_by_kind=created_by_kind,
            metadata=metadata,
        )

    async def clone_tier_version(
        self,
        *,
        tier_id: str,
        source_tier_version_id: str,
        created_by_account_id: str | None = None,
        created_by_kind: str = "unknown",
    ) -> TierVersionRecord | None:
        source = self.versions.get(source_tier_version_id)
        if source is None or source.tier_id != tier_id:
            return None
        tier_versions = [version for version in self.versions.values() if version.tier_id == tier_id]
        tier_version_id = f"version-{len(self.versions) + 1}"
        cloned_policies = [
            replace(
                policy,
                tier_model_policy_id=f"policy-cloned-{index + 1}",
                tier_version_id=tier_version_id,
            )
            for index, policy in enumerate(self.model_policies.get(source_tier_version_id, []))
        ]
        cloned_pools = [
            replace(
                pool,
                tier_capacity_pool_id=f"pool-cloned-{index + 1}",
                tier_version_id=tier_version_id,
            )
            for index, pool in enumerate(self.capacity_pools.get(source_tier_version_id, []))
        ]
        record = TierVersionRecord(
            tier_version_id=tier_version_id,
            tier_id=tier_id,
            version_number=max((version.version_number for version in tier_versions), default=0) + 1,
            status="draft",
            created_by_account_id=created_by_account_id,
            created_by_kind=created_by_kind,
            source_tier_version_id=source_tier_version_id,
            metadata=source.metadata,
            model_policy_count=len(cloned_policies),
            capacity_pool_count=len(cloned_pools),
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        self.versions[tier_version_id] = record
        self.model_policies[tier_version_id] = cloned_policies
        self.capacity_pools[tier_version_id] = cloned_pools
        return record

    async def activate_tier_version(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        expected_active_version_id: str | None,
        published_by_account_id: str | None = None,
    ) -> TierVersionRecord | None:
        version = self.versions.get(tier_version_id)
        if version is None or version.tier_id != tier_id:
            return None
        if version.configuration_revision != expected_revision:
            raise TierActivationConfigurationChangedError(
                expected_revision=expected_revision,
                current_revision=version.configuration_revision,
            )
        active = await self.get_active_tier_version(tier_id)
        active_id = active.tier_version_id if active else None
        if active_id != expected_active_version_id:
            raise TierActivationActiveVersionChangedError(
                expected_active_version_id=expected_active_version_id,
                current_active_version_id=active_id,
            )
        if version.status != "draft":
            raise ValueError("only draft tier versions can be activated")
        for version_id, record in list(self.versions.items()):
            if record.tier_id == tier_id and record.status == "active":
                self.versions[version_id] = replace(record, status="archived")
        activated = replace(
            version,
            status="active",
            published_at=datetime.now(tz=UTC),
            published_by_account_id=published_by_account_id,
        )
        self.versions[tier_version_id] = activated
        return activated

    async def archive_tier_version(self, tier_version_id: str) -> TierVersionRecord | None:
        if self.archive_error is not None:
            raise ValueError(self.archive_error)
        record = self.versions.get(tier_version_id)
        if record is None:
            return None
        updated = replace(record, status="archived")
        self.versions[tier_version_id] = updated
        return updated

    async def list_model_policies(self, tier_version_id: str) -> list[TierModelPolicyRecord]:
        return list(self.model_policies.get(tier_version_id, []))

    async def list_model_policies_page(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        search: str | None,
        enabled: bool | None,
        access_mode: str | None,
        capacity_pool_key: str | None,
        sort: str,
        order: str,
        limit: int,
        offset: int,
    ) -> TierModelPolicyPage | None:
        version = self.versions.get(tier_version_id)
        if version is None or version.tier_id != tier_id:
            return None
        records = list(self.model_policies.get(tier_version_id, []))
        if search:
            lowered = search.lower()
            records = [
                record
                for record in records
                if lowered in record.callable_key.lower()
                or lowered in str(record.capacity_pool_key or "").lower()
            ]
        if enabled is not None:
            records = [record for record in records if record.enabled is enabled]
        if access_mode:
            records = [record for record in records if record.access_mode == access_mode]
        if capacity_pool_key:
            records = [
                record for record in records if record.capacity_pool_key == capacity_pool_key
            ]
        records.sort(
            key=lambda record: getattr(record, sort),
            reverse=order == "desc",
        )
        return TierModelPolicyPage(
            tuple(records[offset : offset + limit]),
            len(records),
            version.configuration_revision,
            version.updated_at,
        )

    async def get_model_policy_for_version(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
    ) -> TierModelPolicyRecord | None:
        version = self.versions.get(tier_version_id)
        if version is None or version.tier_id != tier_id:
            return None
        return next(
            (
                policy
                for policy in self.model_policies.get(tier_version_id, [])
                if policy.tier_model_policy_id == tier_model_policy_id
            ),
            None,
        )

    async def create_model_policy(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        policy: TierModelPolicyRecord,
    ) -> TierModelPolicyMutationResult:
        self._guard_configuration(tier_id, tier_version_id, expected_revision)
        records = self.model_policies.setdefault(tier_version_id, [])
        created = replace(
            policy,
            tier_model_policy_id=f"policy-{len(records) + 1}",
            tier_version_id=tier_version_id,
        )
        records.append(created)
        version = self._bump_configuration_revision(tier_version_id)
        return TierModelPolicyMutationResult(
            created,
            version.configuration_revision,
            version.updated_at,
        )

    async def update_model_policy(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
        expected_revision: int,
        policy: TierModelPolicyRecord,
    ) -> TierModelPolicyMutationResult:
        self._guard_configuration(tier_id, tier_version_id, expected_revision)
        records = self.model_policies.get(tier_version_id, [])
        index = next(
            (
                index
                for index, current in enumerate(records)
                if current.tier_model_policy_id == tier_model_policy_id
            ),
            None,
        )
        if index is None:
            raise TierConfigurationVersionNotFoundError("model policy not found")
        updated = replace(
            policy,
            tier_model_policy_id=tier_model_policy_id,
            tier_version_id=tier_version_id,
        )
        records[index] = updated
        version = self._bump_configuration_revision(tier_version_id)
        return TierModelPolicyMutationResult(
            updated,
            version.configuration_revision,
            version.updated_at,
        )

    async def delete_model_policy(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_model_policy_id: str,
        expected_revision: int,
    ) -> TierModelPolicyMutationResult:
        self._guard_configuration(tier_id, tier_version_id, expected_revision)
        records = self.model_policies.get(tier_version_id, [])
        remaining = [
            policy
            for policy in records
            if policy.tier_model_policy_id != tier_model_policy_id
        ]
        if len(remaining) == len(records):
            raise TierConfigurationVersionNotFoundError("model policy not found")
        self.model_policies[tier_version_id] = remaining
        version = self._bump_configuration_revision(tier_version_id)
        return TierModelPolicyMutationResult(
            None,
            version.configuration_revision,
            version.updated_at,
        )

    async def bulk_update_model_policy_limits(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        update_rpm_limit: bool,
        rpm_limit: int | None,
        update_tpm_limit: bool,
        tpm_limit: int | None,
        tier_model_policy_ids: tuple[str, ...] | None,
        search: str | None,
        enabled: bool | None,
        access_mode: str | None,
        capacity_pool_key: str | None,
    ) -> TierModelPolicyBulkMutationResult:
        self._guard_configuration(tier_id, tier_version_id, expected_revision)
        records = self.model_policies.get(tier_version_id, [])
        selected_ids = set(tier_model_policy_ids or ())
        if selected_ids and not selected_ids.issubset(
            {record.tier_model_policy_id for record in records}
        ):
            raise TierConfigurationVersionNotFoundError("model policy not found")
        lowered = str(search or "").lower()
        affected = 0
        updated_records: list[TierModelPolicyRecord] = []
        for record in records:
            matches = (
                (not selected_ids or record.tier_model_policy_id in selected_ids)
                and (not lowered or lowered in record.callable_key.lower() or lowered in str(record.capacity_pool_key or "").lower())
                and (enabled is None or record.enabled is enabled)
                and (not access_mode or record.access_mode == access_mode)
                and (not capacity_pool_key or record.capacity_pool_key == capacity_pool_key)
            )
            if not matches:
                updated_records.append(record)
                continue
            affected += 1
            updated_records.append(
                replace(
                    record,
                    rpm_limit=rpm_limit if update_rpm_limit else record.rpm_limit,
                    tpm_limit=tpm_limit if update_tpm_limit else record.tpm_limit,
                )
            )
        self.model_policies[tier_version_id] = updated_records
        version = self._bump_configuration_revision(tier_version_id) if affected else self.versions[tier_version_id]
        return TierModelPolicyBulkMutationResult(
            affected,
            version.configuration_revision,
            version.updated_at,
        )

    async def list_capacity_pools(self, tier_version_id: str) -> list[TierCapacityPoolRecord]:
        return list(self.capacity_pools.get(tier_version_id, []))

    async def list_capacity_pools_page(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        search: str | None,
        callable_key: str | None,
        strategy: str | None,
        sort: str,
        order: str,
        limit: int,
        offset: int,
    ) -> TierCapacityPoolPage | None:
        version = self.versions.get(tier_version_id)
        if version is None or version.tier_id != tier_id:
            return None
        records = list(self.capacity_pools.get(tier_version_id, []))
        if search:
            lowered = search.lower()
            records = [
                record
                for record in records
                if lowered in record.pool_key.lower()
                or lowered in record.callable_key.lower()
            ]
        if callable_key:
            records = [record for record in records if record.callable_key == callable_key]
        if strategy:
            records = [record for record in records if record.strategy == strategy]
        records.sort(
            key=lambda record: getattr(record, sort),
            reverse=order == "desc",
        )
        return TierCapacityPoolPage(
            tuple(records[offset : offset + limit]),
            len(records),
            version.configuration_revision,
            version.updated_at,
        )

    async def get_capacity_pool_for_version(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
    ) -> TierCapacityPoolRecord | None:
        version = self.versions.get(tier_version_id)
        if version is None or version.tier_id != tier_id:
            return None
        return next(
            (
                pool
                for pool in self.capacity_pools.get(tier_version_id, [])
                if pool.tier_capacity_pool_id == tier_capacity_pool_id
            ),
            None,
        )

    async def create_capacity_pool(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
        pool: TierCapacityPoolRecord,
    ) -> TierCapacityPoolMutationResult:
        self._guard_configuration(tier_id, tier_version_id, expected_revision)
        records = self.capacity_pools.setdefault(tier_version_id, [])
        created = replace(
            pool,
            tier_capacity_pool_id=f"pool-{len(records) + 1}",
            tier_version_id=tier_version_id,
        )
        records.append(created)
        version = self._bump_configuration_revision(tier_version_id)
        return TierCapacityPoolMutationResult(
            created,
            version.configuration_revision,
            version.updated_at,
        )

    async def update_capacity_pool(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
        expected_revision: int,
        pool: TierCapacityPoolRecord,
    ) -> TierCapacityPoolMutationResult:
        self._guard_configuration(tier_id, tier_version_id, expected_revision)
        records = self.capacity_pools.get(tier_version_id, [])
        index = next(
            (
                index
                for index, current in enumerate(records)
                if current.tier_capacity_pool_id == tier_capacity_pool_id
            ),
            None,
        )
        if index is None:
            raise TierConfigurationVersionNotFoundError("capacity pool not found")
        updated = replace(
            pool,
            tier_capacity_pool_id=tier_capacity_pool_id,
            tier_version_id=tier_version_id,
        )
        records[index] = updated
        version = self._bump_configuration_revision(tier_version_id)
        return TierCapacityPoolMutationResult(
            updated,
            version.configuration_revision,
            version.updated_at,
        )

    async def delete_capacity_pool(
        self,
        *,
        tier_id: str,
        tier_version_id: str,
        tier_capacity_pool_id: str,
        expected_revision: int,
    ) -> TierCapacityPoolMutationResult:
        self._guard_configuration(tier_id, tier_version_id, expected_revision)
        target = next(
            (
                pool
                for pool in self.capacity_pools.get(tier_version_id, [])
                if pool.tier_capacity_pool_id == tier_capacity_pool_id
            ),
            None,
        )
        if target is not None and any(
            policy.capacity_pool_key == target.pool_key
            and policy.callable_key == target.callable_key
            for policy in self.model_policies.get(tier_version_id, [])
        ):
            raise TierConfigurationPoolInUseError("capacity pool is in use")
        records = self.capacity_pools.get(tier_version_id, [])
        remaining = [
            pool
            for pool in records
            if pool.tier_capacity_pool_id != tier_capacity_pool_id
        ]
        if len(remaining) == len(records):
            raise TierConfigurationVersionNotFoundError("capacity pool not found")
        self.capacity_pools[tier_version_id] = remaining
        version = self._bump_configuration_revision(tier_version_id)
        return TierCapacityPoolMutationResult(
            None,
            version.configuration_revision,
            version.updated_at,
        )

    def _guard_configuration(
        self,
        tier_id: str,
        tier_version_id: str,
        expected_revision: int,
    ) -> TierVersionRecord:
        version = self.versions.get(tier_version_id)
        if version is None or version.tier_id != tier_id:
            raise TierConfigurationVersionNotFoundError("tier version not found")
        if version.status != "draft":
            raise TierConfigurationVersionNotDraftError("tier version is not a draft")
        if version.configuration_revision != expected_revision:
            raise TierConfigurationStaleError(
                expected_revision=expected_revision,
                current_revision=version.configuration_revision,
            )
        return version

    def _bump_configuration_revision(self, tier_version_id: str) -> TierVersionRecord:
        version = self.versions[tier_version_id]
        updated = replace(
            version,
            configuration_revision=version.configuration_revision + 1,
            updated_at=datetime.now(tz=UTC),
        )
        self.versions[tier_version_id] = updated
        return updated

    def _tier_with_counts(self, record: TierRecord) -> TierRecord:
        versions = [
            version for version in self.versions.values() if version.tier_id == record.tier_id
        ]
        active_versions = [version for version in versions if version.status == "active"]
        active_versions.sort(key=lambda item: item.version_number, reverse=True)
        draft_versions = [version for version in versions if version.status == "draft"]
        draft_versions.sort(key=lambda item: item.version_number, reverse=True)

        def summary(version: TierVersionRecord) -> TierCatalogVersionSummaryRecord:
            return TierCatalogVersionSummaryRecord(
                tier_version_id=version.tier_version_id,
                version_number=version.version_number,
                configuration_revision=version.configuration_revision,
                model_policy_count=len(
                    self.model_policies.get(version.tier_version_id, [])
                ),
                capacity_pool_count=len(
                    self.capacity_pools.get(version.tier_version_id, [])
                ),
                created_by_account_id=version.created_by_account_id,
                created_by_kind=version.created_by_kind,
                created_by_email=version.created_by_email,
                source_tier_version_id=version.source_tier_version_id,
                created_at=version.created_at,
                updated_at=version.updated_at,
            )

        last_activity = max(
            [value for value in [record.updated_at, *(item.updated_at for item in versions)] if value],
            default=None,
        )
        active_assignment_count = self.active_assignment_counts.get(record.tier_id, 0)
        return replace(
            record,
            active_version_id=active_versions[0].tier_version_id if active_versions else None,
            active_version=summary(active_versions[0]) if active_versions else None,
            latest_draft_version=summary(draft_versions[0]) if draft_versions else None,
            draft_count=len(draft_versions),
            version_count=len(versions),
            assignment_count=active_assignment_count,
            live_assignment_count=active_assignment_count,
            organization_count=active_assignment_count,
            last_activity_at=last_activity,
        )


class _SnapshotTierPolicyService:
    mode = "enforce"
    snapshot_stale = False
    last_reload_failed = False
    last_reload_error_at = None

    def __init__(self, snapshot: TierPolicySnapshot) -> None:
        self.snapshot = snapshot

    def get_snapshot(self) -> TierPolicySnapshot:
        return self.snapshot

    def snapshot_info(self) -> object:
        return SimpleNamespace(
            etag=self.snapshot.etag,
            generated_at=self.snapshot.generated_at,
            org_count=self.snapshot.org_count,
            assignment_count=self.snapshot.assignment_count,
            model_policy_count=self.snapshot.model_policy_count,
            capacity_pool_count=self.snapshot.capacity_pool_count,
            next_transition_at=self.snapshot.next_transition_at,
            mode=self.mode,
            snapshot_stale=self.snapshot_stale,
            last_reload_failed=self.last_reload_failed,
            last_reload_error_at=self.last_reload_error_at,
        )


def _capacity_dashboard_snapshot(
    pool: CompiledTierCapacityPoolPolicy,
    *,
    organization_ids: tuple[str, ...] = ("org-1",),
) -> TierPolicySnapshot:
    pool_ref = (pool.pool_key, pool.callable_key)
    return replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType({pool_ref: pool}),
        capacity_pool_members=MappingProxyType(
            {
                pool_ref: tuple(
                    CompiledTierCapacityPoolMember(
                        pool_key=pool.pool_key,
                        callable_key=pool.callable_key,
                        organization_id=organization_id,
                        tier_key="growth",
                        assignment_weight=1,
                    )
                    for organization_id in organization_ids
                )
            }
        ),
        capacity_pool_count=1,
    )


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    setattr(test_app.state.settings, "master_key", "mk-test")
    return {"Authorization": "Bearer mk-test"}


def _set_auth_context(monkeypatch: pytest.MonkeyPatch, context: PlatformAuthContext | None) -> None:
    monkeypatch.setattr(
        "src.middleware.platform_auth.get_platform_auth_context", lambda request: context
    )
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)


def _make_context(
    *, platform_role: str = "platform_user", org_role: str | None = None
) -> PlatformAuthContext:
    org_memberships = [{"organization_id": "org-1", "role": org_role}] if org_role else []
    return PlatformAuthContext(
        account_id="acct-1",
        email="user@example.com",
        role=platform_role,
        organization_memberships=org_memberships,
    )


def _audit_response_payloads(audit: _RecordingAuditService) -> list[dict[str, Any]]:
    return [
        payload.content_json
        for _, payloads in audit.sync_calls
        for payload in payloads
        if payload.kind == "response" and payload.content_json is not None
    ]


@pytest.mark.asyncio
async def test_tier_admin_create_list_detail_update_and_audit(client, test_app):
    repository = _FakeTierRepository()
    audit = _RecordingAuditService()
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit
    headers = _headers(test_app)

    create = await client.post(
        "/ui/api/tiers",
        headers=headers,
        json={
            "tier_key": "Growth",
            "name": "Growth",
            "description": "Scaled access",
            "metadata": {"segment": "growth"},
        },
    )
    assert create.status_code == 200
    created = create.json()
    assert created["tier_key"] == "growth"
    assert created["metadata"] == {"segment": "growth"}

    update = await client.patch(
        f"/ui/api/tiers/{created['tier_id']}",
        headers=headers,
        json={"name": "Growth Plus", "enabled": False},
    )
    assert update.status_code == 200
    assert update.json()["name"] == "Growth Plus"
    assert update.json()["enabled"] is False

    listing = await client.get("/ui/api/tiers?search=growth", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["pagination"]["total"] == 1
    assert listing.json()["data"][0]["name"] == "Growth Plus"

    detail = await client.get(f"/ui/api/tiers/{created['tier_id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["tier"]["tier_key"] == "growth"
    assert detail.json()["versions"] == []

    actions = [event.action for event, _ in audit.sync_calls]
    assert AuditAction.ADMIN_TIER_CREATE.value in actions
    assert AuditAction.ADMIN_TIER_UPDATE.value in actions


@pytest.mark.asyncio
async def test_tier_catalog_returns_live_draft_package_and_organization_summaries(
    client,
    test_app,
) -> None:
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version(
        tier_version_id="version-live",
        version_number=2,
        status="active",
    )
    draft = repository.seed_version(
        tier_version_id="version-draft",
        version_number=3,
        status="draft",
    )
    repository.versions[draft.tier_version_id] = replace(
        draft,
        created_by_kind="account",
        created_by_account_id="account-1",
        created_by_email="admin@example.com",
        source_tier_version_id="version-live",
    )
    repository.model_policies[draft.tier_version_id] = [
        TierModelPolicyRecord(
            tier_model_policy_id="policy-1",
            tier_version_id=draft.tier_version_id,
            callable_key="gpt-4o-mini",
        )
    ]
    repository.capacity_pools[draft.tier_version_id] = [
        TierCapacityPoolRecord(
            tier_capacity_pool_id="pool-1",
            tier_version_id=draft.tier_version_id,
            pool_key="shared",
            callable_key="gpt-4o-mini",
        )
    ]
    repository.active_assignment_counts["tier-1"] = 4
    test_app.state.tier_repository = repository

    response = await client.get("/ui/api/tiers?limit=10", headers=_headers(test_app))

    assert response.status_code == 200
    tier = response.json()["data"][0]
    assert tier["active_version"]["version_number"] == 2
    assert tier["latest_draft_version"]["version_number"] == 3
    assert tier["latest_draft_version"]["model_policy_count"] == 1
    assert tier["latest_draft_version"]["capacity_pool_count"] == 1
    assert tier["latest_draft_version"]["created_by_email"] == "admin@example.com"
    assert tier["latest_draft_version"]["source_tier_version_id"] == "version-live"
    assert tier["draft_count"] == 1
    assert tier["organization_count"] == 4
    assert tier["live_assignment_count"] == 4
    assert tier["last_activity_at"] is not None


@pytest.mark.asyncio
async def test_tier_admin_bootstrap_creates_draft_and_replays_without_duplicate_audit(
    client,
    test_app,
):
    repository = _FakeTierRepository()
    audit = _RecordingAuditService()
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit
    headers = {**_headers(test_app), "Idempotency-Key": "bootstrap-request-1"}
    payload = {
        "tier_key": "enterprise",
        "name": "Enterprise",
        "description": "Production package",
        "enabled": True,
    }

    created_response = await client.post(
        "/ui/api/tiers/bootstrap", headers=headers, json=payload
    )

    assert created_response.status_code == 200
    created = created_response.json()
    assert created["idempotency_resolution"] == "created"
    assert created["tier"]["version_count"] == 1
    assert created["initial_version"]["version_number"] == 1
    assert created["initial_version"]["status"] == "draft"
    assert created["initial_version"]["created_by_kind"] == "master_key"
    assert len(audit.sync_calls) == 2

    replay_response = await client.post(
        "/ui/api/tiers/bootstrap", headers=headers, json=payload
    )

    assert replay_response.status_code == 200
    replay = replay_response.json()
    assert replay["idempotency_resolution"] == "replayed"
    assert replay["tier"]["tier_id"] == created["tier"]["tier_id"]
    assert (
        replay["initial_version"]["tier_version_id"]
        == created["initial_version"]["tier_version_id"]
    )
    assert len(audit.sync_calls) == 2

    mismatch_response = await client.post(
        "/ui/api/tiers/bootstrap",
        headers=headers,
        json={**payload, "name": "Changed input"},
    )
    assert mismatch_response.status_code == 409
    assert mismatch_response.json()["detail"]["code"] == (
        "tier_bootstrap_idempotency_conflict"
    )


@pytest.mark.asyncio
async def test_tier_admin_bootstrap_requires_idempotency_key(client, test_app):
    test_app.state.tier_repository = _FakeTierRepository()

    response = await client.post(
        "/ui/api/tiers/bootstrap",
        headers=_headers(test_app),
        json={"tier_key": "enterprise", "name": "Enterprise"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Idempotency-Key header is required"


@pytest.mark.asyncio
async def test_tier_admin_row_mutations_use_revision_contract_and_structured_conflicts(
    client,
    test_app,
):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version()
    audit = _RecordingAuditService()
    governance_invalidation = _RecordingGovernanceInvalidationService()
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit
    test_app.state.governance_invalidation_service = governance_invalidation
    headers = _headers(test_app)
    base_path = "/ui/api/tiers/tier-1/versions/version-1"

    pool_response = await client.post(
        f"{base_path}/capacity-pools",
        headers=headers,
        json={
            "expected_revision": 0,
            "pool_key": "shared",
            "callable_key": "gpt-4o-mini",
            "rpm_capacity": 1000,
            "strategy": "hard_cap",
        },
    )
    assert pool_response.status_code == 200
    pool = pool_response.json()
    assert pool["configuration_revision"] == 1
    pool_id = pool["data"]["tier_capacity_pool_id"]

    policy_response = await client.post(
        f"{base_path}/model-policies",
        headers=headers,
        json={
            "expected_revision": 1,
            "callable_key": "gpt-4o-mini",
            "enabled": True,
            "access_mode": "allow",
            "rpm_limit": 100,
            "capacity_pool_key": "shared",
            "priority": 10,
        },
    )
    assert policy_response.status_code == 200
    policy = policy_response.json()
    assert policy["configuration_revision"] == 2
    policy_id = policy["data"]["tier_model_policy_id"]

    update_response = await client.patch(
        f"{base_path}/model-policies/{policy_id}",
        headers=headers,
        json={"expected_revision": 2, "rpm_limit": 250},
    )
    assert update_response.status_code == 200
    assert update_response.json()["configuration_revision"] == 3
    assert update_response.json()["data"]["rpm_limit"] == 250

    stale_response = await client.patch(
        f"{base_path}/model-policies/{policy_id}",
        headers=headers,
        json={"expected_revision": 2, "rpm_limit": 300},
    )
    assert stale_response.status_code == 409
    stale = stale_response.json()["detail"]
    assert stale["code"] == "tier_configuration_stale"
    assert stale["expected_revision"] == 2
    assert stale["current_revision"] == 3

    pool_in_use_response = await client.request(
        "DELETE",
        f"{base_path}/capacity-pools/{pool_id}",
        headers=headers,
        json={"expected_revision": 3},
    )
    assert pool_in_use_response.status_code == 409
    assert pool_in_use_response.json()["detail"]["code"] == "tier_pool_in_use"
    assert repository.versions["version-1"].configuration_revision == 3

    policy_delete_response = await client.request(
        "DELETE",
        f"{base_path}/model-policies/{policy_id}",
        headers=headers,
        json={"expected_revision": 3},
    )
    assert policy_delete_response.status_code == 200
    assert policy_delete_response.json()["configuration_revision"] == 4

    pool_delete_response = await client.request(
        "DELETE",
        f"{base_path}/capacity-pools/{pool_id}",
        headers=headers,
        json={"expected_revision": 4},
    )
    assert pool_delete_response.status_code == 200
    assert pool_delete_response.json()["configuration_revision"] == 5

    wrong_tier_response = await client.patch(
        f"/ui/api/tiers/tier-other/versions/version-1/model-policies/{policy_id}",
        headers=headers,
        json={"expected_revision": 5, "rpm_limit": 400},
    )
    assert wrong_tier_response.status_code == 404
    assert repository.versions["version-1"].configuration_revision == 5

    actions = [event.action for event, _ in audit.sync_calls]
    assert AuditAction.ADMIN_TIER_CAPACITY_POOL_CREATE.value in actions
    assert AuditAction.ADMIN_TIER_MODEL_POLICY_CREATE.value in actions
    assert AuditAction.ADMIN_TIER_MODEL_POLICY_UPDATE.value in actions
    assert AuditAction.ADMIN_TIER_MODEL_POLICY_DELETE.value in actions
    assert AuditAction.ADMIN_TIER_CAPACITY_POOL_DELETE.value in actions
    assert len(governance_invalidation.local_targets) == 5


@pytest.mark.asyncio
async def test_tier_admin_bulk_limits_updates_filtered_rows_once(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version()
    repository.model_policies["version-1"] = [
        TierModelPolicyRecord(
            tier_model_policy_id="policy-1",
            tier_version_id="version-1",
            callable_key="gpt-one",
            enabled=True,
            access_mode="allow",
            rpm_limit=100,
            priority=1,
        ),
        TierModelPolicyRecord(
            tier_model_policy_id="policy-2",
            tier_version_id="version-1",
            callable_key="other",
            enabled=True,
            access_mode="allow",
            rpm_limit=100,
            priority=2,
        ),
    ]
    audit = _RecordingAuditService()
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit

    response = await client.post(
        "/ui/api/tiers/tier-1/versions/version-1/model-policies/bulk-limits",
        headers=_headers(test_app),
        json={
            "expected_revision": 0,
            "rpm_limit": 250,
            "all_filtered": True,
            "search": "gpt",
        },
    )

    assert response.status_code == 200
    assert response.json()["affected_count"] == 1
    assert response.json()["configuration_revision"] == 1
    assert repository.model_policies["version-1"][0].rpm_limit == 250
    assert repository.model_policies["version-1"][1].rpm_limit == 100
    assert AuditAction.ADMIN_TIER_MODEL_POLICY_BULK_LIMITS.value in [
        event.action for event, _ in audit.sync_calls
    ]


@pytest.mark.asyncio
async def test_tier_admin_configuration_and_archive_reads_are_paginated(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    version = repository.seed_version()
    repository.versions[version.tier_version_id] = replace(
        version,
        configuration_revision=8,
    )
    repository.model_policies[version.tier_version_id] = [
        TierModelPolicyRecord(
            tier_model_policy_id=f"policy-{index:02d}",
            tier_version_id=version.tier_version_id,
            callable_key=f"model-{index:02d}",
            priority=index,
        )
        for index in range(15)
    ]
    repository.capacity_pools[version.tier_version_id] = [
        TierCapacityPoolRecord(
            tier_capacity_pool_id=f"pool-{index:02d}",
            tier_version_id=version.tier_version_id,
            pool_key=f"pool-{index:02d}",
            callable_key=f"model-{index:02d}",
        )
        for index in range(12)
    ]
    for index in range(12):
        repository.seed_version(
            tier_version_id=f"version-archived-{index:02d}",
            version_number=index + 2,
            status="archived",
        )
    test_app.state.tier_repository = repository
    headers = _headers(test_app)
    base_path = "/ui/api/tiers/tier-1/versions/version-1"

    policies_response = await client.get(
        f"{base_path}/model-policies?limit=10&offset=10",
        headers=headers,
    )
    assert policies_response.status_code == 200
    policies = policies_response.json()
    assert policies["pagination"] == {
        "total": 15,
        "limit": 10,
        "offset": 10,
        "has_more": False,
    }
    assert len(policies["data"]) == 5
    assert policies["configuration_revision"] == 8

    pools_response = await client.get(
        f"{base_path}/capacity-pools?limit=10&offset=10",
        headers=headers,
    )
    assert pools_response.status_code == 200
    pools = pools_response.json()
    assert pools["pagination"]["total"] == 12
    assert len(pools["data"]) == 2
    assert pools["configuration_revision"] == 8

    compatible_pools_response = await client.get(
        f"{base_path}/capacity-pools?callable_key=model-11&limit=10",
        headers=headers,
    )
    assert compatible_pools_response.status_code == 200
    compatible_pools = compatible_pools_response.json()
    assert compatible_pools["pagination"]["total"] == 1
    assert [pool["pool_key"] for pool in compatible_pools["data"]] == ["pool-11"]

    archived_response = await client.get(
        "/ui/api/tiers/tier-1/versions?status=archived&limit=10&offset=10",
        headers=headers,
    )
    assert archived_response.status_code == 200
    archived = archived_response.json()
    assert archived["pagination"]["total"] == 12
    assert len(archived["data"]) == 2
    assert all(record["status"] == "archived" for record in archived["data"])


@pytest.mark.asyncio
async def test_tier_admin_activation_preview_and_guarded_activation(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    active = repository.seed_version(
        tier_version_id="version-live",
        version_number=1,
        status="active",
    )
    draft = repository.seed_version(
        tier_version_id="version-draft",
        version_number=2,
        status="draft",
    )
    repository.versions[draft.tier_version_id] = replace(
        draft,
        configuration_revision=2,
    )
    repository.active_assignment_counts["tier-1"] = 3
    repository.model_policies[active.tier_version_id] = [
        TierModelPolicyRecord(
            tier_model_policy_id="policy-live",
            tier_version_id=active.tier_version_id,
            callable_key="model-a",
            rpm_limit=100,
        )
    ]
    repository.model_policies[draft.tier_version_id] = [
        TierModelPolicyRecord(
            tier_model_policy_id="policy-draft-a",
            tier_version_id=draft.tier_version_id,
            callable_key="model-a",
            rpm_limit=200,
        ),
        TierModelPolicyRecord(
            tier_model_policy_id="policy-draft-b",
            tier_version_id=draft.tier_version_id,
            callable_key="model-b",
        ),
    ]
    audit = _RecordingAuditService()
    governance_invalidation = _RecordingGovernanceInvalidationService()
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit
    test_app.state.governance_invalidation_service = governance_invalidation
    headers = _headers(test_app)
    base_path = "/ui/api/tiers/tier-1/versions/version-draft"

    preview_response = await client.get(
        f"{base_path}/activation-preview",
        headers=headers,
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["draft_configuration_revision"] == 2
    assert preview["expected_active_version_id"] == "version-live"
    assert preview["affected_assignment_count"] == 3
    assert preview["affected_organization_count"] == 3
    assert preview["changes"]["policy_added"]["count"] == 1
    assert preview["changes"]["policy_changed"]["count"] == 1
    assert preview["can_activate"] is True

    stale_response = await client.post(
        f"{base_path}/activate",
        headers=headers,
        json={
            "expected_revision": 1,
            "expected_active_version_id": "version-live",
        },
    )
    assert stale_response.status_code == 409
    assert stale_response.json()["detail"]["code"] == "tier_configuration_stale"

    active_changed_response = await client.post(
        f"{base_path}/activate",
        headers=headers,
        json={
            "expected_revision": 2,
            "expected_active_version_id": "version-other",
        },
    )
    assert active_changed_response.status_code == 409
    assert active_changed_response.json()["detail"]["code"] == (
        "tier_activation_active_changed"
    )

    activate_response = await client.post(
        f"{base_path}/activate",
        headers=headers,
        json={
            "expected_revision": 2,
            "expected_active_version_id": "version-live",
        },
    )
    assert activate_response.status_code == 200
    assert activate_response.json()["status"] == "active"
    assert repository.versions["version-live"].status == "archived"
    assert repository.versions["version-draft"].status == "active"
    assert audit.sync_calls[-1][0].action == AuditAction.ADMIN_TIER_VERSION_ACTIVATE.value
    assert len(governance_invalidation.local_targets) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("org_role", [OrganizationRole.OWNER, OrganizationRole.ADMIN])
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/ui/api/tiers", None),
        ("POST", "/ui/api/tiers", {"tier_key": "blocked", "name": "Blocked"}),
        ("POST", "/ui/api/tiers/bootstrap", {"tier_key": "blocked", "name": "Blocked"}),
        ("GET", "/ui/api/tiers/tier-1", None),
        ("PATCH", "/ui/api/tiers/tier-1", {"name": "Blocked"}),
        ("DELETE", "/ui/api/tiers/tier-1", None),
        ("POST", "/ui/api/tiers/tier-1/versions", {}),
        ("GET", "/ui/api/tiers/tier-1/versions?status=draft&limit=10", None),
        ("GET", "/ui/api/tiers/tier-1/versions/version-1", None),
        ("POST", "/ui/api/tiers/tier-1/versions/version-1/clone", None),
        ("GET", "/ui/api/tiers/tier-1/versions/version-1/model-policies?limit=10", None),
        (
            "POST",
            "/ui/api/tiers/tier-1/versions/version-1/model-policies",
            {"expected_revision": 0, "callable_key": "gpt-4o-mini"},
        ),
        (
            "PATCH",
            "/ui/api/tiers/tier-1/versions/version-1/model-policies/policy-1",
            {"expected_revision": 0, "rpm_limit": 100},
        ),
        (
            "DELETE",
            "/ui/api/tiers/tier-1/versions/version-1/model-policies/policy-1",
            {"expected_revision": 0},
        ),
        (
            "POST",
            "/ui/api/tiers/tier-1/versions/version-1/model-policies/bulk-limits",
            {"expected_revision": 0, "rpm_limit": 100, "all_filtered": True},
        ),
        ("GET", "/ui/api/tiers/tier-1/versions/version-1/capacity-pools?limit=10", None),
        (
            "POST",
            "/ui/api/tiers/tier-1/versions/version-1/capacity-pools",
            {
                "expected_revision": 0,
                "pool_key": "shared",
                "callable_key": "gpt-4o-mini",
            },
        ),
        (
            "PATCH",
            "/ui/api/tiers/tier-1/versions/version-1/capacity-pools/pool-1",
            {"expected_revision": 0, "rpm_capacity": 1000},
        ),
        (
            "DELETE",
            "/ui/api/tiers/tier-1/versions/version-1/capacity-pools/pool-1",
            {"expected_revision": 0},
        ),
        ("GET", "/ui/api/tiers/tier-1/versions/version-1/activation-preview", None),
        (
            "POST",
            "/ui/api/tiers/tier-1/versions/version-1/activate",
            {"expected_revision": 0, "expected_active_version_id": None},
        ),
        ("POST", "/ui/api/tiers/tier-1/versions/version-1/archive", None),
        ("GET", "/ui/api/tier-capacity/dashboard", None),
        (
            "POST",
            "/ui/api/tier-capacity/boosts",
            {
                "organization_id": "org-1",
                "pool_key": "shared",
                "callable_key": "gpt-4o-mini",
            },
        ),
        (
            "DELETE",
            "/ui/api/tier-capacity/boosts?organization_id=org-1&pool_key=shared&callable_key=gpt-4o-mini",
            None,
        ),
        ("GET", "/ui/api/organizations/org-1/tier-policy-preview", None),
        (
            "POST",
            "/ui/api/organizations/org-1/tier-policy/simulate",
            {"callable_key": "gpt-4o-mini"},
        ),
    ],
)
async def test_tier_admin_routes_require_platform_admin(
    client,
    test_app,
    monkeypatch,
    org_role,
    method,
    path,
    payload,
):
    test_app.state.tier_repository = _FakeTierRepository()
    _set_auth_context(monkeypatch, _make_context(org_role=org_role))

    request_kwargs = {"json": payload} if payload is not None else {}
    response = await client.request(method, path, **request_kwargs)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_tier_admin_allows_platform_admin_session(client, test_app, monkeypatch):
    repository = _FakeTierRepository()
    repository.seed_tier()
    test_app.state.tier_repository = repository
    _set_auth_context(monkeypatch, _make_context(platform_role=PlatformRole.ADMIN))

    response = await client.get("/ui/api/tiers")

    assert response.status_code == 200
    assert response.json()["data"][0]["tier_key"] == "growth"


@pytest.mark.asyncio
async def test_tier_admin_duplicate_key_returns_conflict(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier(tier_key="growth")
    test_app.state.tier_repository = repository

    response = await client.post(
        "/ui/api/tiers",
        headers=_headers(test_app),
        json={"tier_key": "growth", "name": "Duplicate"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "A tier with this tier_key already exists"


@pytest.mark.asyncio
async def test_tier_admin_version_policy_pool_activation_flow(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    audit = _RecordingAuditService()
    governance_invalidation = _RecordingGovernanceInvalidationService()
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit
    test_app.state.governance_invalidation_service = governance_invalidation
    headers = _headers(test_app)

    version_response = await client.post("/ui/api/tiers/tier-1/versions", headers=headers, json={})
    assert version_response.status_code == 200
    version = version_response.json()
    assert version["version_number"] == 1
    assert version["configuration_revision"] == 0
    assert version["created_by_account_id"] is None
    assert version["created_by_kind"] == "master_key"
    assert version["source_tier_version_id"] is None
    version_id = version["tier_version_id"]

    pools_response = await client.post(
        f"/ui/api/tiers/tier-1/versions/{version_id}/capacity-pools",
        headers=headers,
        json={
            "expected_revision": 0,
            "pool_key": "shared",
            "callable_key": "gpt-4o-mini",
            "rpm_capacity": 1000,
            "tpm_capacity": 500000,
            "strategy": "weighted_fair",
            "saturation_threshold": 0.8,
            "burst_multiplier": 1.5,
        },
    )
    assert pools_response.status_code == 200
    assert pools_response.json()["data"]["pool_key"] == "shared"

    policies_response = await client.post(
        f"/ui/api/tiers/tier-1/versions/{version_id}/model-policies",
        headers=headers,
        json={
            "expected_revision": 1,
            "callable_key": "gpt-4o-mini",
            "rpm_limit": 100,
            "tpm_limit": 10000,
            "pricing": {"input_cost_per_token": 0.000001},
            "capacity_pool_key": "shared",
        },
    )
    assert policies_response.status_code == 200
    assert policies_response.json()["data"]["pricing"] == {"input_cost_per_token": 0.000001}

    activate_response = await client.post(
        f"/ui/api/tiers/tier-1/versions/{version_id}/activate",
        headers=headers,
        json={"expected_revision": 2, "expected_active_version_id": None},
    )
    assert activate_response.status_code == 200
    assert activate_response.json()["status"] == "active"

    detail = await client.get(f"/ui/api/tiers/tier-1/versions/{version_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["model_policies"][0]["capacity_pool_key"] == "shared"
    assert detail.json()["capacity_pools"][0]["strategy"] == "weighted_fair"

    actions = [event.action for event, _ in audit.sync_calls]
    assert AuditAction.ADMIN_TIER_VERSION_CREATE.value in actions
    assert AuditAction.ADMIN_TIER_CAPACITY_POOL_CREATE.value in actions
    assert AuditAction.ADMIN_TIER_MODEL_POLICY_CREATE.value in actions
    assert AuditAction.ADMIN_TIER_VERSION_ACTIVATE.value in actions
    assert governance_invalidation.local_targets == [
        ("tier_policy",),
        ("tier_policy",),
        ("tier_policy",),
    ]
    assert governance_invalidation.notified_targets == [
        ("tier_policy",),
        ("tier_policy",),
        ("tier_policy",),
    ]
    tier_policy_payloads = [
        payload["tier_policy_invalidation"]
        for payload in _audit_response_payloads(audit)
        if "tier_policy_invalidation" in payload
    ]
    assert tier_policy_payloads == [
        {
            "attempted": True,
            "reloaded": True,
            "notified": True,
            "reason": "reloaded_and_notified",
        },
        {
            "attempted": True,
            "reloaded": True,
            "notified": True,
            "reason": "reloaded_and_notified",
        },
        {
            "attempted": True,
            "reloaded": True,
            "notified": True,
            "reason": "reloaded_and_notified",
        },
    ]


@pytest.mark.asyncio
async def test_tier_admin_clone_version_copies_policy_pool_and_metadata(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version(
        tier_version_id="version-active",
        version_number=3,
        status="active",
    )
    repository.versions["version-active"] = replace(
        repository.versions["version-active"],
        metadata={"release": "stable"},
    )
    repository.capacity_pools["version-active"] = [
        TierCapacityPoolRecord(
            tier_capacity_pool_id="pool-1",
            tier_version_id="version-active",
            pool_key="shared",
            callable_key="gpt-4o-mini",
            rpm_capacity=1000,
            metadata={"pool": "gold"},
        )
    ]
    repository.model_policies["version-active"] = [
        TierModelPolicyRecord(
            tier_model_policy_id="policy-1",
            tier_version_id="version-active",
            callable_key="gpt-4o-mini",
            rpm_limit=100,
            pricing={"input_cost_per_token": 0.000001},
            capacity_pool_key="shared",
            metadata={"policy": "gold"},
        )
    ]
    audit = _RecordingAuditService()
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit

    response = await client.post(
        "/ui/api/tiers/tier-1/versions/version-active/clone",
        headers=_headers(test_app),
    )

    assert response.status_code == 200
    cloned = response.json()
    assert cloned["status"] == "draft"
    assert cloned["version_number"] == 4
    assert cloned["metadata"] == {"release": "stable"}
    assert cloned["model_policy_count"] == 1
    assert cloned["capacity_pool_count"] == 1
    cloned_version_id = cloned["tier_version_id"]
    assert repository.capacity_pools[cloned_version_id][0].metadata == {"pool": "gold"}
    assert repository.model_policies[cloned_version_id][0].metadata == {"policy": "gold"}
    event, _ = audit.sync_calls[-1]
    assert event.action == AuditAction.ADMIN_TIER_VERSION_CLONE.value
    assert event.resource_id == cloned_version_id


@pytest.mark.asyncio
async def test_tier_admin_update_audits_non_fatal_tier_policy_reload_failure(
    client,
    test_app,
) -> None:
    repository = _FakeTierRepository()
    repository.seed_tier()
    audit = _RecordingAuditService()
    governance_invalidation = _RecordingGovernanceInvalidationService(fail_local=True)
    test_app.state.tier_repository = repository
    test_app.state.audit_service = audit
    test_app.state.governance_invalidation_service = governance_invalidation

    response = await client.patch(
        "/ui/api/tiers/tier-1",
        headers=_headers(test_app),
        json={"name": "Growth Plus"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Growth Plus"
    assert governance_invalidation.local_targets == [("tier_policy",)]
    assert governance_invalidation.notified_targets == [("tier_policy",)]
    event, _ = audit.sync_calls[-1]
    invalidation = event.metadata["tier_policy_invalidation"]
    assert invalidation["attempted"] is True
    assert invalidation["reloaded"] is False
    assert invalidation["notified"] is True
    assert invalidation["reason"] == "local_reload_failed_remote_notified"
    assert "tier policy reload unavailable" in invalidation["error"]
    response_payload = _audit_response_payloads(audit)[-1]
    assert response_payload["tier_policy_invalidation"] == invalidation


@pytest.mark.asyncio
async def test_legacy_tier_mutation_routes_are_removed(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version()
    test_app.state.tier_repository = repository
    headers = _headers(test_app)
    base_path = "/ui/api/tiers/tier-1/versions/version-1"

    policies_response = await client.put(
        f"{base_path}/model-policies",
        headers=headers,
        json={"policies": []},
    )
    pools_response = await client.put(
        f"{base_path}/capacity-pools",
        headers=headers,
        json={"pools": []},
    )
    publish_response = await client.post(f"{base_path}/publish", headers=headers)

    openapi_paths = test_app.openapi()["paths"]
    policies_path = "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/model-policies"
    pools_path = "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/capacity-pools"
    publish_path = "/ui/api/tiers/{tier_id}/versions/{tier_version_id}/publish"
    assert "put" not in openapi_paths[policies_path]
    assert "put" not in openapi_paths[pools_path]
    assert publish_path not in openapi_paths
    assert policies_response.status_code == 405
    assert pools_response.status_code == 405
    assert publish_response.status_code == 405


@pytest.mark.asyncio
async def test_tier_admin_delete_rejects_active_assignment(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.active_assignment_counts["tier-1"] = 1
    test_app.state.tier_repository = repository

    response = await client.delete("/ui/api/tiers/tier-1", headers=_headers(test_app))

    assert response.status_code == 409
    assert response.json()["detail"] == "Tier has active organization assignments"
    assert "tier-1" in repository.tiers


@pytest.mark.asyncio
async def test_tier_admin_disable_rejects_live_or_scheduled_assignment(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.active_assignment_counts["tier-1"] = 1
    test_app.state.tier_repository = repository

    response = await client.patch(
        "/ui/api/tiers/tier-1",
        headers=_headers(test_app),
        json={"enabled": False},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Tier has enabled live or scheduled organization assignments"
    )
    assert repository.tiers["tier-1"].enabled is True


@pytest.mark.asyncio
async def test_tier_admin_disable_maps_database_race_to_conflict(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.update_error = (
        "cannot disable tier while enabled organization assignments exist"
    )
    test_app.state.tier_repository = repository

    response = await client.patch(
        "/ui/api/tiers/tier-1",
        headers=_headers(test_app),
        json={"enabled": False},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Tier has enabled live or scheduled organization assignments"
    )


@pytest.mark.asyncio
async def test_tier_capacity_dashboard_endpoint_returns_snapshot_pools(client, test_app):
    pool = CompiledTierCapacityPoolPolicy(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        rpm_capacity=100,
        tpm_capacity=10_000,
        max_parallel_requests=20,
        strategy="weighted_fair",
        saturation_threshold=0.8,
        burst_multiplier=None,
        source_tier_version_ids=("version-1",),
        source_pool_ids=("pool-1",),
    )
    snapshot = replace(
        empty_tier_policy_snapshot(),
        capacity_pool_policy=MappingProxyType({("shared", "gpt-4o-mini"): pool}),
        capacity_pool_count=1,
    )
    test_app.state.tier_policy_service = _SnapshotTierPolicyService(snapshot)
    test_app.state.redis = FakeRedis()

    response = await client.get(
        "/ui/api/tier-capacity/dashboard",
        headers=_headers(test_app),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot"]["etag"] == "empty"
    assert payload["pools"][0]["pool_key"] == "shared"
    assert payload["pools"][0]["callable_key"] == "gpt-4o-mini"
    assert payload["pools"][0]["advanced_fair_share"] is True
    assert payload["advanced_pool_count"] == 1
    assert payload["saturated_pool_count"] == 0
    assert payload["limit_hit_count"] == 0
    assert payload["pool_scan_truncated"] is False
    assert payload["live_data"] == {
        "status": "healthy",
        "redis_available": True,
        "failed_sections": [],
    }


@pytest.mark.asyncio
async def test_tier_capacity_dashboard_marks_live_values_unavailable_without_redis(
    client,
    test_app,
):
    pool = CompiledTierCapacityPoolPolicy(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        rpm_capacity=100,
        tpm_capacity=10_000,
        max_parallel_requests=20,
        strategy="weighted_fair",
        saturation_threshold=0.8,
        burst_multiplier=None,
        source_tier_version_ids=("version-1",),
        source_pool_ids=("pool-1",),
    )
    test_app.state.tier_policy_service = _SnapshotTierPolicyService(
        _capacity_dashboard_snapshot(pool)
    )
    test_app.state.redis = None

    response = await client.get(
        "/ui/api/tier-capacity/dashboard",
        headers=_headers(test_app),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["live_data"]["status"] == "unavailable"
    assert payload["live_data"]["redis_available"] is False
    assert payload["live_data"]["failed_sections"] == [
        "active_boosts",
        "cleanup_lag",
        "limit_hit_heatmap",
        "limit_hit_total",
        "pool_usage",
        "top_orgs",
    ]
    assert payload["saturated_pool_count"] is None
    assert payload["limit_hit_count"] is None
    assert payload["pools"][0]["rpm_used"] is None
    assert payload["pools"][0]["tpm_used"] is None
    assert payload["pools"][0]["active_org_count"] is None
    assert payload["pools"][0]["active_boost_count"] is None
    assert payload["pools"][0]["cleanup_lagged"] is None


@pytest.mark.asyncio
async def test_tier_capacity_boost_endpoint_writes_deletes_and_audits(client, test_app):
    redis = FakeRedis()
    audit = _RecordingAuditService()
    pool = CompiledTierCapacityPoolPolicy(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        rpm_capacity=100,
        tpm_capacity=10_000,
        max_parallel_requests=20,
        strategy="weighted_fair",
        saturation_threshold=0.8,
        burst_multiplier=None,
        source_tier_version_ids=("version-1",),
        source_pool_ids=("pool-1",),
    )
    test_app.state.redis = redis
    test_app.state.audit_service = audit
    test_app.state.tier_policy_service = _SnapshotTierPolicyService(_capacity_dashboard_snapshot(pool))

    response = await client.post(
        "/ui/api/tier-capacity/boosts",
        headers=_headers(test_app),
        json={
            "organization_id": "org-1",
            "pool_key": "shared",
            "callable_key": "gpt-4o-mini",
            "weight_multiplier": 2.0,
            "ttl_seconds": 60,
            "reason": "temporary launch capacity",
        },
    )

    assert response.status_code == 200
    assert response.json()["weight_multiplier"] == 2.0
    boost_key = fair_share_boost_key(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-1",
    )
    assert await redis.get(boost_key) == "2.0"

    delete_response = await client.delete(
        "/ui/api/tier-capacity/boosts",
        headers=_headers(test_app),
        params={
            "organization_id": "org-1",
            "pool_key": "shared",
            "callable_key": "gpt-4o-mini",
        },
    )

    assert delete_response.status_code == 200
    assert await redis.get(boost_key) is None
    actions = [event.action for event, _ in audit.sync_calls]
    assert AuditAction.ADMIN_TIER_CAPACITY_BOOST_UPSERT.value in actions
    assert AuditAction.ADMIN_TIER_CAPACITY_BOOST_DELETE.value in actions
    boost_events = [
        event
        for event, _ in audit.sync_calls
        if event.action
        in {
            AuditAction.ADMIN_TIER_CAPACITY_BOOST_UPSERT.value,
            AuditAction.ADMIN_TIER_CAPACITY_BOOST_DELETE.value,
        }
    ]
    assert [event.organization_id for event in boost_events] == ["org-1", "org-1"]


@pytest.mark.asyncio
async def test_tier_capacity_boost_endpoint_rejects_unknown_pool(client, test_app):
    pool = CompiledTierCapacityPoolPolicy(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        rpm_capacity=100,
        tpm_capacity=10_000,
        max_parallel_requests=20,
        strategy="weighted_fair",
        saturation_threshold=0.8,
        burst_multiplier=None,
        source_tier_version_ids=("version-1",),
        source_pool_ids=("pool-1",),
    )
    test_app.state.redis = FakeRedis()
    test_app.state.tier_policy_service = _SnapshotTierPolicyService(_capacity_dashboard_snapshot(pool))

    response = await client.post(
        "/ui/api/tier-capacity/boosts",
        headers=_headers(test_app),
        json={
            "organization_id": "org-1",
            "pool_key": "typo",
            "callable_key": "gpt-4o-mini",
            "weight_multiplier": 2.0,
            "ttl_seconds": 60,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Capacity pool not found in active tier policy snapshot"


@pytest.mark.asyncio
async def test_tier_capacity_boost_delete_allows_stale_non_member_org(client, test_app):
    redis = FakeRedis()
    pool = CompiledTierCapacityPoolPolicy(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        rpm_capacity=100,
        tpm_capacity=10_000,
        max_parallel_requests=20,
        strategy="weighted_fair",
        saturation_threshold=0.8,
        burst_multiplier=None,
        source_tier_version_ids=("version-1",),
        source_pool_ids=("pool-1",),
    )
    boost_key = fair_share_boost_key(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        organization_id="org-stale",
    )
    await redis.set(boost_key, "2.0", ex=60)
    test_app.state.redis = redis
    test_app.state.tier_policy_service = _SnapshotTierPolicyService(
        _capacity_dashboard_snapshot(pool, organization_ids=())
    )

    response = await client.delete(
        "/ui/api/tier-capacity/boosts",
        headers=_headers(test_app),
        params={
            "organization_id": "org-stale",
            "pool_key": "shared",
            "callable_key": "gpt-4o-mini",
        },
    )

    assert response.status_code == 200
    assert await redis.get(boost_key) is None


@pytest.mark.asyncio
async def test_tier_capacity_boost_endpoint_rejects_non_member_org(client, test_app):
    pool = CompiledTierCapacityPoolPolicy(
        pool_key="shared",
        callable_key="gpt-4o-mini",
        rpm_capacity=100,
        tpm_capacity=10_000,
        max_parallel_requests=20,
        strategy="weighted_fair",
        saturation_threshold=0.8,
        burst_multiplier=None,
        source_tier_version_ids=("version-1",),
        source_pool_ids=("pool-1",),
    )
    test_app.state.redis = FakeRedis()
    test_app.state.tier_policy_service = _SnapshotTierPolicyService(_capacity_dashboard_snapshot(pool))

    response = await client.post(
        "/ui/api/tier-capacity/boosts",
        headers=_headers(test_app),
        json={
            "organization_id": "org-missing",
            "pool_key": "shared",
            "callable_key": "gpt-4o-mini",
            "weight_multiplier": 2.0,
            "ttl_seconds": 60,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Organization is not an active member of this capacity pool"
