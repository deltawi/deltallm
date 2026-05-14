from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.batch.endpoints import BATCH_ENDPOINT_CHAT_COMPLETIONS, BATCH_ENDPOINT_EMBEDDINGS
from src.batch.scheduling import (
    API_KEY_TENANT_SCOPE_PREFIX,
    BatchJobRankInput,
    BatchSizeAgingConfig,
    BatchTenantFairShareConfig,
    build_flow_id,
    calculate_size_aging_rank,
    display_tenant_scope_id,
    estimate_request_work_units,
    max_deficit_for_flow,
    parse_model_group_list,
    parse_tenant_scope_preference,
    quantum_for_weight,
    resolve_model_group,
    resolve_scheduler_version,
    resolve_tenant_scope,
    tuned_claim_item_limit,
)
from src.batch.scheduling.estimator import size_class_for_work_units
from src.config import GeneralSettings


def test_resolve_tenant_scope_prefers_organization_then_team_then_api_key_then_user() -> None:
    assert resolve_tenant_scope(
        organization_id="org-1",
        team_id="team-1",
        api_key="key-1",
        user_id="user-1",
    ).scope_type == "organization"
    assert resolve_tenant_scope(team_id="team-1", api_key="key-1", user_id="user-1").scope_type == "team"
    assert resolve_tenant_scope(api_key="key-1", user_id="user-1").scope_type == "api_key"
    assert resolve_tenant_scope(user_id="user-1").scope_type == "user"
    anonymous = resolve_tenant_scope()
    assert anonymous.scope_type == "anonymous"
    assert anonymous.scope_id == "anonymous"


def test_api_key_tenant_scope_uses_stable_non_secret_hash() -> None:
    scope = resolve_tenant_scope(api_key="sk-test-secret")

    assert scope.scope_type == "api_key"
    assert scope.scope_id.startswith(API_KEY_TENANT_SCOPE_PREFIX)
    assert "sk-test-secret" not in scope.scope_id
    assert resolve_tenant_scope(api_key="sk-test-secret").scope_id == scope.scope_id


def test_resolve_tenant_scope_accepts_operator_preference_order() -> None:
    scope = resolve_tenant_scope(
        organization_id="org-1",
        team_id="team-1",
        api_key="key-1",
        scope_preference=("team", "organization", "api_key"),
    )

    assert scope.scope_type == "team"
    assert scope.scope_id == "team-1"


def test_fair_share_flow_id_is_stable_and_hides_api_key() -> None:
    raw_api_key = "sk-test-secret"
    hashed_scope = resolve_tenant_scope(api_key=raw_api_key).scope_id
    flow_id = build_flow_id(
        service_tier="standard",
        model_group="text-embedding-3-small",
        tenant_scope_type="api_key",
        tenant_scope_id=hashed_scope,
    )

    assert flow_id == build_flow_id(
        service_tier="standard",
        model_group="text-embedding-3-small",
        tenant_scope_type="api_key",
        tenant_scope_id=hashed_scope,
    )
    assert raw_api_key not in flow_id
    assert hashed_scope not in flow_id
    assert display_tenant_scope_id(scope_type="api_key", scope_id=hashed_scope).startswith("api_key:")


def test_fair_share_config_quantum_and_deficit_caps() -> None:
    assert parse_tenant_scope_preference("team,organization,api_key,team,bad") == (
        "team",
        "organization",
        "api_key",
    )
    assert quantum_for_weight(base_quantum_work_units=16, weight=4) == 64
    assert quantum_for_weight(base_quantum_work_units=300, weight=4) == 256
    assert max_deficit_for_flow(quantum_work_units=16, max_deficit_multiplier=8) == 128
    config = BatchTenantFairShareConfig.from_settings(
        GeneralSettings(
            embeddings_batch_tenant_fair_share_enabled=True,
            embeddings_batch_model_capacity_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
            embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
        )
    )
    assert config.enabled is True
    assert config.base_quantum_work_units == 16
    assert config.max_deficit_multiplier == 8
    assert parse_model_group_list("model-a,model-b,model-a,, ") == ("model-a", "model-b")


def test_resolve_scheduler_version_prefers_active_then_shadow_then_fifo() -> None:
    assert resolve_scheduler_version(active_enabled=True, shadow_enabled=False) == "scheduler_v2"
    assert resolve_scheduler_version(active_enabled=True, shadow_enabled=True) == "scheduler_v2"
    assert resolve_scheduler_version(active_enabled=False, shadow_enabled=True) == "scheduler_v2_shadow"
    assert resolve_scheduler_version(active_enabled=False, shadow_enabled=False) == "fifo_v1"


def test_active_scheduler_requires_work_slice_claiming() -> None:
    with pytest.raises(ValidationError, match="scheduler_claim_mode='work_slice'"):
        GeneralSettings(embeddings_batch_scheduler_enabled=True)


