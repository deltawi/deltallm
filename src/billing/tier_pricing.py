from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from src.billing.cost import BillingResult, ModelPricing
from src.billing.pricing import pricing_from_model_info

PricingMode = Literal["sync", "batch"]
PricingSource = Literal["tier", "deployment", "default"]
TierPolicyServiceMode = Literal["disabled", "shadow", "enforce"]

_TIER_PRICING_LOOKUP_FAILED = "tier_pricing_lookup_failed"

_PRICING_KEYS = frozenset(
    {
        "input_cost_per_token",
        "output_cost_per_token",
        "input_cost_per_token_cache_hit",
        "output_cost_per_token_cache_hit",
        "batch_input_cost_per_token",
        "batch_output_cost_per_token",
        "batch_price_multiplier",
        "input_cost_per_character",
        "output_cost_per_character",
        "input_cost_per_second",
        "output_cost_per_second",
        "input_cost_per_image",
        "output_cost_per_image",
        "input_cost_per_audio_token",
        "output_cost_per_audio_token",
        "cost_per_request",
    }
)


@dataclass(frozen=True, slots=True)
class PricingResolution:
    requested_mode: PricingMode
    source: PricingSource
    customer_model_info: dict[str, Any]
    provider_model_info: dict[str, Any]
    customer_token_pricing: ModelPricing | None
    provider_token_pricing: ModelPricing | None
    customer_tier_keys: tuple[str, ...] = ()
    customer_tier_key: str | None = None
    tier_assignment_id: str | None = None
    tier_version_id: str | None = None
    tier_version_number: int | None = None
    tier_model_policy_id: str | None = None
    tier_policy_service_mode: TierPolicyServiceMode = "disabled"
    tier_snapshot_stale: bool = False
    tier_pricing_authoritative: bool = False
    tier_unavailable_reason: str | None = None
    tier_pricing_error_type: str | None = None
    tier_pricing_applied: bool = False
    tier_pricing_policy_mode: str | None = None
    tier_pricing_fields: tuple[str, ...] = field(default_factory=tuple)

    def spend_metadata(
        self,
        *,
        provider_cost: float | None = None,
        billing: BillingResult | Mapping[str, Any] | None = None,
        pricing_tier: str | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "pricing_source": self.source,
            "tier_pricing_mode": self.requested_mode,
            "tier_policy_service_mode": self.tier_policy_service_mode,
            "tier_snapshot_stale": self.tier_snapshot_stale,
            "tier_pricing_authoritative": self.tier_pricing_authoritative,
            "tier_pricing_applied": self.tier_pricing_applied,
        }
        if self.tier_unavailable_reason is not None:
            metadata["tier_unavailable_reason"] = self.tier_unavailable_reason
        if self.tier_pricing_error_type is not None:
            metadata["tier_pricing_error_type"] = self.tier_pricing_error_type
        if self.customer_tier_keys:
            metadata["customer_tier_keys"] = list(self.customer_tier_keys)
        if self.customer_tier_key is not None:
            metadata["customer_tier_key"] = self.customer_tier_key
        if self.tier_assignment_id is not None:
            metadata["tier_assignment_id"] = self.tier_assignment_id
        if self.tier_version_id is not None:
            metadata["tier_version_id"] = self.tier_version_id
        if self.tier_version_number is not None:
            metadata["tier_version_number"] = self.tier_version_number
        if self.tier_model_policy_id is not None:
            metadata["tier_model_policy_id"] = self.tier_model_policy_id
        if self.tier_pricing_policy_mode is not None:
            metadata["tier_pricing_policy_mode"] = self.tier_pricing_policy_mode
        if self.tier_pricing_fields:
            metadata["tier_pricing_fields"] = list(self.tier_pricing_fields)
        if provider_cost is not None:
            metadata["provider_cost"] = round(float(provider_cost), 10)
        if pricing_tier is not None:
            metadata["pricing_tier"] = pricing_tier
        if billing is not None:
            metadata["billing"] = _billing_metadata(billing)
        return metadata


