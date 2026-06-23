from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from src.db.tiers import (
    OrganizationTierAssignmentRecord,
    TierCapacityPoolRecord,
    TierModelPolicyRecord,
    TierPolicyAssignmentRecord,
    TierPolicyLoadResult,
)
from src.services.tier_policy_service import TierPolicyService


class _TierPreviewRepository:
    def __init__(self) -> None:
        now = datetime.now(tz=UTC)
        self.organizations = {"org-1"}
        self.assignments = [
            OrganizationTierAssignmentRecord(
                assignment_id="assignment-1",
                organization_id="org-1",
                tier_id="tier-1",
                tier_version_id="version-1",
                assignment_type="primary",
                enabled=True,
                weight=2,
                tier_key="growth",
                tier_name="Growth",
                tier_version_number=1,
                tier_version_status="active",
                created_at=now,
                updated_at=now,
            )
        ]
        self.policy_inputs = TierPolicyLoadResult(
            assignments=(
                TierPolicyAssignmentRecord(
                    assignment_id="assignment-1",
                    organization_id="org-1",
                    tier_id="tier-1",
                    tier_version_id="version-1",
                    effective_tier_version_id="version-1",
                    assignment_type="primary",
                    enabled=True,
                    weight=2,
                    tier_key="growth",
                    tier_name="Growth",
                    tier_version_number=1,
                    tier_version_status="active",
                    created_at=now,
                    updated_at=now,
                ),
            ),
            model_policies=(
                TierModelPolicyRecord(
                    tier_model_policy_id="policy-1",
                    tier_version_id="version-1",
                    callable_key="gpt-4o-mini",
                    rpm_limit=10,
                    tpm_limit=500,
                    max_parallel_requests=3,
                    pricing={"input_cost_per_token": 0.01},
                    capacity_pool_key="shared-chat",
                    priority=5,
                    created_at=now,
                    updated_at=now,
                ),
            ),
            capacity_pools=(
                TierCapacityPoolRecord(
                    tier_capacity_pool_id="pool-1",
                    tier_version_id="version-1",
                    pool_key="shared-chat",
                    callable_key="gpt-4o-mini",
                    rpm_capacity=100,
                    tpm_capacity=5_000,
                    max_parallel_requests=20,
                    created_at=now,
                    updated_at=now,
                ),
            ),
        )

    async def organization_exists_for_tier_assignment(self, organization_id: str) -> bool:
        return organization_id in self.organizations

    async def list_org_assignments(
        self,
        organization_id: str,
        *,
        enabled: bool | None = None,
    ) -> list[OrganizationTierAssignmentRecord]:
        records = [
            assignment
            for assignment in self.assignments
            if assignment.organization_id == organization_id
        ]
        if enabled is not None:
            records = [assignment for assignment in records if assignment.enabled is enabled]
        return records

    async def load_active_tier_policy_inputs(self, *, reference_time: datetime):
        del reference_time
        return self.policy_inputs


def _headers(test_app) -> dict[str, str]:  # noqa: ANN001
    setattr(test_app.state.settings, "master_key", "mk-test")
    return {"Authorization": "Bearer mk-test"}


async def _install_preview_services(  # noqa: ANN001
    test_app,
    repository: _TierPreviewRepository | None = None,
) -> _TierPreviewRepository:
    repository = repository or _TierPreviewRepository()
    service = TierPolicyService(repository=repository, mode="enforce")
    await service.reload(reason="test")
    test_app.state.tier_repository = repository
    test_app.state.tier_policy_service = service
    return repository