def test_active_scheduler_requires_strict_model_homogeneity() -> None:
    with pytest.raises(ValidationError, match="strict_model_homogeneity"):
        GeneralSettings(
            embeddings_batch_scheduler_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
        )


def test_active_scheduler_config_is_allowed_with_safe_prerequisites() -> None:
    settings = GeneralSettings(
        embeddings_batch_scheduler_enabled=True,
        embeddings_batch_scheduler_claim_mode="work_slice",
        embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
    )

    assert settings.embeddings_batch_scheduler_enabled is True


def test_scheduler_backfill_worker_is_opt_in_by_default() -> None:
    assert GeneralSettings().embeddings_batch_scheduler_backfill_enabled is False


def test_model_capacity_scheduler_requires_work_slice_claiming() -> None:
    with pytest.raises(ValidationError, match="scheduler_claim_mode='work_slice'"):
        GeneralSettings(
            embeddings_batch_model_capacity_enabled=True,
            embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
        )


def test_model_capacity_scheduler_requires_strict_model_homogeneity() -> None:
    with pytest.raises(ValidationError, match="strict_model_homogeneity"):
        GeneralSettings(
            embeddings_batch_model_capacity_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
        )


def test_model_capacity_scheduler_config_is_opt_in() -> None:
    settings = GeneralSettings(
        embeddings_batch_model_capacity_enabled=True,
        embeddings_batch_scheduler_claim_mode="work_slice",
        embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
    )

    assert settings.embeddings_batch_default_model_max_in_flight == 16
    assert settings.embeddings_batch_default_model_max_claim_work_units == 64
    assert settings.embeddings_batch_model_capacity_fraction == 0.25
    assert settings.embeddings_batch_model_capacity_fail_open is False


def test_scheduler_shadow_requires_work_slice_claiming() -> None:
    with pytest.raises(ValidationError, match="scheduler_claim_mode='work_slice'"):
        GeneralSettings(
            embeddings_batch_scheduler_shadow_enabled=True,
            embeddings_batch_model_capacity_enabled=True,
            embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
        )


def test_scheduler_shadow_requires_strict_model_homogeneity() -> None:
    with pytest.raises(ValidationError, match="strict_model_homogeneity"):
        GeneralSettings(
            embeddings_batch_scheduler_shadow_enabled=True,
            embeddings_batch_model_capacity_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
        )


def test_scheduler_shadow_requires_model_capacity() -> None:
    with pytest.raises(ValidationError, match="model_capacity_enabled"):
        GeneralSettings(
            embeddings_batch_scheduler_shadow_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
            embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
        )


def test_scheduler_shadow_config_is_allowed_with_safe_prerequisites() -> None:
    settings = GeneralSettings(
        embeddings_batch_scheduler_shadow_enabled=True,
        embeddings_batch_model_capacity_enabled=True,
        embeddings_batch_scheduler_claim_mode="work_slice",
        embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
    )

    assert settings.embeddings_batch_scheduler_shadow_enabled is True


def test_tenant_fair_share_requires_model_capacity() -> None:
    with pytest.raises(ValidationError, match="model_capacity_enabled"):
        GeneralSettings(
            embeddings_batch_tenant_fair_share_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
            embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
        )


def test_tenant_fair_share_config_supports_disabled_model_groups() -> None:
    config = BatchTenantFairShareConfig.from_settings(
        GeneralSettings(
            embeddings_batch_tenant_fair_share_enabled=True,
            embeddings_batch_model_capacity_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
            embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
            embeddings_batch_tenant_fair_share_disabled_model_groups=["model-a", "model-b"],
        )
    )

    assert config.disabled_model_groups == ("model-a", "model-b")


def test_size_aware_scheduler_requires_fair_share_or_shadow() -> None:
    with pytest.raises(ValidationError, match="tenant_fair_share_enabled=true or"):
        GeneralSettings(
            embeddings_batch_size_aware_scheduling_enabled=True,
            embeddings_batch_model_capacity_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
            embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
        )


def test_size_aware_scheduler_allows_shadow_rollout() -> None:
    settings = GeneralSettings(
        embeddings_batch_size_aware_scheduling_enabled=True,
        embeddings_batch_scheduler_shadow_enabled=True,
        embeddings_batch_model_capacity_enabled=True,
        embeddings_batch_scheduler_claim_mode="work_slice",
        embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
    )

    assert settings.embeddings_batch_size_aware_scheduling_enabled is True
    assert settings.embeddings_batch_scheduler_shadow_enabled is True


def test_size_aware_scheduler_config_defaults_are_bounded() -> None:
    config = BatchSizeAgingConfig.from_settings(
        GeneralSettings(
            embeddings_batch_tenant_fair_share_enabled=True,
            embeddings_batch_model_capacity_enabled=True,
            embeddings_batch_size_aware_scheduling_enabled=True,
            embeddings_batch_scheduler_claim_mode="work_slice",
            embeddings_batch_scheduler_strict_model_homogeneity_enabled=True,
        )
    )

    assert config.enabled is True
    assert config.aging_seconds_per_work_unit == 30
    assert config.max_age_credit_work_units == 1_000
    assert config.min_large_job_claim_interval_seconds == 30
    assert config.small_job_fast_lane_enabled is False
    assert config.small_job_max_work_units == 100