def resolve_tier_pricing(
    *,
    auth: Any,
    model: str,
    tier_policy_service: Any | None,
    deployment_model_info: Mapping[str, Any] | None = None,
    fallback_input_cost_per_token: float | None = None,
    fallback_output_cost_per_token: float | None = None,
    mode: PricingMode = "sync",
) -> PricingResolution:
    requested_mode = _normalize_mode(mode)
    provider_model_info = _provider_model_info(
        deployment_model_info,
        fallback_input_cost_per_token=fallback_input_cost_per_token,
        fallback_output_cost_per_token=fallback_output_cost_per_token,
    )
    organization_id = getattr(auth, "organization_id", None)
    tier_policy_service_mode = _tier_policy_service_mode(tier_policy_service)
    tier_snapshot_stale = (
        _tier_snapshot_stale(tier_policy_service)
        if tier_policy_service_mode != "disabled"
        else False
    )
    tier_unavailable_reason = (
        _tier_unavailable_reason(tier_policy_service, organization_id)
        if tier_snapshot_stale
        else None
    )
    tier_lookup = (
        _tier_pricing_lookup(
            tier_policy_service=tier_policy_service,
            organization_id=organization_id,
            model=model,
            mode=requested_mode,
        )
        if tier_policy_service_mode != "disabled"
        else _TierPricingLookup()
    )
    if tier_unavailable_reason is None:
        tier_unavailable_reason = tier_lookup.unavailable_reason
    tier_pricing_authoritative = (
        tier_policy_service_mode == "enforce"
        and not tier_snapshot_stale
        and tier_lookup.unavailable_reason is None
    )
    tier_policy = tier_lookup.policy
    tier_pricing_applied = tier_policy is not None and tier_pricing_authoritative
    customer_model_info = dict(provider_model_info)
    if tier_pricing_applied:
        customer_model_info.update(dict(getattr(tier_policy, "pricing", {}) or {}))

    source: PricingSource = "tier" if tier_pricing_applied else _fallback_source(provider_model_info)
    customer_tier_keys = (
        _customer_tier_keys(tier_policy_service, organization_id)
        if tier_policy_service_mode != "disabled"
        else ()
    )
    source_record = getattr(tier_policy, "source", None)

    return PricingResolution(
        requested_mode=requested_mode,
        source=source,
        customer_model_info=customer_model_info,
        provider_model_info=provider_model_info,
        customer_token_pricing=pricing_from_model_info(customer_model_info),
        provider_token_pricing=pricing_from_model_info(provider_model_info),
        customer_tier_keys=customer_tier_keys,
        customer_tier_key=_str_or_none(getattr(source_record, "tier_key", None)),
        tier_assignment_id=_str_or_none(getattr(source_record, "assignment_id", None)),
        tier_version_id=_str_or_none(getattr(source_record, "tier_version_id", None)),
        tier_version_number=_int_or_none(getattr(source_record, "tier_version_number", None)),
        tier_model_policy_id=_str_or_none(getattr(source_record, "model_policy_id", None)),
        tier_policy_service_mode=tier_policy_service_mode,
        tier_snapshot_stale=tier_snapshot_stale,
        tier_pricing_authoritative=tier_pricing_authoritative,
        tier_unavailable_reason=tier_unavailable_reason,
        tier_pricing_error_type=tier_lookup.error_type,
        tier_pricing_applied=tier_pricing_applied,
        tier_pricing_policy_mode=_str_or_none(getattr(tier_policy, "mode", None)),
        tier_pricing_fields=tuple(sorted(str(key) for key in getattr(tier_policy, "pricing", {}) or {})),
    )


def resolve_deployment_tier_pricing(
    *,
    auth: Any,
    model: str,
    deployment: Any,
    tier_policy_service: Any | None,
    mode: PricingMode = "sync",
) -> PricingResolution:
    return resolve_tier_pricing(
        auth=auth,
        model=model,
        tier_policy_service=tier_policy_service,
        deployment_model_info=getattr(deployment, "model_info", None),
        fallback_input_cost_per_token=getattr(deployment, "input_cost_per_token", None),
        fallback_output_cost_per_token=getattr(deployment, "output_cost_per_token", None),
        mode=mode,
    )


def attach_pricing_metadata(
    metadata: Mapping[str, Any] | None,
    resolution: PricingResolution,
    *,
    provider_cost: float | None = None,
    billing: BillingResult | Mapping[str, Any] | None = None,
    pricing_tier: str | None = None,
) -> dict[str, Any]:
    merged = dict(metadata or {})
    existing_billing = merged.get("billing") if isinstance(merged.get("billing"), Mapping) else None
    merged.update(
        resolution.spend_metadata(
            provider_cost=provider_cost,
            billing=billing if billing is not None else existing_billing,
            pricing_tier=pricing_tier,
        )
    )
    return merged


