from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

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


class _OrganizationLimitsDB:
    async def query_raw(self, query: str, *params):  # noqa: ANN001, ANN201
        assert "FROM deltallm_organizationtable" in query
        assert params == ("org-1",)
        return [
            {
                "rpm_limit": 5,
                "tpm_limit": 2_000,
                "rph_limit": None,
                "rpd_limit": 100,
                "tpd_limit": None,
                "model_rpm_limit": {"gpt-*": 4, "gpt-4o-mini": 3},
                "model_tpm_limit": None,
            }
        ]


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
async def test_org_tier_policy_preview_and_simulation_include_organization_hard_caps(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)
    test_app.state.prisma_manager = SimpleNamespace(client=_OrganizationLimitsDB())
    headers = _headers(test_app)

    preview = await client.get(
        "/ui/api/organizations/org-1/tier-policy-preview",
        headers=headers,
    )
    simulation = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=headers,
        json={
            "callable_key": "gpt-4o-mini",
            "request_count": 6,
            "prompt_tokens": 1,
        },
    )

    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["organization_hard_caps"]["rpm_limit"] == 5
    assert {item["scope"] for item in preview_payload["organization_rate_limits"]} == {
        "org_rpm",
        "org_tpm",
        "org_rpd",
    }

    assert simulation.status_code == 200
    simulation_payload = simulation.json()
    hard_cap_checks = {
        item["scope"]: item
        for item in simulation_payload["static_limit_checks"]
        if item["scope"].startswith("org_")
    }
    assert set(hard_cap_checks) == {
        "org_rpm",
        "org_tpm",
        "org_rpd",
        "org_model_rpm",
    }
    assert hard_cap_checks["org_rpm"]["would_exceed_limit"] is True
    assert hard_cap_checks["org_model_rpm"]["would_exceed_limit"] is True
    assert hard_cap_checks["org_model_rpm"]["limit"] == 3
    assert simulation_payload["decision"]["primary_limiting_scope"] == "org_model_rpm"


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
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(
            replace(
                base_policy,
                pricing={
                    "input_cost_per_token": 0.01,
                    "output_cost_per_token": 0.0,
                },
            ),
        ),
    )
    await _install_preview_services(test_app, repository=repository)

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
    assert payload["decision"] == {
        "allowed": False,
        "reason": "static_limit_exceeded",
        "primary_limiting_scope": "tier_org_model_tpm",
        "limiting_scopes": [
            "tier_org_model_tpm",
            "tier_pool_model_tpm",
            "tier_org_model_rpm",
        ],
        "basis": "empty_window_static",
        "live_capacity_evaluated": False,
    }
    assert payload["calculated_price"] == {
        "status": "available",
        "reason": None,
        "currency": "USD",
        "kind": "exact",
        "amount": 33.0,
        "minimum_amount": 33.0,
        "maximum_amount": 33.0,
        "request_count": 11,
        "amount_scope": "aggregate",
        "per_request_amount": 3.0,
        "per_request_minimum_amount": 3.0,
        "per_request_maximum_amount": 3.0,
        "billing_mode": "chat",
        "usage_snapshot": {"prompt_tokens": 300, "completion_tokens": 250},
        "configured_candidate_count": 1,
        "priced_candidate_count": 1,
        "unpriced_candidate_count": 0,
        "unevaluated_candidate_count": 0,
        "unpriced_reasons": [],
        "pricing_sources": ["tier"],
        "basis": "configured_routes",
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
async def test_org_tier_policy_simulation_returns_price_range_across_routes(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(
            base_deployment,
            deployment_id="priced-low",
            model_info={"output_cost_per_token": 0.02},
        ),
        replace(
            base_deployment,
            deployment_id="priced-high",
            model_info={"output_cost_per_token": 0.04},
        ),
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "request_count": 2,
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"]["allowed"] is True
    assert payload["calculated_price"]["kind"] == "range"
    assert payload["calculated_price"]["amount"] is None
    assert payload["calculated_price"]["minimum_amount"] == 4.0
    assert payload["calculated_price"]["maximum_amount"] == 6.0
    assert payload["calculated_price"]["request_count"] == 2
    assert payload["calculated_price"]["amount_scope"] == "aggregate"
    assert payload["calculated_price"]["per_request_amount"] is None
    assert payload["calculated_price"]["per_request_minimum_amount"] == 2.0
    assert payload["calculated_price"]["per_request_maximum_amount"] == 3.0
    assert payload["calculated_price"]["configured_candidate_count"] == 2
    assert payload["calculated_price"]["pricing_sources"] == ["deployment", "tier"]


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_does_not_quote_without_configured_routes(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)
    test_app.state.router.deployment_registry.pop("gpt-4o-mini", None)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "no_configured_routes"
    assert quote["configured_candidate_count"] == 0
    assert quote["priced_candidate_count"] == 0
    assert quote["unpriced_candidate_count"] == 0
    assert quote["unevaluated_candidate_count"] == 0


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_prices_generated_images(client, test_app) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={"output_cost_per_image": 0.4}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "image_generation"})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "image_generation",
            "request_count": 3,
            "input_images": 0,
            "output_images": 2,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "available"
    assert quote["amount"] == 2.4
    assert quote["billing_mode"] == "image_generation"
    assert quote["usage_snapshot"] == {"input_images": 0, "output_images": 2}


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_requires_input_image_pricing(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(
            replace(base_policy, pricing={"output_cost_per_image": 0.4}),
        ),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "image_generation"})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "image_generation",
            "input_images": 1,
            "output_images": 2,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "no_configured_pricing"
    assert quote["unpriced_reasons"] == ["no_configured_pricing"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("billing_mode", "pricing", "usage", "expected_amount"),
    (
        (
            "audio_speech",
            {"input_cost_per_character": 0.002},
            {"input_characters": 1000, "duration_seconds": 0},
            2.0,
        ),
        (
            "audio_speech",
            {"input_cost_per_audio_token": 0.1},
            {"input_characters": 0, "input_audio_tokens": 10, "duration_seconds": 0},
            1.0,
        ),
        (
            "audio_transcription",
            {"input_cost_per_second": 0.1},
            {"duration_seconds": 30},
            3.0,
        ),
        (
            "rerank",
            {"input_cost_per_token": 0.01},
            {"prompt_tokens": 100},
            1.0,
        ),
        (
            "embedding",
            {"input_cost_per_token": 0.01},
            {"prompt_tokens": 100},
            1.0,
        ),
    ),
)
async def test_org_tier_policy_simulation_dispatches_runtime_billing_modes(
    client,
    test_app,
    billing_mode: str,
    pricing: dict[str, float],
    usage: dict[str, int],
    expected_amount: float,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing=pricing),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": billing_mode})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={"callable_key": "gpt-4o-mini", "billing_mode": billing_mode, **usage},
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "available"
    assert quote["amount"] == expected_amount


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_applies_provider_transcription_duration_rules(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(
            replace(base_policy, pricing={"input_cost_per_second": 0.111}),
        ),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(
            base_deployment,
            deployment_id="groq-transcription",
            deltallm_params={"model": "groq/whisper-large-v3"},
            model_info={"mode": "audio_transcription"},
        ),
        replace(
            base_deployment,
            deployment_id="openai-transcription",
            deltallm_params={"model": "openai/whisper-1"},
            model_info={"mode": "audio_transcription"},
        ),
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "audio_transcription",
            "duration_seconds": 2,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "available"
    assert quote["kind"] == "range"
    assert quote["minimum_amount"] == 0.222
    assert quote["maximum_amount"] == 1.11
    assert quote["usage_snapshot"]["duration_seconds"] == 2.0


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_does_not_bill_groq_minimum_without_duration(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(
            replace(base_policy, pricing={"input_cost_per_second": 0.111}),
        ),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(
            base_deployment,
            deployment_id="groq-transcription",
            deltallm_params={"model": "groq/whisper-large-v3"},
            model_info={"mode": "audio_transcription"},
        ),
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "audio_transcription",
            "duration_seconds": 0,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "missing_usage_for_billing_mode"


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_supports_legacy_image_price_fallback(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={"input_cost_per_image": 0.25}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "image_generation"})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "image_generation",
            "output_images": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["calculated_price"]["amount"] == 0.5


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_marks_missing_mode_usage_unavailable(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={"input_cost_per_character": 0.002}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "audio_speech"})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "audio_speech",
            "input_characters": 0,
            "input_audio_tokens": 0,
            "duration_seconds": 0,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "missing_usage_for_billing_mode"


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_preserves_explicit_zero_price(client, test_app) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={"output_cost_per_image": 0.0}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "image_generation"})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "image_generation",
            "output_images": 1,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "available"
    assert quote["amount"] == 0.0


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_uses_default_token_catalog_pricing(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={}),),
    )
    await _install_preview_services(test_app, repository=repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "chat",
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "available"
    assert quote["amount"] == 0.000045
    assert quote["pricing_sources"] == ["default"]


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_does_not_treat_token_defaults_as_free(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["unknown-model"] = [
        replace(
            base_deployment,
            model_name="unknown-model",
            deployment_id="unknown-model-1",
            deltallm_params={
                **base_deployment.deltallm_params,
                "model": "openai/unknown-model",
            },
            model_info={"mode": "chat"},
        )
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "unknown-model",
            "billing_mode": "chat",
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "no_configured_pricing"


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_rejects_partial_token_override(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(
            base_deployment,
            model_info={"mode": "chat", "output_cost_per_token": 0.25},
        )
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "chat",
            "prompt_tokens": 100,
            "completion_tokens": 0,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "no_configured_pricing"
    assert quote["pricing_sources"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "non_sync_pricing",
    (
        {"input_cost_per_token_cache_hit": 0.25},
        {"batch_input_cost_per_token": 0.25},
    ),
)
async def test_sync_token_simulation_uses_catalog_for_non_sync_only_metadata(
    client,
    test_app,
    non_sync_pricing: dict[str, float],
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(
            base_deployment,
            model_info={"mode": "chat", **non_sync_pricing},
        )
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "mode": "sync",
            "billing_mode": "chat",
            "prompt_tokens": 100,
            "completion_tokens": 0,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "available"
    assert quote["amount"] == 0.000015
    assert quote["pricing_sources"] == ["default"]


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_preserves_zero_request_only_token_price(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={"cost_per_request": 0.0}),),
    )
    await _install_preview_services(test_app, repository=repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "chat",
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "available"
    assert quote["amount"] == 0.0
    assert quote["pricing_sources"] == ["tier"]


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_requires_token_usage_without_request_price(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={}),),
    )
    await _install_preview_services(test_app, repository=repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "chat",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "missing_usage_for_billing_mode"


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_marks_partial_token_route_pricing(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(
            base_deployment,
            deployment_id="complete-token-pricing",
            model_info={
                "mode": "chat",
                "input_cost_per_token": 0.01,
                "output_cost_per_token": 0.02,
            },
        ),
        replace(
            base_deployment,
            deployment_id="partial-token-pricing",
            model_info={"mode": "chat", "output_cost_per_token": 0.02},
        ),
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "chat",
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "partial"
    assert quote["reason"] == "some_routes_unpriced"
    assert quote["minimum_amount"] == 2.0
    assert quote["maximum_amount"] == 2.0
    assert quote["priced_candidate_count"] == 1
    assert quote["unpriced_candidate_count"] == 1
    assert quote["pricing_sources"] == ["deployment"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pricing", "expected_status", "expected_reason"),
    (
        ({"input_cost_per_character": 0.0}, "available", None),
        (
            {"output_cost_per_audio_token": 0.0},
            "unavailable",
            "missing_usage_for_billing_mode",
        ),
    ),
)
async def test_org_tier_policy_simulation_applies_zero_audio_price_only_to_matching_usage(
    client,
    test_app,
    pricing: dict[str, float],
    expected_status: str,
    expected_reason: str | None,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing=pricing),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "audio_speech"})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "audio_speech",
            "input_characters": 100,
            "input_audio_tokens": 0,
            "output_audio_tokens": 0,
            "duration_seconds": 0,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == expected_status
    assert quote["reason"] == expected_reason
    if expected_status == "available":
        assert quote["amount"] == 0.0


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_rejects_partial_audio_token_pricing(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(
            replace(
                base_policy,
                pricing={"input_cost_per_audio_token": 0.25},
            ),
        ),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "audio_transcription"})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "audio_transcription",
            "prompt_tokens": 12,
            "input_audio_tokens": 100,
            "duration_seconds": 0,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "no_configured_pricing"
    assert quote["unpriced_reasons"] == ["no_configured_pricing"]


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_does_not_treat_router_defaults_as_free(
    client,
    test_app,
) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "audio_speech"})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "audio_speech",
            "input_characters": 100,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "unavailable"
    assert quote["reason"] == "no_configured_pricing"


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_applies_batch_token_price(client, test_app) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(
            replace(
                base_policy,
                pricing={
                    "input_cost_per_token": 0.01,
                    "batch_input_cost_per_token": 0.005,
                },
            ),
        ),
    )
    await _install_preview_services(test_app, repository=repository)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "mode": "batch",
            "billing_mode": "chat",
            "prompt_tokens": 100,
            "completion_tokens": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["calculated_price"]["amount"] == 0.5


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_marks_partial_route_pricing(client, test_app) -> None:
    repository = _TierPreviewRepository()
    base_policy = repository.policy_inputs.model_policies[0]
    repository.policy_inputs = replace(
        repository.policy_inputs,
        model_policies=(replace(base_policy, pricing={}),),
    )
    await _install_preview_services(test_app, repository=repository)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(
            base_deployment,
            deployment_id="priced-image",
            model_info={"mode": "image_generation", "output_cost_per_image": 0.25},
        ),
        replace(
            base_deployment,
            deployment_id="unpriced-image",
            model_info={"mode": "image_generation"},
        ),
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "image_generation",
            "output_images": 2,
        },
    )

    assert response.status_code == 200
    quote = response.json()["calculated_price"]
    assert quote["status"] == "partial"
    assert quote["reason"] == "some_routes_unpriced"
    assert quote["amount"] is None
    assert quote["minimum_amount"] == 0.5
    assert quote["maximum_amount"] == 0.5
    assert quote["priced_candidate_count"] == 1
    assert quote["unpriced_candidate_count"] == 1
    assert quote["unevaluated_candidate_count"] == 0


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_rejects_mixed_or_mismatched_billing_modes(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, deployment_id="chat", model_info={"mode": "chat"}),
        replace(
            base_deployment,
            deployment_id="image",
            model_info={"mode": "image_generation"},
        ),
    ]

    mixed = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={"callable_key": "gpt-4o-mini"},
    )
    assert mixed.status_code == 200
    mixed_quote = mixed.json()["calculated_price"]
    assert mixed_quote["reason"] == "mixed_billing_modes"
    assert mixed_quote["unpriced_candidate_count"] == 0
    assert mixed_quote["unevaluated_candidate_count"] == 2

    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": "image_generation"})
    ]
    mismatch = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={"callable_key": "gpt-4o-mini", "billing_mode": "chat"},
    )
    assert mismatch.status_code == 200
    mismatch_quote = mismatch.json()["calculated_price"]
    assert mismatch_quote["reason"] == "billing_mode_mismatch"
    assert mismatch_quote["unpriced_candidate_count"] == 0
    assert mismatch_quote["unevaluated_candidate_count"] == 1


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
    assert payload["decision"]["allowed"] is False
    assert payload["decision"]["reason"] == "tier_policy_denied"
    assert payload["decision"]["primary_limiting_scope"] == "tier_model_access"
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
        ("input_images", 1.5),
        ("output_audio_tokens", True),
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
@pytest.mark.parametrize(
    ("field_value", "expected_detail"),
    (
        (True, "duration_seconds must be a number"),
        ("not-a-number", "duration_seconds must be a number"),
        (-0.1, "duration_seconds must be a non-negative finite number"),
        ("nan", "duration_seconds must be a non-negative finite number"),
        ("inf", "duration_seconds must be a non-negative finite number"),
    ),
)
async def test_org_tier_policy_simulation_rejects_invalid_duration(
    client,
    test_app,
    field_value: object,
    expected_detail: str,
) -> None:
    await _install_preview_services(test_app)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "audio_transcription",
            "duration_seconds": field_value,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_org_tier_policy_simulation_rejects_unknown_billing_mode(
    client,
    test_app,
) -> None:
    await _install_preview_services(test_app)

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": "video_generation",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "billing_mode must be chat, embedding, rerank, image_generation, "
        "audio_speech, or audio_transcription"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("billing_mode", ("embedding", "rerank"))
async def test_org_tier_policy_simulation_rejects_completion_tokens_for_input_only_modes(
    client,
    test_app,
    billing_mode: str,
) -> None:
    await _install_preview_services(test_app)
    base_deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    test_app.state.router.deployment_registry["gpt-4o-mini"] = [
        replace(base_deployment, model_info={"mode": billing_mode})
    ]

    response = await client.post(
        "/ui/api/organizations/org-1/tier-policy/simulate",
        headers=_headers(test_app),
        json={
            "callable_key": "gpt-4o-mini",
            "billing_mode": billing_mode,
            "prompt_tokens": 100,
            "completion_tokens": 1,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        f"completion_tokens is not supported for {billing_mode} billing"
    )


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
