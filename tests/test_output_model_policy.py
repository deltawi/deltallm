from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.config import AppConfig
from src.db.tiers.tiers import TierModelPolicyRecord, TierPolicyLoadResult
from src.models.errors import InvalidRequestError, ServiceUnavailableError
from src.models.output_limits import validate_model_output_limits
from src.models.responses import UserAPIKeyAuth
from src.api.admin.endpoints.tier_schemas import TierModelPolicyBulkLimitsRequest
from src.services.output_admission import prepare_output_policy
from src.services.output_limit_types import OutputPolicy, OutputScope
from src.services.output_policy_configuration import validate_output_policy_configuration
from src.services.output_policy_preview import output_limit_projection
from src.services.tier_policy_compiler import compile_tier_policy_snapshot
from tests.services.test_tier_policy_compiler import _assignment


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"m": True},
        {"m": "10"},
        {"m": 1.5},
        {"m": 0},
        {"m": -1},
        {"m": 2**31},
        {"m": None},
        {"*": 10},
        {" m": 10},
        {"m\n": 10},
        {"": 10},
        {"é" * 129: 10},
        {str(i): 1 for i in range(65)},
    ],
)
def test_model_limits_reject_unsafe_or_unbounded_values(value):
    with pytest.raises(InvalidRequestError):
        validate_model_output_limits(value)


def test_model_limits_preserve_exact_ids_and_explicit_clear():
    assert validate_model_output_limits(None) is None
    assert validate_model_output_limits({}) is None
    assert validate_model_output_limits({"__proto__": 1, "models/a:b": 2**31 - 1}) == {
        "__proto__": 1,
        "models/a:b": 2**31 - 1,
    }
    with pytest.raises(ValidationError):
        UserAPIKeyAuth(api_key="k", key_model_output_tpm_limit={"m": False})


def compiled(limit=100, version="v1", assignment_type="primary"):
    policy = TierModelPolicyRecord("p", version, "m", output_tpm_limit=limit)
    return compile_tier_policy_snapshot(
        TierPolicyLoadResult(
            assignments=(_assignment("a", version_id=version, assignment_type=assignment_type),),
            model_policies=(policy,),
            capacity_pools=(),
        )
    ).org_model_policy[("org-1", "m")]


def tier_service(policy, **kwargs):
    return SimpleNamespace(
        mode="enforce",
        snapshot_stale=False,
        missing_service_mode="fail_closed",
        get_model_policy=lambda org, model: policy if (org, model) == ("org-1", "m") else None,
        **kwargs,
    )


def test_all_seven_scopes_apply_and_counter_identity_survives_tier_edit():
    auth = UserAPIKeyAuth(
        api_key="k",
        organization_id="org-1",
        team_id="t",
        user_id="u",
        key_output_tpm_limit=50,
        user_output_tpm_limit=60,
        team_output_tpm_limit=70,
        org_output_tpm_limit=80,
        key_model_output_tpm_limit={"m": 90},
        team_model_output_tpm_limit={"m": 110},
    )
    policy = prepare_output_policy(auth, model="m", tier_policy_service=tier_service(compiled()))
    assert len(policy.scopes) == 7
    changed = prepare_output_policy(
        auth, model="m", tier_policy_service=tier_service(compiled(200, "v2", "override"))
    )
    assert changed.keys(environment="test") == policy.keys(environment="test")
    other = prepare_output_policy(auth, model="other", tier_policy_service=tier_service(compiled()))
    assert other.scopes == policy.scopes[:4]
    child_cleared = prepare_output_policy(
        auth.model_copy(update={"key_model_output_tpm_limit": None}),
        model="m",
        tier_policy_service=tier_service(compiled()),
    )
    assert "org_model_output_tpm" in {s.scope for s in child_cleared.scopes}
    assert "team_model_output_tpm" in {s.scope for s in child_cleared.scopes}


def test_scope_identity_has_no_delimiter_collision_and_preserves_scalar_namespace():
    a = OutputPolicy((OutputScope("team_model_output_tpm", "a:b", 10, "c"),))
    b = OutputPolicy((OutputScope("team_model_output_tpm", "a", 10, "b:c"),))
    assert a.keys(environment="test") != b.keys(environment="test")
    assert a.keys(environment="test") == replace(a, scopes=(replace(a.scopes[0], limit=20),)).keys(
        environment="test"
    )
    with pytest.raises(ValueError):
        OutputPolicy((OutputScope("team_model_output_tpm", "t", 10),))


@pytest.mark.parametrize("mode", ["disabled", "shadow"])
def test_tier_modes_do_not_enforce_but_child_limits_remain(mode):
    service = tier_service(compiled())
    service.mode = mode
    auth = UserAPIKeyAuth(
        api_key="k", organization_id="org-1", key_model_output_tpm_limit={"m": 10}
    )
    policy = prepare_output_policy(auth, model="m", tier_policy_service=service)
    assert [s.scope for s in policy.scopes] == ["key_model_output_tpm"]


@pytest.mark.parametrize("state", ["missing", "stale", "fail_open"])
def test_tier_output_never_silently_uses_an_unavailable_enforcing_policy(state):
    service = tier_service(compiled())
    if state == "stale":
        service.snapshot_stale = True
    if state == "fail_open":
        service.missing_service_mode = "fail_open"
    with pytest.raises(ServiceUnavailableError):
        prepare_output_policy(
            UserAPIKeyAuth(api_key="k", organization_id="org-1"),
            model="m",
            tier_policy_service=None if state == "missing" else service,
            tier_policy_mode="enforce",
            tier_policy_missing_service_mode="fail_closed",
        )


def test_bulk_output_can_be_set_or_cleared_without_changing_other_limits():
    for value in (10, None):
        body = TierModelPolicyBulkLimitsRequest(
            expected_revision=1, output_tpm_limit=value, all_filtered=True
        )
        assert body.model_dump(exclude_unset=True) == {
            "expected_revision": 1,
            "output_tpm_limit": value,
            "all_filtered": True,
        }


def test_simulation_separates_completion_projection_from_admission():
    projected = output_limit_projection(compiled(10), 100, request_count=2, completion_tokens=5)
    assert [p["next_call_blocked"] for p in projected] == [False, True]
    assert projected[1]["basis"] == "empty_window_completion_projection"


@pytest.mark.parametrize("missing_mode", ["fail_open", "fail_closed"])
async def test_configuration_protects_tier_only_policies(missing_mode):
    db = SimpleNamespace(query_raw=AsyncMock(return_value=[{"tier_enabled": True}]))
    config = AppConfig(
        general_settings={
            "tier_policy_mode": "enforce",
            "tier_policy_missing_service_mode": missing_mode,
        }
    )
    if missing_mode == "fail_open":
        with pytest.raises(ValueError, match="tier_policy_missing_service_mode"):
            await validate_output_policy_configuration(
                db, config, redis_available=True, degraded_mode="fail_closed"
            )
    else:
        await validate_output_policy_configuration(
            db, config, redis_available=True, degraded_mode="fail_closed"
        )
        with pytest.raises(ValueError, match="Redis"):
            await validate_output_policy_configuration(
                db, config, redis_available=False, degraded_mode="fail_closed"
            )
