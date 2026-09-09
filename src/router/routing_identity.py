"""Precompute response-cache routing identity as part of runtime publication."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
from typing import TYPE_CHECKING

from src.providers.request_defaults import provider_request_defaults
from src.chat_capabilities import ChatRoutingCapabilities
from src.providers.resolution import resolve_provider, resolve_upstream_model
from src.route_policy_contract import (
    LLMTierSelectorPolicy,
    SELECTOR_POLICY_SEMANTICS_VERSION,
)
from src.router.callable_key_ownership import resolve_enabled_route_group_owners
from src.router.fallback_identity import fingerprint_fallback_dependencies
from src.router.selection.policy import build_routing_fingerprint

if TYPE_CHECKING:
    from src.router.failover import FallbackConfig
    from src.router.router import Deployment, RouteGroupPolicy, RoutingStrategy


def build_runtime_routing_fingerprints(
    *,
    groups: Sequence[Mapping[str, object]],
    policies: Mapping[str, RouteGroupPolicy],
    deployments: Mapping[str, Sequence[Deployment]],
    default_strategy: RoutingStrategy,
    failover_config: FallbackConfig,
    enable_pre_call_checks: bool = False,
) -> Mapping[str, str]:
    """Project validated runtime inputs once; never read mutable state on cache lookup."""

    owners = resolve_enabled_route_group_owners(groups)
    fingerprints: dict[str, str] = {}
    for key, members in deployments.items():
        group = owners.get(key, {})
        policy = policies.get(key)
        # File policies already contain current context semantics; historical database
        # policies carry their own version. No generation or policy revision enters identity.
        version = group.get("policy_semantics_version")
        semantics = (
            int(version) if isinstance(version, (int, str)) else SELECTOR_POLICY_SEMANTICS_VERSION
        )
        selector = group.get("selector") if semantics >= SELECTOR_POLICY_SEMANTICS_VERSION else None
        selector = LLMTierSelectorPolicy.model_validate(selector) if selector is not None else None
        lanes = _member_lanes(group) if selector is not None else {}
        strategy = policy.strategy if policy is not None and policy.strategy else default_strategy
        policy_identity = build_routing_fingerprint(
            workload_mode=str(group.get("mode") or ""),
            strategy=strategy.value,
            semantics_version=semantics,
            timeout_seconds=policy.timeout_seconds if policy else None,
            retry_max_attempts=policy.retry_max_attempts if policy else None,
            retryable_error_classes=policy.retryable_error_classes if policy else None,
            context=policy.context if policy else None,
            selector=selector,
            effective_members=[
                {
                    "deployment_id": member.deployment_id,
                    "enabled": True,
                    "weight": member.weight,
                    "priority": member.priority,
                    "lane": lanes.get(member.deployment_id),
                }
                for member in members
            ],
        )
        fingerprints[key] = _digest(
            {
                "policy": policy_identity,
                "deployments": [_deployment_identity(member) for member in members],
                "pre_call_checks": enable_pre_call_checks,
                "execution": {
                    "timeout": float(
                        policy.timeout_seconds
                        if policy and policy.timeout_seconds is not None
                        else failover_config.timeout
                    ),
                    "retries": policy.retry_max_attempts
                    if policy and policy.retry_max_attempts is not None
                    else failover_config.num_retries,
                    "retry_after": float(failover_config.retry_after),
                    "backoff_multiplier": float(failover_config.backoff_multiplier),
                    "backoff_max": float(failover_config.backoff_max),
                    "backoff_jitter": failover_config.backoff_jitter,
                },
            }
        )
    return fingerprint_fallback_dependencies(fingerprints, failover_config)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _deployment_identity(deployment: Deployment) -> str:
    """Project only configuration used by provider execution and candidate ordering.

    Credentials, health incarnation, live capacity/health, and opaque operator
    metadata are deliberately absent. Only the digest survives publication.
    """

    params, info = deployment.deltallm_params, deployment.model_info
    api_base = params.get("api_base")
    return _digest(
        {
            "provider": resolve_provider(params),
            "model": resolve_upstream_model(params),
            "api_base": api_base.rstrip("/") if isinstance(api_base, str) else api_base,
            "connection": {
                key: params.get(key)
                for key in (
                    "api_version",
                    "region",
                    "auth_header_name",
                    "auth_header_format",
                    "timeout",
                    "stream_timeout",
                    "max_tokens",
                )
            },
            "credential_reference": deployment.named_credential_id,
            "mode": str(info.get("mode") or "chat").strip().lower(),
            **(
                {
                    "chat_capabilities": ChatRoutingCapabilities.model_validate(
                        info["chat_capabilities"]
                    ).model_dump(mode="json")
                }
                if info.get("chat_capabilities") is not None
                else {}
            ),
            "defaults": provider_request_defaults(info),
            "context_limits": {
                key: info.get(key)
                for key in ("max_tokens", "max_input_tokens", "max_output_tokens")
            },
            "tags": sorted(set(deployment.tags)),
            "input_cost_per_token": info.get(
                "input_cost_per_token", deployment.input_cost_per_token
            ),
            "output_cost_per_token": info.get(
                "output_cost_per_token", deployment.output_cost_per_token
            ),
            "cost_per_request": info.get("cost_per_request"),
            "rpm": deployment.rpm_limit,
            "tpm": deployment.tpm_limit,
        }
    )


def _member_lanes(group: Mapping[str, object]) -> dict[str, str]:
    members = group.get("members")
    if not isinstance(members, list):
        return {}
    return {
        str(member["deployment_id"]): member["lane"]
        for member in members
        if isinstance(member, Mapping)
        and isinstance(member.get("deployment_id"), str)
        and isinstance(member.get("lane"), str)
    }
