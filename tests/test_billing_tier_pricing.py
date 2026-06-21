from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.billing.cost import completion_cost, compute_billing_result
from src.billing.tier_pricing import resolve_tier_pricing
from src.models.responses import UserAPIKeyAuth


def _policy(pricing: dict[str, float], *, mode: str = "sync"):
    return SimpleNamespace(
        mode=mode,
        pricing=pricing,
        source=SimpleNamespace(
            assignment_id="assignment-1",
            tier_key="enterprise",
            tier_version_id="version-1",
            tier_version_number=3,
            model_policy_id="policy-1",
        ),
    )


class _TierPricingService:
    def __init__(
        self,
        policies: dict[tuple[str, str, str], object],
        *,
        mode: str = "enforce",
        snapshot_stale: bool = False,
        unavailable_reason: str = "tier_policy_unavailable_fail_open",
    ) -> None:
        self.policies = policies
        self.mode = mode
        self.snapshot_stale = snapshot_stale
        self.unavailable_reason = unavailable_reason

    def get_pricing_policy(self, organization_id: str, callable_key: str, *, mode: str = "sync"):
        return self.policies.get((organization_id, callable_key, mode))

    def get_snapshot(self):
        return SimpleNamespace(org_tier_keys={"org-1": ("enterprise",)})

    def resolve_unavailable_decision(self, organization_id: str):
        return SimpleNamespace(
            allowed=True,
            reason=self.unavailable_reason,
            explicit_tier_policy=organization_id == "org-1",
        )


class _FailingTierPricingService(_TierPricingService):
    def get_pricing_policy(self, organization_id: str, callable_key: str, *, mode: str = "sync"):
        del organization_id, callable_key, mode
        raise RuntimeError("tier pricing backend unavailable")


def test_tier_pricing_overrides_customer_price_and_preserves_provider_price() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService(
        {
            (
                "org-1",
                "model-a",
                "sync",
            ): _policy({"input_cost_per_token": 0.1, "output_cost_per_token": 0.2})
        }
    )

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={"input_cost_per_token": 1.0, "output_cost_per_token": 2.0},
    )

    usage = {"prompt_tokens": 10, "completion_tokens": 5}
    assert completion_cost(model="model-a", usage=usage, custom_pricing=resolution.customer_token_pricing) == 2.0
    assert completion_cost(model="model-a", usage=usage, custom_pricing=resolution.provider_token_pricing) == 20.0
    metadata = resolution.spend_metadata(provider_cost=20.0)
    assert metadata["pricing_source"] == "tier"
    assert metadata["customer_tier_key"] == "enterprise"
    assert metadata["customer_tier_keys"] == ["enterprise"]
    assert metadata["tier_model_policy_id"] == "policy-1"
    assert metadata["tier_version_id"] == "version-1"
    assert metadata["provider_cost"] == 20.0


def test_tier_pricing_partially_overrides_and_falls_back_to_deployment_fields() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService(
        {("org-1", "model-a", "sync"): _policy({"input_cost_per_token": 0.1})}
    )

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={"input_cost_per_token": 1.0, "output_cost_per_token": 2.0},
    )

    cost = completion_cost(
        model="model-a",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        custom_pricing=resolution.customer_token_pricing,
    )
    assert cost == 11.0


def test_shadow_mode_observes_tier_pricing_without_charging_it() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService(
        {("org-1", "model-a", "sync"): _policy({"input_cost_per_token": 0.1, "output_cost_per_token": 0.2})},
        mode="shadow",
    )

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={"input_cost_per_token": 1.0, "output_cost_per_token": 2.0},
    )

    cost = completion_cost(
        model="model-a",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        custom_pricing=resolution.customer_token_pricing,
    )
    metadata = resolution.spend_metadata(provider_cost=20.0)

    assert cost == 20.0
    assert metadata["pricing_source"] == "deployment"
    assert metadata["tier_policy_service_mode"] == "shadow"
    assert metadata["tier_pricing_applied"] is False
    assert metadata["customer_tier_key"] == "enterprise"
    assert metadata["tier_model_policy_id"] == "policy-1"


def test_disabled_mode_ignores_tier_pricing() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService(
        {("org-1", "model-a", "sync"): _policy({"input_cost_per_token": 0.1, "output_cost_per_token": 0.2})},
        mode="disabled",
    )

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={"input_cost_per_token": 1.0, "output_cost_per_token": 2.0},
    )

    cost = completion_cost(
        model="model-a",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        custom_pricing=resolution.customer_token_pricing,
    )
    metadata = resolution.spend_metadata(provider_cost=20.0)

    assert cost == 20.0
    assert metadata["pricing_source"] == "deployment"
    assert metadata["tier_policy_service_mode"] == "disabled"
    assert metadata["tier_pricing_applied"] is False
    assert "customer_tier_key" not in metadata
    assert "tier_model_policy_id" not in metadata


