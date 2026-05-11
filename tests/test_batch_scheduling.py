from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.batch.endpoints import BATCH_ENDPOINT_CHAT_COMPLETIONS, BATCH_ENDPOINT_EMBEDDINGS
from src.batch.scheduling import (
    API_KEY_TENANT_SCOPE_PREFIX,
    estimate_request_work_units,
    resolve_model_group,
    resolve_scheduler_version,
    resolve_tenant_scope,
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
