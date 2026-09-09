from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from types import MappingProxyType
from typing import TYPE_CHECKING

from src.billing.operation_reservation import token_price_allowance
from src.billing.selector_charge import SelectorPriceSnapshot
from src.chat_capabilities import ChatRoutingCapabilities
from src.providers.resolution import resolve_provider, resolve_upstream_model
from src.router.selection.capacity import SelectorCapacityBounds
from src.router.selection.contracts import SelectorPolicyIdentity
from src.router.selection.lanes import SelectorLaneRouting
from src.router.selection.policy import build_routing_fingerprint
from src.router.selection.provider import ConcreteSelectorTarget
from src.router.selection.target_validation import qualify_chat_target

if TYPE_CHECKING:
    from src.router.group_policy import RouteGroupPolicy
    from src.router.router import Deployment


@dataclass(frozen=True, slots=True)
class QualifiedSelector:
    routing: SelectorLaneRouting
    identity: SelectorPolicyIdentity
    target: ConcreteSelectorTarget
    pricing: SelectorPriceSnapshot
    capacity: SelectorCapacityBounds
    admission_allowance: Decimal
    provider: str
    deployment_model: str
    capabilities: Mapping[str, ChatRoutingCapabilities]
    classifier_tags: frozenset[str]


def qualify_selector_groups(
    policies: Mapping[str, RouteGroupPolicy],
    deployments: Mapping[str, tuple[Deployment, ...]],
) -> Mapping[str, QualifiedSelector]:
    """Compile once per pinned generation, never on a request or Redis cache hit."""
    return MappingProxyType(
        {
            key: _qualify(policy, deployments.get(key, ()))
            for key, policy in policies.items()
            if policy.selector is not None
        }
    )


def _qualify(policy: RouteGroupPolicy, deployments: tuple[Deployment, ...]) -> QualifiedSelector:
    routing = policy.selector
    if routing is None:
        raise ValueError("selector qualification requires a selector")
    if policy.context is None or policy.context.unknown_capacity != "exclude":
        raise ValueError("selector requires context routing with unknown_capacity=exclude")
    enabled = {member.deployment_id for member in routing.members if member.enabled}
    inventory = {dep.deployment_id: dep for dep in deployments if dep.deployment_id in enabled}
    if inventory.keys() != enabled:
        raise ValueError("selector members require concrete enabled deployments")
    capabilities = MappingProxyType({key: _capabilities(dep) for key, dep in inventory.items()})
    classifier = inventory[routing.policy.classifier_deployment_id]
    pricing = selector_price_snapshot(classifier.model_info)
    input_bound = selector_context_capacity(classifier.model_info)
    rpm, tpm = selector_capacity_limits(classifier.model_info)
    capacity = SelectorCapacityBounds(
        health_ref=classifier.health_ref,
        rpm=rpm,
        tpm=tpm,
        concurrency=rpm,
        token_allowance=input_bound + 64,
    )
    return QualifiedSelector(
        routing=routing,
        identity=SelectorPolicyIdentity(
            policy_version=policy.policy_version,
            fingerprint=build_routing_fingerprint(
                workload_mode="chat",
                strategy=policy.strategy.value if policy.strategy else None,
                semantics_version=3,
                timeout_seconds=policy.timeout_seconds,
                retry_max_attempts=policy.retry_max_attempts,
                retryable_error_classes=policy.retryable_error_classes,
                context=policy.context,
                selector=routing.policy,
                effective_members=[member.model_dump() for member in routing.members],
            ),
        ),
        target=ConcreteSelectorTarget.from_config(
            classifier.deployment_id, classifier.deltallm_params
        ),
        pricing=pricing,
        capacity=capacity,
        admission_allowance=token_price_allowance(
            pricing, input_tokens=input_bound, output_tokens=64
        ),
        provider=resolve_provider(classifier.deltallm_params),
        deployment_model=resolve_upstream_model(classifier.deltallm_params),
        capabilities=capabilities,
        classifier_tags=frozenset(classifier.tags),
    )


def selector_context_capacity(info: Mapping[str, object]) -> int:
    limits = [info.get(key) for key in ("max_input_tokens", "max_tokens")]
    known = [value for value in limits if type(value) is int and 1 <= value < 2**31 - 64]
    if not known:
        raise ValueError("selector members require known positive context capacity")
    return min(known)


def selector_capacity_limits(info: Mapping[str, object]) -> tuple[int, int]:
    rpm, tpm = info.get("rpm_limit"), info.get("tpm_limit")
    if (
        type(rpm) is not int
        or type(tpm) is not int
        or not 1 <= rpm <= 2**31 - 1
        or not 1 <= tpm <= 2**31 - 1
    ):
        raise ValueError("selector classifier requires positive RPM and TPM limits")
    return rpm, tpm


def _capabilities(deployment: Deployment) -> ChatRoutingCapabilities:
    qualify_chat_target(deployment.deltallm_params)
    selector_context_capacity(deployment.model_info)
    value = deployment.model_info.get("chat_capabilities")
    if value is None:
        raise ValueError("selector members require explicit model_info.chat_capabilities")
    return ChatRoutingCapabilities.model_validate(value)


def selector_price_snapshot(info: Mapping[str, object]) -> SelectorPriceSnapshot:
    fields = {
        "input_cost_per_token",
        "output_cost_per_token",
        "input_cost_per_token_cache_hit",
        "cost_per_request",
    }
    for key, value in info.items():
        if (
            "cost_per_" in key
            and key not in fields
            and key not in {"batch_input_cost_per_token", "batch_output_cost_per_token"}
            and value not in (None, 0)
        ):
            raise ValueError("selector has unsupported provider billing dimensions")
    if any(info.get(key) is None for key in ("input_cost_per_token", "output_cost_per_token")):
        raise ValueError("selector requires explicit input and output provider prices")
    try:
        prices = {key: Decimal(str(info[key])) for key in fields if info.get(key) is not None}
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("selector requires finite non-negative provider prices") from exc
    prices.setdefault("cost_per_request", Decimal(0))
    version = sha256(
        json.dumps({key: str(value) for key, value in prices.items()}, sort_keys=True).encode()
    ).hexdigest()
    return SelectorPriceSnapshot(source="deployment:model_info", version=version, **prices)
