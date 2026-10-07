from __future__ import annotations

from src.models.responses import UserAPIKeyAuth
from src.services.output_limit_types import OutputPolicy, output_scopes
from src.services.output_limit_types import OutputScope
from src.services.output_limit_redis import output_unavailable
from src.services.tier_policy_service import TierPolicyService


def prepare_output_policy(
    auth: UserAPIKeyAuth,
    *,
    model: str | None = None,
    tier_policy_service: TierPolicyService | None = None,
    tier_policy_mode: str = "disabled",
    tier_policy_missing_service_mode: str = "fail_open",
) -> OutputPolicy | None:
    scopes = list(output_scopes(auth))
    model = model.strip() if model else None
    if model:
        for scope, identity, limits in (
            ("team_model_output_tpm", auth.team_id, auth.team_model_output_tpm_limit),
            ("key_model_output_tpm", auth.api_key, auth.key_model_output_tpm_limit),
        ):
            limit = limits.get(model) if limits else None
            if limit is not None:
                if not identity:
                    raise output_unavailable()
                scopes.append(OutputScope(scope, identity, limit, model))
        tier_limit = _tier_output_limit(
            auth.organization_id,
            model,
            tier_policy_service,
            tier_policy_mode,
            tier_policy_missing_service_mode,
        )
        if tier_limit is not None:
            scopes.append(
                OutputScope("org_model_output_tpm", auth.organization_id, tier_limit, model)
            )
    return OutputPolicy(tuple(scopes)) if scopes else None


def _tier_output_limit(
    organization_id: str | None,
    model: str,
    service: TierPolicyService | None,
    mode: str,
    missing_mode: str,
) -> int | None:
    if not organization_id or getattr(service, "mode", mode) != "enforce":
        return None
    if service is None or getattr(service, "snapshot_stale", False):
        if getattr(service, "missing_service_mode", missing_mode) == "fail_closed":
            raise output_unavailable()
        return None
    policy = service.get_model_policy(organization_id, model)
    if policy is not None and getattr(policy, "access_mode", "allow") != "allow":
        return None
    limit = getattr(getattr(policy, "limits", None), "output_tpm_limit", None)
    if (
        limit is not None
        and getattr(service, "missing_service_mode", missing_mode) != "fail_closed"
    ):
        raise output_unavailable()
    return limit