def test_size_aging_rank_favors_smaller_remaining_work_at_equal_age() -> None:
    now = datetime.now(tz=UTC)

    small = calculate_size_aging_rank(
        BatchJobRankInput(remaining_work_units=10, queue_entered_at=now),
        now=now,
    )
    large = calculate_size_aging_rank(
        BatchJobRankInput(remaining_work_units=100, queue_entered_at=now),
        now=now,
    )

    assert small.rank < large.rank
    assert small.policy_reason == "small_remaining_work"


def test_size_aging_rank_decreases_with_capped_age_credit() -> None:
    now = datetime.now(tz=UTC)

    aged = calculate_size_aging_rank(
        BatchJobRankInput(remaining_work_units=500, queue_entered_at=now - timedelta(seconds=600)),
        now=now,
        aging_seconds_per_work_unit=30,
        max_age_credit_work_units=5,
    )

    assert aged.age_credit_work_units == 5
    assert aged.rank == 495
    assert aged.policy_reason == "aging_credit"


def test_size_aging_rank_uses_fractional_age_credit_like_sql() -> None:
    now = datetime.now(tz=UTC)

    result = calculate_size_aging_rank(
        BatchJobRankInput(
            remaining_work_units=10,
            queue_entered_at=now - timedelta(seconds=45),
        ),
        now=now,
        aging_seconds_per_work_unit=30,
        max_age_credit_work_units=1_000,
    )

    assert result.age_credit_work_units == 1
    assert result.rank == 8.5
    assert result.policy_reason == "aging_credit"


def test_large_job_progress_floor_activates_after_interval() -> None:
    now = datetime.now(tz=UTC)

    result = calculate_size_aging_rank(
        BatchJobRankInput(
            remaining_work_units=5_000,
            queue_entered_at=now,
            last_scheduled_at=now - timedelta(seconds=31),
            size_class="xl",
        ),
        now=now,
        min_large_job_claim_interval_seconds=30,
    )

    assert result.large_job_progress_floor is True
    assert result.policy_reason == "large_job_progress_floor"


def test_large_job_progress_floor_does_not_activate_immediately() -> None:
    now = datetime.now(tz=UTC)

    result = calculate_size_aging_rank(
        BatchJobRankInput(
            remaining_work_units=5_000,
            queue_entered_at=now,
            last_scheduled_at=None,
            size_class="xl",
        ),
        now=now,
        min_large_job_claim_interval_seconds=30,
    )

    assert result.large_job_progress_floor is False
    assert result.policy_reason == "tenant_fair_share"


def test_size_aware_large_claim_limit_respects_microbatch_floor() -> None:
    assert tuned_claim_item_limit(max_items=10, min_items_for_microbatch=4, size_class="xl") == 5
    assert tuned_claim_item_limit(max_items=3, min_items_for_microbatch=4, size_class="l") == 3
    assert tuned_claim_item_limit(max_items=10, min_items_for_microbatch=4, size_class="s") == 10


def test_resolve_model_group_uses_router_alias_with_identity_fallback() -> None:
    class _Router:
        def resolve_model_group(self, model_name: str) -> str:
            assert model_name == "public-model"
            return "route-group"

    assert resolve_model_group("public-model", _Router()) == "route-group"
    assert resolve_model_group("public-model") == "public-model"


def test_estimate_embeddings_work_units_handles_strings_tokens_and_arrays() -> None:
    assert estimate_request_work_units(BATCH_ENDPOINT_EMBEDDINGS, {"input": "a" * 257}) == 2
    assert estimate_request_work_units(BATCH_ENDPOINT_EMBEDDINGS, {"input": list(range(257))}) == 2
    assert estimate_request_work_units(BATCH_ENDPOINT_EMBEDDINGS, {"input": ["a", "b" * 257]}) == 3


def test_estimate_chat_work_units_uses_prompt_and_completion_size() -> None:
    request_body = {
        "messages": [{"role": "user", "content": "a" * 700}],
        "max_tokens": 257,
    }
    assert estimate_request_work_units(BATCH_ENDPOINT_CHAT_COMPLETIONS, request_body) == 4


@pytest.mark.parametrize(
    ("work_units", "expected"),
    [
        (1, "xs"),
        (10, "xs"),
        (11, "s"),
        (100, "s"),
        (101, "m"),
        (1_000, "m"),
        (1_001, "l"),
        (10_000, "l"),
        (10_001, "xl"),
    ],
)
def test_size_class_boundaries(work_units: int, expected: str) -> None:
    assert size_class_for_work_units(work_units) == expected