@pytest.mark.asyncio
async def test_org_tier_policy_preview_serializes_effective_snapshot(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)

    response = await client.get(
        "/ui/api/organizations/org-1/tier-policy-preview",
        headers=_headers(test_app),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["organization_id"] == "org-1"
    assert payload["explicit_policy"] is True
    assert payload["tier_keys"] == ["growth"]
    assert payload["allowed_callable_keys"] == ["gpt-4o-mini"]
    assert payload["assignments"][0]["assignment_id"] == "assignment-1"
    assert payload["model_policies"][0]["source"]["tier_key"] == "growth"
    assert payload["model_policies"][0]["limits"]["rpm_limit"] == 10
    assert payload["pricing_policies"][0]["pricing"]["input_cost_per_token"] == 0.01
    assert payload["rate_limits"][0]["scope"] == "tier_org_model_rpm"
    assert payload["capacity_pools"][0]["pool_key"] == "shared-chat"
    assert payload["snapshot"]["mode"] == "enforce"


@pytest.mark.asyncio
async def test_org_tier_policy_preview_filters_capacity_pools_by_model(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    extra_pool = TierCapacityPoolRecord(
        tier_capacity_pool_id="pool-other-model",
        tier_version_id="version-1",
        pool_key="shared-chat",
        callable_key="gpt-4o",
        rpm_capacity=5,
        tpm_capacity=500,
    )
    repository.policy_inputs = TierPolicyLoadResult(
        assignments=repository.policy_inputs.assignments,
        model_policies=repository.policy_inputs.model_policies,
        capacity_pools=repository.policy_inputs.capacity_pools + (extra_pool,),
        next_transition_at=repository.policy_inputs.next_transition_at,
    )
    await _install_preview_services(test_app, repository=repository)

    response = await client.get(
        "/ui/api/organizations/org-1/tier-policy-preview",
        headers=_headers(test_app),
    )

    assert response.status_code == 200
    payload = response.json()
    assert [
        (pool["pool_key"], pool["callable_key"])
        for pool in payload["capacity_pools"]
    ] == [("shared-chat", "gpt-4o-mini")]


@pytest.mark.asyncio
async def test_org_tier_policy_preview_skips_capacity_pools_for_denied_policy(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    repository.policy_inputs = TierPolicyLoadResult(
        assignments=repository.policy_inputs.assignments,
        model_policies=(
            replace(repository.policy_inputs.model_policies[0], access_mode="deny"),
        ),
        capacity_pools=repository.policy_inputs.capacity_pools,
        next_transition_at=repository.policy_inputs.next_transition_at,
    )
    await _install_preview_services(test_app, repository=repository)

    response = await client.get(
        "/ui/api/organizations/org-1/tier-policy-preview",
        headers=_headers(test_app),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["allowed_callable_keys"] == []
    assert payload["model_policies"][0]["access_mode"] == "deny"
    assert payload["model_policies"][0]["capacity_pool_key"] == "shared-chat"
    assert payload["capacity_pools"] == []


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_is_static_and_reports_limits(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "mode": "sync",
            "request_count": 11,
            "prompt_tokens": 300,
            "completion_tokens": 250,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access"] == {
        "allowed": True,
        "reason": "tier_policy_allowed",
        "explicit_policy": True,
        "tier_keys": ["growth"],
    }
    assert payload["request"]["tokens_per_request"] == 550
    assert payload["request"]["aggregate_tokens"] == 6050
    exceeded_scopes = {
        item["scope"]
        for item in payload["static_limit_checks"]
        if item["would_exceed_limit"]
    }
    assert exceeded_scopes == {
        "tier_org_model_rpm",
        "tier_org_model_tpm",
        "tier_pool_model_tpm",
    }
    assert payload["capacity_pool"]["pool_key"] == "shared-chat"


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_scales_token_checks_by_request_count(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "mode": "sync",
            "request_count": 2,
            "prompt_tokens": 251,
            "completion_tokens": 0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    model_tpm_check = next(
        item
        for item in payload["static_limit_checks"]
        if item["scope"] == "tier_org_model_tpm"
    )
    assert payload["request"]["tokens_per_request"] == 251
    assert payload["request"]["aggregate_tokens"] == 502
    assert model_tpm_check["amount"] == 502
    assert model_tpm_check["would_exceed_limit"] is True
    assert model_tpm_check["remaining_after_amount"] == -2


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_skips_pool_checks_for_denied_policy(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = TierPolicyLoadResult(
        assignments=repository.policy_inputs.assignments,
        model_policies=(replace(base_policy, access_mode="deny"),),
        capacity_pools=repository.policy_inputs.capacity_pools,
        next_transition_at=repository.policy_inputs.next_transition_at,
    )
    await _install_preview_services(test_app, repository=repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "mode": "sync",
            "request_count": 1,
            "prompt_tokens": 100,
            "completion_tokens": 100,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access"]["allowed"] is False
    assert payload["access"]["reason"] == "tier_policy_denied"
    assert payload["model_policy"]["access_mode"] == "deny"
    assert payload["model_policy"]["capacity_pool_key"] == "shared-chat"
    assert payload["capacity_pool"] is None
    assert payload["capacity_pool_rate_limits"] == []
    assert not any(
        item["scope"].startswith("tier_pool_")
        for item in payload["static_limit_checks"]
    )


@pytest.mark.asyncio
async def test_org_tier_policy_batch_simulation_falls_back_to_sync_limits(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "mode": "batch",
            "request_count": 11,
            "prompt_tokens": 30,
            "completion_tokens": 20,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    scopes = {item["scope"] for item in payload["static_limit_checks"]}
    assert {"tier_org_model_rpm", "tier_org_model_tpm"}.issubset(scopes)
    exceeded_scopes = {
        item["scope"]
        for item in payload["static_limit_checks"]
        if item["would_exceed_limit"]
    }
    assert {"tier_org_model_rpm", "tier_org_model_tpm"}.issubset(exceeded_scopes)


@pytest.mark.asyncio
async def test_org_tier_policy_batch_simulation_prefers_batch_limits(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    batch_policy = TierModelPolicyRecord(
        tier_model_policy_id=base_policy.tier_model_policy_id,
        tier_version_id=base_policy.tier_version_id,
        callable_key=base_policy.callable_key,
        enabled=base_policy.enabled,
        access_mode=base_policy.access_mode,
        rpm_limit=base_policy.rpm_limit,
        tpm_limit=base_policy.tpm_limit,
        rph_limit=base_policy.rph_limit,
        rpd_limit=base_policy.rpd_limit,
        tpd_limit=base_policy.tpd_limit,
        max_parallel_requests=base_policy.max_parallel_requests,
        batch_rpm_limit=20,
        batch_tpm_limit=600,
        pricing=base_policy.pricing,
        capacity_pool_key=base_policy.capacity_pool_key,
        priority=base_policy.priority,
        metadata=base_policy.metadata,
        created_at=base_policy.created_at,
        updated_at=base_policy.updated_at,
    )
    repository.policy_inputs = TierPolicyLoadResult(
        assignments=repository.policy_inputs.assignments,
        model_policies=(batch_policy,),
        capacity_pools=repository.policy_inputs.capacity_pools,
        next_transition_at=repository.policy_inputs.next_transition_at,
    )
    await _install_preview_services(test_app, repository=repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "mode": "batch",
            "request_count": 11,
            "prompt_tokens": 30,
            "completion_tokens": 20,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    scopes = {item["scope"] for item in payload["static_limit_checks"]}
    assert {"tier_org_model_batch_rpm", "tier_org_model_batch_tpm"}.issubset(scopes)
    assert "tier_org_model_rpm" not in scopes
    assert "tier_org_model_tpm" not in scopes
    assert not any(
        item["would_exceed_limit"]
        for item in payload["static_limit_checks"]
        if item["scope"].startswith("tier_org_model_batch_")
    )


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_rejects_unknown_mode(client, test_app) -> None:
    await _install_preview_services(test_app)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "mode": "realtime",
            "request_count": 1,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "mode must be sync or batch"


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_requires_existing_organization(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)

    response = await client.post(
        "/ui/api/organizations/org-missing/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "request_count": 1,
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Organization not found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "field_value"),
    (
        ("request_count", 1.9),
        ("request_count", True),
        ("prompt_tokens", 2.5),
        ("completion_tokens", False),
    ),
)
async def test_org_tier_policy_simulation_rejects_non_integer_counts(
    client,
    test_app,
    field_name: str,
    field_value: object,
) -> None:
    await _install_preview_services(test_app)
    payload = {
        "callable_key": "gpt-4o-mini",
        "mode": "sync",
        "request_count": 1,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        field_name: field_value,
    }

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == f"{field_name} must be an integer"


@pytest.mark.asyncio
async def test_org_tier_policy_preview_requires_snapshot_service(client, test_app) -> None:
    repository = _TierPreviewRepository()
    test_app.state.tier_repository = repository
    test_app.state.tier_policy_service = None

    response = await client.get(
        "/ui/api/organizations/org-1/tier-policy-preview",
        headers=_headers(test_app),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Tier policy service unavailable"