@dataclass(frozen=True, slots=True)
class _TierPricingLookup:
    policy: Any | None = None
    unavailable_reason: str | None = None
    error_type: str | None = None


def _tier_pricing_lookup(
    *,
    tier_policy_service: Any | None,
    organization_id: str | None,
    model: str,
    mode: PricingMode,
) -> _TierPricingLookup:
    if tier_policy_service is None:
        return _TierPricingLookup()
    getter = getattr(tier_policy_service, "get_pricing_policy", None)
    if getter is None:
        return _TierPricingLookup()
    try:
        policy = getter(organization_id, model, mode=mode)
    except Exception as exc:
        return _failed_tier_pricing_lookup(exc)
    if policy is not None:
        return _TierPricingLookup(policy=policy)
    if mode == "batch":
        try:
            return _TierPricingLookup(policy=getter(organization_id, model, mode="sync"))
        except Exception as exc:
            return _failed_tier_pricing_lookup(exc)
    return _TierPricingLookup()


def _failed_tier_pricing_lookup(exc: Exception) -> _TierPricingLookup:
    return _TierPricingLookup(
        unavailable_reason=_TIER_PRICING_LOOKUP_FAILED,
        error_type=exc.__class__.__name__,
    )


def _tier_policy_service_mode(tier_policy_service: Any | None) -> TierPolicyServiceMode:
    if tier_policy_service is None:
        return "disabled"
    normalized = str(getattr(tier_policy_service, "mode", "enforce") or "enforce").strip().lower()
    if normalized in {"disabled", "shadow", "enforce"}:
        return normalized  # type: ignore[return-value]
    return "disabled"


def _tier_snapshot_stale(tier_policy_service: Any | None) -> bool:
    return bool(getattr(tier_policy_service, "snapshot_stale", False))


def _tier_unavailable_reason(tier_policy_service: Any | None, organization_id: str | None) -> str | None:
    resolver = getattr(tier_policy_service, "resolve_unavailable_decision", None)
    if callable(resolver):
        try:
            decision = resolver(organization_id)
        except Exception:
            return "tier_policy_unavailable"
        reason = _str_or_none(getattr(decision, "reason", None))
        if reason is not None:
            return reason
    return "tier_policy_snapshot_stale"


def _provider_model_info(
    model_info: Mapping[str, Any] | None,
    *,
    fallback_input_cost_per_token: float | None,
    fallback_output_cost_per_token: float | None,
) -> dict[str, Any]:
    info = dict(model_info or {})
    if fallback_input_cost_per_token is not None and info.get("input_cost_per_token") is None:
        info["input_cost_per_token"] = float(fallback_input_cost_per_token)
    if fallback_output_cost_per_token is not None and info.get("output_cost_per_token") is None:
        info["output_cost_per_token"] = float(fallback_output_cost_per_token)
    return info


def _customer_tier_keys(
    tier_policy_service: Any | None,
    organization_id: str | None,
) -> tuple[str, ...]:
    normalized_org = _str_or_none(organization_id)
    if tier_policy_service is None or normalized_org is None:
        return ()
    snapshot_getter = getattr(tier_policy_service, "get_snapshot", None)
    if snapshot_getter is None:
        return ()
    try:
        snapshot = snapshot_getter()
    except Exception:
        return ()
    tier_keys = getattr(snapshot, "org_tier_keys", {}).get(normalized_org, ())
    return tuple(str(key) for key in tier_keys if _str_or_none(key) is not None)


def _fallback_source(model_info: Mapping[str, Any]) -> PricingSource:
    return "deployment" if any(key in model_info and model_info.get(key) is not None for key in _PRICING_KEYS) else "default"


def _billing_metadata(result: BillingResult | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(result, BillingResult):
        metadata: dict[str, Any] = {"cost": result.cost}
        if result.billing_unit is not None:
            metadata["billing_unit"] = result.billing_unit
        if result.pricing_fields_used:
            metadata["pricing_fields_used"] = list(result.pricing_fields_used)
        if result.usage_snapshot:
            metadata["usage_snapshot"] = result.usage_snapshot
        if result.unpriced_reason is not None:
            metadata["unpriced_reason"] = result.unpriced_reason
        return metadata
    return dict(result)


def _normalize_mode(value: str) -> PricingMode:
    return "batch" if str(value or "").strip().lower() == "batch" else "sync"


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
