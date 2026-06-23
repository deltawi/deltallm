from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.audit.actions import AuditAction
from src.auth.roles import OrganizationRole, PlatformRole
from src.db.tiers import (
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierRecord,
    TierVersionRecord,
)
from src.models.platform_auth import PlatformAuthContext


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
        self.publish_error: str | None = None
        self.archive_error: str | None = None

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

    async def list_tier_versions(self, tier_id: str) -> list[TierVersionRecord]:
        records = [record for record in self.versions.values() if record.tier_id == tier_id]
        return sorted(records, key=lambda item: item.version_number, reverse=True)

    async def get_tier_version(self, tier_version_id: str) -> TierVersionRecord | None:
        return self.versions.get(tier_version_id)

    async def create_tier_version(
        self,
        *,
        tier_id: str,
        version_number: int,
        status: str = "draft",
        published_at=None,  # noqa: ANN001
        published_by_account_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TierVersionRecord:
        del published_at, published_by_account_id
        tier_version_id = f"version-{len(self.versions) + 1}"
        record = TierVersionRecord(
            tier_version_id=tier_version_id,
            tier_id=tier_id,
            version_number=version_number,
            status=status,
            metadata=metadata,
            created_at=datetime.now(tz=UTC),
            updated_at=datetime.now(tz=UTC),
        )
        self.versions[tier_version_id] = record
        return record

    async def clone_tier_version(
        self,
        *,
        tier_id: str,
        source_tier_version_id: str,
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

    async def publish_tier_version(
        self,
        tier_version_id: str,
        *,
        published_by_account_id: str | None = None,
    ) -> TierVersionRecord | None:
        if self.publish_error is not None:
            raise ValueError(self.publish_error)
        record = self.versions.get(tier_version_id)
        if record is None:
            return None
        if record.status != "draft":
            raise ValueError("only draft tier versions can be published")
        for version_id, version in list(self.versions.items()):
            if version.tier_id == record.tier_id and version.status == "active":
                self.versions[version_id] = replace(version, status="archived")
        updated = replace(
            record,
            status="active",
            published_at=datetime.now(tz=UTC),
            published_by_account_id=published_by_account_id,
        )
        self.versions[tier_version_id] = updated
        return updated

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

    async def replace_model_policies(
        self,
        tier_version_id: str,
        policies: list[TierModelPolicyRecord],
    ) -> list[TierModelPolicyRecord]:
        records = [
            replace(policy, tier_model_policy_id=f"policy-{index + 1}")
            for index, policy in enumerate(policies)
        ]
        self.model_policies[tier_version_id] = records
        return records

    async def list_capacity_pools(self, tier_version_id: str) -> list[TierCapacityPoolRecord]:
        return list(self.capacity_pools.get(tier_version_id, []))

    async def replace_capacity_pools(
        self,
        tier_version_id: str,
        pools: list[TierCapacityPoolRecord],
    ) -> list[TierCapacityPoolRecord]:
        records = [
            replace(pool, tier_capacity_pool_id=f"pool-{index + 1}")
            for index, pool in enumerate(pools)
        ]
        self.capacity_pools[tier_version_id] = records
        return records

    def _tier_with_counts(self, record: TierRecord) -> TierRecord:
        versions = [
            version for version in self.versions.values() if version.tier_id == record.tier_id
        ]
        active_versions = [version for version in versions if version.status == "active"]
        active_versions.sort(key=lambda item: item.version_number, reverse=True)
        return replace(
            record,
            active_version_id=active_versions[0].tier_version_id if active_versions else None,
            version_count=len(versions),
            assignment_count=self.active_assignment_counts.get(record.tier_id, 0),
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
async def test_tier_admin_requires_platform_admin(client, test_app, monkeypatch):
    test_app.state.tier_repository = _FakeTierRepository()
    _set_auth_context(monkeypatch, _make_context(org_role=OrganizationRole.ADMIN))

    response = await client.get("/ui/api/tiers")

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
async def test_tier_admin_version_policy_pool_publish_flow(client, test_app):
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
    version_id = version["tier_version_id"]

    pools_response = await client.put(
        f"/ui/api/tiers/tier-1/versions/{version_id}/capacity-pools",
        headers=headers,
        json={
            "pools": [
                {
                    "pool_key": "shared",
                    "callable_key": "gpt-4o-mini",
                    "rpm_capacity": 1000,
                    "tpm_capacity": 500000,
                    "strategy": "weighted_fair",
                    "saturation_threshold": 0.8,
                    "burst_multiplier": 1.5,
                }
            ]
        },
    )
    assert pools_response.status_code == 200
    assert pools_response.json()["data"][0]["pool_key"] == "shared"

    policies_response = await client.put(
        f"/ui/api/tiers/tier-1/versions/{version_id}/model-policies",
        headers=headers,
        json={
            "policies": [
                {
                    "callable_key": "gpt-4o-mini",
                    "rpm_limit": 100,
                    "tpm_limit": 10000,
                    "pricing": {"input_cost_per_token": 0.000001},
                    "capacity_pool_key": "shared",
                }
            ]
        },
    )
    assert policies_response.status_code == 200
    assert policies_response.json()["data"][0]["pricing"] == {"input_cost_per_token": 0.000001}

    publish_response = await client.post(
        f"/ui/api/tiers/tier-1/versions/{version_id}/publish",
        headers=headers,
    )
    assert publish_response.status_code == 200
    assert publish_response.json()["status"] == "active"

    detail = await client.get(f"/ui/api/tiers/tier-1/versions/{version_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["model_policies"][0]["capacity_pool_key"] == "shared"
    assert detail.json()["capacity_pools"][0]["strategy"] == "weighted_fair"

    actions = [event.action for event, _ in audit.sync_calls]
    assert AuditAction.ADMIN_TIER_VERSION_CREATE.value in actions
    assert AuditAction.ADMIN_TIER_CAPACITY_POOLS_REPLACE.value in actions
    assert AuditAction.ADMIN_TIER_MODEL_POLICIES_REPLACE.value in actions
    assert AuditAction.ADMIN_TIER_VERSION_PUBLISH.value in actions
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
async def test_tier_admin_replace_requires_explicit_policy_and_pool_lists(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version()
    repository.model_policies["version-1"] = [
        TierModelPolicyRecord(
            tier_model_policy_id="policy-1",
            tier_version_id="version-1",
            callable_key="gpt-4o-mini",
        )
    ]
    repository.capacity_pools["version-1"] = [
        TierCapacityPoolRecord(
            tier_capacity_pool_id="pool-1",
            tier_version_id="version-1",
            pool_key="shared",
            callable_key="gpt-4o-mini",
        )
    ]
    test_app.state.tier_repository = repository
    test_app.state.audit_service = _RecordingAuditService()
    headers = _headers(test_app)

    policies_response = await client.put(
        "/ui/api/tiers/tier-1/versions/version-1/model-policies",
        headers=headers,
        json={},
    )
    pools_response = await client.put(
        "/ui/api/tiers/tier-1/versions/version-1/capacity-pools",
        headers=headers,
        json={},
    )

    assert policies_response.status_code == 422
    assert pools_response.status_code == 422
    assert [policy.callable_key for policy in repository.model_policies["version-1"]] == [
        "gpt-4o-mini"
    ]
    assert [pool.pool_key for pool in repository.capacity_pools["version-1"]] == ["shared"]

    clear_policies_response = await client.put(
        "/ui/api/tiers/tier-1/versions/version-1/model-policies",
        headers=headers,
        json={"policies": []},
    )
    clear_pools_response = await client.put(
        "/ui/api/tiers/tier-1/versions/version-1/capacity-pools",
        headers=headers,
        json={"pools": []},
    )

    assert clear_policies_response.status_code == 200
    assert clear_policies_response.json()["data"] == []
    assert clear_pools_response.status_code == 200
    assert clear_pools_response.json()["data"] == []
    assert repository.model_policies["version-1"] == []
    assert repository.capacity_pools["version-1"] == []


@pytest.mark.asyncio
async def test_tier_admin_model_policy_rejects_missing_capacity_pool(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version()
    test_app.state.tier_repository = repository

    response = await client.put(
        "/ui/api/tiers/tier-1/versions/version-1/model-policies",
        headers=_headers(test_app),
        json={
            "policies": [
                {
                    "callable_key": "gpt-4o-mini",
                    "capacity_pool_key": "shared",
                }
            ]
        },
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "capacity_pool_key must reference a pool for the same callable_key"
    )


@pytest.mark.asyncio
async def test_tier_admin_capacity_pool_replace_preserves_referenced_pools(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version()
    repository.capacity_pools["version-1"] = [
        TierCapacityPoolRecord(
            tier_capacity_pool_id="pool-1",
            tier_version_id="version-1",
            pool_key="shared",
            callable_key="gpt-4o-mini",
        )
    ]
    repository.model_policies["version-1"] = [
        TierModelPolicyRecord(
            tier_model_policy_id="policy-1",
            tier_version_id="version-1",
            callable_key="gpt-4o-mini",
            capacity_pool_key="shared",
        )
    ]
    test_app.state.tier_repository = repository

    response = await client.put(
        "/ui/api/tiers/tier-1/versions/version-1/capacity-pools",
        headers=_headers(test_app),
        json={"pools": []},
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "Cannot remove a capacity pool referenced by draft model policies"
    )


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
async def test_tier_admin_publish_non_draft_returns_conflict(client, test_app):
    repository = _FakeTierRepository()
    repository.seed_tier()
    repository.seed_version(status="active")
    test_app.state.tier_repository = repository

    response = await client.post(
        "/ui/api/tiers/tier-1/versions/version-1/publish",
        headers=_headers(test_app),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "only draft tier versions can be published"
