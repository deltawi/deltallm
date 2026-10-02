from types import SimpleNamespace

import pytest

from src.models.responses import UserAPIKeyAuth
from src.config import AppConfig
from src.realtime.capacity import RealtimeCapacity
from src.realtime.config import RealtimeSettings
from src.realtime.errors import RealtimeError
from src.router.router import Deployment
from src.services.limit_counter import LimitCounter
from src.services.tier_policy_service import TierPolicyService


@pytest.mark.parametrize(
    "field,value",
    [
        ("tpm_limit", 100),
        ("key_tpm_limit", 100),
        ("key_tpd_limit", 100),
        ("user_tpm_limit", 100),
        ("team_tpm_limit", 100),
        ("org_tpm_limit", 100),
        ("org_tpd_limit", 100),
        ("team_tpd_limit", 100),
        ("user_tpd_limit", 100),
        ("team_model_tpm_limit", {"voice": 100}),
        ("org_model_tpm_limit", {"voi*": 100}),
        ("max_parallel_requests", 1),
    ],
)
def test_unsupported_limit_profiles_are_detected_before_zero_token_handshake(field, value):
    routing = SimpleNamespace(
        tiers=TierPolicyService(repository=None, mode="disabled"),
        tier_policy_mode="disabled",
        generations=SimpleNamespace(
            require_snapshot=lambda: SimpleNamespace(app_config=AppConfig())
        ),
    )
    capacity = RealtimeCapacity(
        LimitCounter(None, degraded_mode="fail_closed"), routing, RealtimeSettings()
    )
    auth = UserAPIKeyAuth(
        api_key="key", user_id="user", team_id="team", organization_id="org", **{field: value}
    )
    route = SimpleNamespace(
        target=SimpleNamespace(public_model="voice"), deployment=Deployment("voice", "voice", {})
    )
    with pytest.raises(RealtimeError):
        capacity.require_supported(auth, route)


@pytest.mark.parametrize("field", ["tpm_limit", "audio_seconds_pm_limit", "char_pm_limit"])
def test_deployment_quotas_without_native_allowances_fail_closed(field):
    routing = SimpleNamespace(
        tiers=TierPolicyService(repository=None, mode="disabled"),
        tier_policy_mode="disabled",
        generations=SimpleNamespace(
            require_snapshot=lambda: SimpleNamespace(app_config=AppConfig())
        ),
    )
    capacity = RealtimeCapacity(
        LimitCounter(None, degraded_mode="fail_closed"), routing, RealtimeSettings()
    )
    deployment = Deployment("voice", "voice", {}, model_info={"mode": "realtime"}, **{field: 100})
    route = SimpleNamespace(target=SimpleNamespace(public_model="voice"), deployment=deployment)
    with pytest.raises(RealtimeError, match="quotas require"):
        capacity.require_supported(UserAPIKeyAuth(api_key="key"), route)