def test_stale_tier_pricing_is_observed_without_charging_it() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService(
        {("org-1", "model-a", "sync"): _policy({"input_cost_per_token": 0.1, "output_cost_per_token": 0.2})},
        snapshot_stale=True,
    )

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={"input_cost_per_token": 1.0, "output_cost_per_token": 2.0},
    )

    cost = completion_cost(
        model="model-a",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        custom_pricing=resolution.customer_token_pricing,
    )
    metadata = resolution.spend_metadata(provider_cost=20.0)

    assert cost == 20.0
    assert metadata["pricing_source"] == "deployment"
    assert metadata["tier_policy_service_mode"] == "enforce"
    assert metadata["tier_snapshot_stale"] is True
    assert metadata["tier_pricing_authoritative"] is False
    assert metadata["tier_unavailable_reason"] == "tier_policy_unavailable_fail_open"
    assert metadata["tier_pricing_applied"] is False
    assert metadata["customer_tier_key"] == "enterprise"
    assert metadata["tier_model_policy_id"] == "policy-1"


def test_tier_pricing_lookup_failure_falls_back_with_observable_metadata() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _FailingTierPricingService({})

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={"input_cost_per_token": 1.0, "output_cost_per_token": 2.0},
    )

    cost = completion_cost(
        model="model-a",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        custom_pricing=resolution.customer_token_pricing,
    )
    metadata = resolution.spend_metadata(provider_cost=20.0)

    assert cost == 20.0
    assert metadata["pricing_source"] == "deployment"
    assert metadata["tier_pricing_authoritative"] is False
    assert metadata["tier_pricing_applied"] is False
    assert metadata["tier_unavailable_reason"] == "tier_pricing_lookup_failed"
    assert metadata["tier_pricing_error_type"] == "RuntimeError"
    assert metadata["customer_tier_keys"] == ["enterprise"]


def test_batch_resolution_uses_batch_tier_policy_when_present() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService(
        {
            (
                "org-1",
                "model-a",
                "batch",
            ): _policy(
                {
                    "input_cost_per_token": 10.0,
                    "output_cost_per_token": 20.0,
                    "batch_input_cost_per_token": 0.5,
                    "batch_output_cost_per_token": 0.75,
                },
                mode="batch",
            )
        }
    )

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={"input_cost_per_token": 1.0, "output_cost_per_token": 2.0},
        mode="batch",
    )

    cost = completion_cost(
        model="model-a",
        usage={"prompt_tokens": 5, "completion_tokens": 2},
        custom_pricing=resolution.customer_token_pricing,
        pricing_tier="batch",
        model_info=resolution.customer_model_info,
    )
    assert cost == 4.0
    assert resolution.tier_pricing_policy_mode == "batch"


def test_batch_resolution_charges_batch_only_tier_policy_without_sync_pricing() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService(
        {
            (
                "org-1",
                "model-a",
                "batch",
            ): _policy(
                {
                    "batch_input_cost_per_token": 0.5,
                    "batch_output_cost_per_token": 0.75,
                },
                mode="batch",
            )
        }
    )

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={},
        mode="batch",
    )

    cost = completion_cost(
        model="model-a",
        usage={"prompt_tokens": 5, "completion_tokens": 2},
        custom_pricing=resolution.customer_token_pricing,
        pricing_tier="batch",
        model_info=resolution.customer_model_info,
    )

    assert cost == 4.0
    assert resolution.source == "tier"
    assert resolution.tier_pricing_policy_mode == "batch"


def test_batch_resolution_falls_back_to_sync_tier_policy_when_batch_policy_absent() -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService(
        {
            (
                "org-1",
                "model-a",
                "sync",
            ): _policy({"input_cost_per_token": 1.0, "output_cost_per_token": 1.0})
        }
    )

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={
            "input_cost_per_token": 10.0,
            "output_cost_per_token": 10.0,
            "batch_price_multiplier": 0.5,
        },
        mode="batch",
    )

    cost = completion_cost(
        model="model-a",
        usage={"prompt_tokens": 5, "completion_tokens": 5},
        custom_pricing=resolution.customer_token_pricing,
        pricing_tier="batch",
        model_info=resolution.customer_model_info,
    )
    assert cost == 5.0
    assert resolution.tier_pricing_policy_mode == "sync"


@pytest.mark.parametrize(
    ("mode", "usage", "tier_pricing", "expected_cost"),
    [
        ("image_generation", {"images": 2}, {"input_cost_per_image": 0.25}, 0.5),
        ("audio_speech", {"input_characters": 1000}, {"input_cost_per_character": 0.002}, 2.0),
        (
            "audio_transcription",
            {"duration_seconds": 30},
            {"input_cost_per_second": 0.1},
            3.0,
        ),
        ("rerank", {"prompt_tokens": 7}, {"input_cost_per_token": 0.3}, 2.1),
        ("image_generation", {"images": 2}, {"cost_per_request": 0.75}, 0.75),
    ],
)
def test_tier_pricing_supports_non_chat_billing_modes(
    mode: str,
    usage: dict[str, int | float],
    tier_pricing: dict[str, float],
    expected_cost: float,
) -> None:
    auth = UserAPIKeyAuth(api_key="key-1", organization_id="org-1")
    service = _TierPricingService({("org-1", "model-a", "sync"): _policy(tier_pricing)})

    resolution = resolve_tier_pricing(
        auth=auth,
        model="model-a",
        tier_policy_service=service,
        deployment_model_info={},
    )

    result = compute_billing_result(mode=mode, usage=usage, model_info=resolution.customer_model_info)
    assert result.cost == expected_cost
