from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from src.chat_capabilities import ChatRoutingCapabilities
from src.route_policy_contract import LLMTierSelectorPolicy, RoutePolicyMember
from src.router.policy_validation import merge_policy_members
from src.router.selection.policy import SELECTOR_POLICY_SEMANTICS_VERSION
from src.router.selection.qualification import (
    selector_context_capacity,
    validate_classifier_metadata,
)
from src.router.selection.target_validation import qualify_chat_target


class ActivationInventoryMember(Protocol):
    enabled: bool
    model_info: Mapping[str, object] | None
    provider_model: str | None
    provider_name: str | None


def validate_selector_activation_inventory(
    document: Mapping[str, object],
    inventory: Mapping[str, ActivationInventoryMember],
    deployments: Mapping[str, ActivationInventoryMember] | None = None,
) -> None:
    """Qualify answer membership and the independently loaded classifier dependency."""
    if document.get("selector") is None:
        return
    selector = LLMTierSelectorPolicy.model_validate(document["selector"])
    targets = deployments if deployments is not None else inventory
    classifier = targets.get(selector.classifier_deployment_id)
    if classifier is None:
        raise ValueError("selector classifier must reference an existing concrete deployment")
    validate_classifier_metadata(
        {"model": classifier.provider_model, "provider": classifier.provider_name},
        classifier.model_info or {},
    )
    effective_members = merge_policy_members(
        [{"deployment_id": key, "enabled": item.enabled} for key, item in inventory.items()],
        document.get("members"),
        semantics_version=SELECTOR_POLICY_SEMANTICS_VERSION,
    )
    for raw in effective_members:
        member = RoutePolicyMember.model_validate(raw)
        if not member.enabled:
            continue
        loaded = inventory.get(member.deployment_id)
        info = loaded.model_info if loaded is not None else None
        if info is None or info.get("chat_capabilities") is None:
            raise ValueError("selector members require explicit model_info.chat_capabilities")
        qualify_chat_target({"model": loaded.provider_model, "provider": loaded.provider_name})
        ChatRoutingCapabilities.model_validate(info["chat_capabilities"])
        selector_context_capacity(info)
