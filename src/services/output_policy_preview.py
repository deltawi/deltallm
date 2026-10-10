"""Forecast completion accounting without treating output as an admission charge."""

from __future__ import annotations

from src.services.tier_policy_models import CompiledTierModelPolicy


def output_limit_projection(
    policy: CompiledTierModelPolicy | None,
    organization_limit: object,
    *,
    request_count: int,
    completion_tokens: int,
) -> list[dict[str, str | int | bool]]:
    limits = [("org_output_tpm", organization_limit)]
    if policy is not None and policy.access_mode == "allow":
        limits.append(("org_model_output_tpm", policy.limits.output_tpm_limit))
    projected = request_count * completion_tokens
    return [
        {
            "scope": scope,
            "limit": limit,
            "projected_output": projected,
            "next_call_blocked": projected >= limit,
            "basis": "empty_window_completion_projection",
        }
        for scope, limit in limits
        if type(limit) is int and limit > 0
    ]
