from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from enum import StrEnum
from hashlib import sha256
import json

from src.route_policy_contract import (
    LLMTierSelectorPolicy,
    RoutePolicyMember,
    SELECTOR_POLICY_SEMANTICS_VERSION,
    SelectorLane,
    validate_selector_assignments,
)


class RouteSelectorActivationUnsupportedError(ValueError):
    """A selector policy reached an activation path before execution is available."""


class RouteSelectorActivationState(StrEnum):
    """Proof carried by runtime snapshots before selector execution is supported."""

    UNCHECKED = "unchecked"
    INACTIVE = "inactive"


def ensure_selector_activation_supported(
    policy_json: Mapping[str, object],
    *,
    semantics_version: int,
) -> None:
    """Keep selector policies inert until the complete execution path lands in PR 4."""

    if (
        semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION
        and policy_json.get("selector") is not None
    ):
        raise RouteSelectorActivationUnsupportedError(
            "LLM route-policy selectors cannot be activated until selector execution support "
            "is available"
        )


def build_routing_fingerprint(
    *,
    workload_mode: str,
    strategy: str | None,
    semantics_version: int,
    timeout_seconds: float | None = None,
    retry_max_attempts: int | None = None,
    retryable_error_classes: Collection[str] | None = None,
    selector: LLMTierSelectorPolicy | Mapping[str, object] | None = None,
    effective_members: Sequence[Mapping[str, object]],
) -> str:
    """Hash only normalized routing semantics, independent of revision identity."""

    canonical_selector: object = None
    if semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION and selector is not None:
        canonical_selector = (
            selector
            if isinstance(selector, LLMTierSelectorPolicy)
            else LLMTierSelectorPolicy.model_validate(selector)
        ).model_dump(mode="json")

    canonical_retryable_errors = None
    if retryable_error_classes:
        canonical_retryable_errors = sorted(set(retryable_error_classes))

    members: list[dict[str, object]] = []
    for member in effective_members:
        canonical_member: dict[str, object] = {
            "deployment_id": str(member.get("deployment_id") or ""),
            "enabled": bool(member.get("enabled", True)),
            "weight": member.get("weight"),
            "priority": member.get("priority"),
        }
        if semantics_version >= SELECTOR_POLICY_SEMANTICS_VERSION:
            canonical_member["lane"] = member.get("lane")
        members.append(canonical_member)

    document = {
        "schema": "deltallm-route-policy-routing-v1",
        "workload_mode": workload_mode,
        "semantics_version": semantics_version,
        "strategy": strategy,
        "timeout_seconds": float(timeout_seconds) if timeout_seconds is not None else None,
        "retry_max_attempts": (int(retry_max_attempts) if retry_max_attempts is not None else None),
        "retryable_error_classes": canonical_retryable_errors,
        "selector": canonical_selector,
        "members": members,
    }
    encoded = json.dumps(
        document,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"route-policy-v1:{sha256(encoded).hexdigest()}"


__all__ = [
    "LLMTierSelectorPolicy",
    "RoutePolicyMember",
    "RouteSelectorActivationState",
    "RouteSelectorActivationUnsupportedError",
    "SELECTOR_POLICY_SEMANTICS_VERSION",
    "SelectorLane",
    "build_routing_fingerprint",
    "ensure_selector_activation_supported",
    "validate_selector_assignments",
]
