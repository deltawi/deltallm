from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from src.billing.cost import BillingResult, ModelPricing, completion_cost, get_model_pricing
from src.billing.pricing import pricing_from_model_info
from src.providers.resolution import resolve_upstream_model

PricingMode = Literal["sync", "batch"]
PricingSource = Literal["tier", "deployment", "default"]
PricingView = Literal["customer", "provider"]
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
    callable_model: str
    provider_model: str
    requested_mode: PricingMode
    source: PricingSource
    customer_model_info: dict[str, Any]
    provider_model_info: dict[str, Any]
    customer_token_pricing: ModelPricing | None
    provider_token_pricing: ModelPricing | None
    customer_pricing_fields: tuple[str, ...] = field(default_factory=tuple)
    provider_pricing_fields: tuple[str, ...] = field(default_factory=tuple)
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
        provider_billing: BillingResult | Mapping[str, Any] | None = None,
        effective_pricing_sources: tuple[PricingSource, ...] | None = None,
        missing_pricing_fields: tuple[str, ...] | None = None,
        pricing_tier: str | None = None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "callable_model": self.callable_model,
            "provider_model": self.provider_model,
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
        if effective_pricing_sources:
            metadata["effective_pricing_sources"] = list(effective_pricing_sources)
        if missing_pricing_fields:
            metadata["missing_pricing_fields"] = list(missing_pricing_fields)
        if pricing_tier is not None:
            metadata["pricing_tier"] = pricing_tier
        if billing is not None:
            metadata["billing"] = _billing_metadata(billing)
            billing_metadata = metadata["billing"]
            if (
                missing_pricing_fields is None
                and billing_metadata.get("missing_pricing_fields")
            ):
                metadata["missing_pricing_fields"] = list(
                    billing_metadata["missing_pricing_fields"]
                )
            metadata["billing_status"] = (
                "unpriced"
                if billing_metadata.get("unpriced_reason") is not None
                else "priced"
            )
        if provider_billing is not None:
            metadata["provider_billing"] = _billing_metadata(provider_billing)
        return metadata


@dataclass(frozen=True, slots=True)
class TokenQuotePricing:
    pricing: ModelPricing | None
    pricing_fields_used: tuple[str, ...] = ()
    pricing_sources_used: tuple[PricingSource, ...] = ()
    missing_pricing_fields: tuple[str, ...] = ()
    unpriced_reason: str | None = None
    request_only: bool = False


@dataclass(frozen=True, slots=True)
class TokenBillingResolution:
    billing: BillingResult
    pricing_sources_used: tuple[PricingSource, ...] = ()
    missing_pricing_fields: tuple[str, ...] = ()


def resolve_token_quote_pricing(
    resolution: PricingResolution,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    prompt_tokens_cached: int = 0,
    cache_hit: bool = False,
    mode: PricingMode | None = None,
    pricing_view: PricingView = "customer",
) -> TokenQuotePricing:
    """Resolve a complete token quote without treating absent rates as zero."""
    requested_mode = mode or resolution.requested_mode
    prompt_tokens = max(0, int(prompt_tokens))
    completion_tokens = max(0, int(completion_tokens))
    cached_prompt_tokens = min(
        prompt_tokens,
        max(0, int(prompt_tokens_cached)),
    )
    if cache_hit and prompt_tokens > 0 and cached_prompt_tokens == 0:
        cached_prompt_tokens = prompt_tokens
    uncached_prompt_tokens = max(0, prompt_tokens - cached_prompt_tokens)
    info = (
        resolution.customer_model_info
        if pricing_view == "customer"
        else resolution.provider_model_info
    )
    request_price = _configured_price(info, "cost_per_request")
    sync_fields_present = any(
        _configured_price(info, field_name) is not None
        for field_name in ("input_cost_per_token", "output_cost_per_token")
    )
    batch_fields_present = any(
        _configured_price(info, field_name) is not None
        for field_name in ("batch_input_cost_per_token", "batch_output_cost_per_token")
    )
    batch_multiplier = _configured_price(info, "batch_price_multiplier")
    cache_fields_present = cache_hit and any(
        _configured_price(info, field_name) is not None
        for field_name in (
            "input_cost_per_token_cache_hit",
            "output_cost_per_token_cache_hit",
        )
    )
    has_metered_usage = prompt_tokens > 0 or completion_tokens > 0

    effective_request_price = float(request_price or 0.0)
    request_fields: list[str] = []
    request_sources: set[PricingSource] = set()
    if request_price is not None:
        request_fields.append("cost_per_request")
        request_sources.add(
            _pricing_field_source_for_view(
                resolution,
                "cost_per_request",
                pricing_view=pricing_view,
            )
        )
    if (
        requested_mode == "batch"
        and not batch_fields_present
        and batch_multiplier is not None
        and request_price is not None
    ):
        effective_request_price *= max(0.0, batch_multiplier)
        request_fields.append("batch_price_multiplier")
        request_sources.add(
            _pricing_field_source_for_view(
                resolution,
                "batch_price_multiplier",
                pricing_view=pricing_view,
            )
        )

    if not has_metered_usage:
        if request_price is None:
            return TokenQuotePricing(
                pricing=None,
                unpriced_reason="missing_usage_for_billing_mode",
            )
        return TokenQuotePricing(
            pricing=ModelPricing(cost_per_request=effective_request_price),
            pricing_fields_used=tuple(request_fields),
            pricing_sources_used=tuple(sorted(request_sources)),
            request_only=True,
        )

    selected_mode_fields_present = sync_fields_present or (
        requested_mode == "batch" and batch_fields_present
    ) or cache_fields_present
    if request_price is not None and not selected_mode_fields_present:
        return TokenQuotePricing(
            pricing=ModelPricing(cost_per_request=effective_request_price),
            pricing_fields_used=tuple(request_fields),
            pricing_sources_used=tuple(sorted(request_sources)),
            request_only=True,
        )

    catalog_pricing = get_model_pricing(resolution.provider_model or model)
    if catalog_pricing is None and resolution.provider_model != model:
        # Azure/custom deployment identifiers are often not catalog model
        # names. Preserve the public-name fallback only when the served model
        # itself cannot be priced.
        catalog_pricing = get_model_pricing(model)
    resolved_rates: dict[str, float] = {}
    fields_used = list(request_fields)
    sources_used = set(request_sources)
    missing_fields: list[str] = []
    cache_rates: dict[str, float] = {}

    def select_regular_rate(
        sync_field: str,
    ) -> tuple[float | None, str, PricingSource | None]:
        selected_field = sync_field
        selected_rate: float | None = None
        selected_source: PricingSource | None = None
        if requested_mode == "batch" and batch_fields_present:
            batch_field = (
                "batch_input_cost_per_token"
                if sync_field == "input_cost_per_token"
                else "batch_output_cost_per_token"
            )
            batch_rate = _configured_price(info, batch_field)
            if batch_rate is not None:
                selected_field = batch_field
                selected_rate = batch_rate
                selected_source = _pricing_field_source_for_view(
                    resolution,
                    batch_field,
                    pricing_view=pricing_view,
                )

        if selected_rate is None:
            sync_rate = _configured_price(info, sync_field)
            if sync_rate is not None:
                selected_rate = sync_rate
                selected_source = _pricing_field_source_for_view(
                    resolution,
                    sync_field,
                    pricing_view=pricing_view,
                )
            elif not sync_fields_present and catalog_pricing is not None:
                selected_rate = float(getattr(catalog_pricing, sync_field))
                selected_source = "default"

        if (
            selected_rate is not None
            and requested_mode == "batch"
            and not batch_fields_present
            and batch_multiplier is not None
        ):
            selected_rate *= max(0.0, batch_multiplier)
            if "batch_price_multiplier" not in fields_used:
                fields_used.append("batch_price_multiplier")
            sources_used.add(
                _pricing_field_source_for_view(
                    resolution,
                    "batch_price_multiplier",
                    pricing_view=pricing_view,
                )
            )
        return selected_rate, selected_field, selected_source

    def select_cache_rate(
        cache_field: str,
        sync_field: str,
    ) -> tuple[float | None, str, PricingSource | None]:
        configured_cache_rate = _configured_price(info, cache_field)
        if configured_cache_rate is not None:
            return (
                configured_cache_rate,
                cache_field,
                _pricing_field_source_for_view(
                    resolution,
                    cache_field,
                    pricing_view=pricing_view,
                ),
            )
        if not sync_fields_present and catalog_pricing is not None:
            catalog_cache_rate = getattr(catalog_pricing, cache_field)
            if catalog_cache_rate is not None:
                return float(catalog_cache_rate), cache_field, "default"
        return select_regular_rate(sync_field)

    def record_rate(
        *,
        target_field: str,
        missing_field: str,
        selected: tuple[float | None, str, PricingSource | None],
        cache_rate: bool = False,
    ) -> None:
        selected_rate, selected_field, selected_source = selected
        if selected_rate is None or selected_source is None:
            missing_fields.append(missing_field)
            return
        if cache_rate:
            cache_rates[target_field] = selected_rate
        else:
            resolved_rates[target_field] = selected_rate
        fields_used.append(selected_field)
        sources_used.add(selected_source)

    if uncached_prompt_tokens > 0:
        record_rate(
            target_field="input_cost_per_token",
            missing_field="input_cost_per_token",
            selected=select_regular_rate("input_cost_per_token"),
        )
    if cached_prompt_tokens > 0:
        record_rate(
            target_field="input_cost_per_token_cache_hit",
            missing_field="input_cost_per_token_cache_hit",
            selected=select_cache_rate(
                "input_cost_per_token_cache_hit",
                "input_cost_per_token",
            ),
            cache_rate=True,
        )
    if completion_tokens > 0:
        if cache_hit:
            selected_output = select_cache_rate(
                "output_cost_per_token_cache_hit",
                "output_cost_per_token",
            )
            output_is_cache_rate = selected_output[1] == "output_cost_per_token_cache_hit"
            record_rate(
                target_field=(
                    "output_cost_per_token_cache_hit"
                    if output_is_cache_rate
                    else "output_cost_per_token"
                ),
                missing_field=(
                    "output_cost_per_token_cache_hit"
                    if output_is_cache_rate
                    else "output_cost_per_token"
                ),
                selected=selected_output,
                cache_rate=output_is_cache_rate,
            )
        else:
            record_rate(
                target_field="output_cost_per_token",
                missing_field="output_cost_per_token",
                selected=select_regular_rate("output_cost_per_token"),
            )

    if missing_fields:
        return TokenQuotePricing(
            pricing=None,
            pricing_fields_used=tuple(dict.fromkeys(fields_used)),
            pricing_sources_used=tuple(sorted(sources_used)),
            missing_pricing_fields=tuple(missing_fields),
            unpriced_reason="no_configured_pricing",
        )

    return TokenQuotePricing(
        pricing=ModelPricing(
            input_cost_per_token=resolved_rates.get("input_cost_per_token", 0.0),
            output_cost_per_token=resolved_rates.get("output_cost_per_token", 0.0),
            input_cost_per_token_cache_hit=cache_rates.get(
                "input_cost_per_token_cache_hit"
            ),
            output_cost_per_token_cache_hit=cache_rates.get(
                "output_cost_per_token_cache_hit"
            ),
            cost_per_request=effective_request_price,
        ),
        pricing_fields_used=tuple(dict.fromkeys(fields_used)),
        pricing_sources_used=tuple(sorted(sources_used)),
    )


def resolve_token_billing_result(
    resolution: PricingResolution,
    *,
    model: str,
    usage: Mapping[str, Any] | None,
    cache_hit: bool = False,
    mode: PricingMode | None = None,
    pricing_view: PricingView = "customer",
) -> TokenBillingResolution:
    """Resolve and calculate a token charge with explicit unpriced state."""
    usage_data = dict(usage or {})
    prompt_tokens = max(0, int(usage_data.get("prompt_tokens", 0) or 0))
    completion_tokens = max(0, int(usage_data.get("completion_tokens", 0) or 0))
    prompt_tokens_cached = max(
        0,
        int(usage_data.get("prompt_tokens_cached", 0) or 0),
    )
    if cache_hit and prompt_tokens > 0 and prompt_tokens_cached == 0:
        prompt_tokens_cached = prompt_tokens
        usage_data["prompt_tokens_cached"] = prompt_tokens_cached
    quote = resolve_token_quote_pricing(
        resolution,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_tokens_cached=prompt_tokens_cached,
        cache_hit=cache_hit,
        mode=mode,
        pricing_view=pricing_view,
    )
    usage_snapshot = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if prompt_tokens_cached > 0:
        usage_snapshot["prompt_tokens_cached"] = min(
            prompt_tokens,
            prompt_tokens_cached,
        )
    if quote.pricing is None:
        return TokenBillingResolution(
            billing=BillingResult(
                cost=0.0,
                pricing_fields_used=quote.pricing_fields_used,
                missing_pricing_fields=quote.missing_pricing_fields,
                usage_snapshot=usage_snapshot,
                unpriced_reason=quote.unpriced_reason or "no_configured_pricing",
            ),
            pricing_sources_used=quote.pricing_sources_used,
            missing_pricing_fields=quote.missing_pricing_fields,
        )
    return TokenBillingResolution(
        billing=BillingResult(
            cost=completion_cost(
                model=model,
                usage=usage_data,
                cache_hit=cache_hit,
                custom_pricing=quote.pricing,
            ),
            billing_unit="request" if quote.request_only else "token",
            pricing_fields_used=quote.pricing_fields_used,
            usage_snapshot=usage_snapshot,
        ),
        pricing_sources_used=quote.pricing_sources_used,
    )


def resolve_tier_pricing(
    *,
    auth: Any,
    model: str,
    provider_model: str | None = None,
    tier_policy_service: Any | None,
    deployment_model_info: Mapping[str, Any] | None = None,
    fallback_input_cost_per_token: float | None = None,
    fallback_output_cost_per_token: float | None = None,
    mode: PricingMode = "sync",
) -> PricingResolution:
    requested_mode = _normalize_mode(mode)
    callable_model = str(model or "").strip()
    catalog_model = str(provider_model or callable_model).strip() or callable_model
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
    provider_pricing_fields = _configured_pricing_fields(provider_model_info)
    customer_model_info = dict(provider_model_info)
    tier_pricing_fields = tuple(
        sorted(str(key) for key in getattr(tier_policy, "pricing", {}) or {})
    )
    if tier_pricing_applied:
        customer_model_info.update(dict(getattr(tier_policy, "pricing", {}) or {}))
    customer_pricing_fields = tuple(
        sorted(
            set(provider_pricing_fields)
            | (set(tier_pricing_fields) if tier_pricing_applied else set())
        )
    )

    source: PricingSource = "tier" if tier_pricing_applied else _fallback_source(provider_model_info)
    customer_tier_keys = (
        _customer_tier_keys(tier_policy_service, organization_id)
        if tier_policy_service_mode != "disabled"
        else ()
    )
    source_record = getattr(tier_policy, "source", None)

    return PricingResolution(
        callable_model=callable_model,
        provider_model=catalog_model,
        requested_mode=requested_mode,
        source=source,
        customer_model_info=customer_model_info,
        provider_model_info=provider_model_info,
        customer_token_pricing=pricing_from_model_info(customer_model_info),
        provider_token_pricing=pricing_from_model_info(provider_model_info),
        customer_pricing_fields=customer_pricing_fields,
        provider_pricing_fields=provider_pricing_fields,
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
        tier_pricing_fields=tier_pricing_fields,
    )


def resolve_deployment_tier_pricing(
    *,
    auth: Any,
    model: str,
    deployment: Any,
    tier_policy_service: Any | None,
    mode: PricingMode = "sync",
) -> PricingResolution:
    model_info = getattr(deployment, "model_info", None)
    provider_model = resolve_upstream_model(
        getattr(deployment, "deltallm_params", None),
        fallback_model=model,
    )
    return resolve_tier_pricing(
        auth=auth,
        model=model,
        provider_model=provider_model,
        tier_policy_service=tier_policy_service,
        deployment_model_info=model_info,
        fallback_input_cost_per_token=_legacy_deployment_token_price(
            deployment,
            model_info=model_info,
            field_name="input_cost_per_token",
        ),
        fallback_output_cost_per_token=_legacy_deployment_token_price(
            deployment,
            model_info=model_info,
            field_name="output_cost_per_token",
        ),
        mode=mode,
    )


def attach_pricing_metadata(
    metadata: Mapping[str, Any] | None,
    resolution: PricingResolution,
    *,
    provider_cost: float | None = None,
    billing: BillingResult | Mapping[str, Any] | None = None,
    provider_billing: BillingResult | Mapping[str, Any] | None = None,
    effective_pricing_sources: tuple[PricingSource, ...] | None = None,
    missing_pricing_fields: tuple[str, ...] | None = None,
    pricing_tier: str | None = None,
) -> dict[str, Any]:
    merged = dict(metadata or {})
    existing_billing = merged.get("billing") if isinstance(merged.get("billing"), Mapping) else None
    merged.update(
        resolution.spend_metadata(
            provider_cost=provider_cost,
            billing=billing if billing is not None else existing_billing,
            provider_billing=provider_billing,
            effective_pricing_sources=effective_pricing_sources,
            missing_pricing_fields=missing_pricing_fields,
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


def _configured_pricing_fields(model_info: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        sorted(
            key
            for key in _PRICING_KEYS
            if key in model_info and model_info.get(key) is not None
        )
    )


def _configured_price(model_info: Mapping[str, Any], field_name: str) -> float | None:
    if field_name not in model_info or model_info.get(field_name) is None:
        return None
    try:
        return float(model_info[field_name])
    except (TypeError, ValueError):
        return None


def _pricing_field_source(
    resolution: PricingResolution,
    field_name: str,
) -> PricingSource:
    if resolution.tier_pricing_applied and field_name in resolution.tier_pricing_fields:
        return "tier"
    if field_name in resolution.provider_pricing_fields:
        return "deployment"
    return "default"


def _pricing_field_source_for_view(
    resolution: PricingResolution,
    field_name: str,
    *,
    pricing_view: PricingView,
) -> PricingSource:
    if pricing_view == "provider":
        return (
            "deployment"
            if field_name in resolution.provider_pricing_fields
            else "default"
        )
    return _pricing_field_source(resolution, field_name)


def _legacy_deployment_token_price(
    deployment: Any,
    *,
    model_info: Mapping[str, Any] | None,
    field_name: str,
) -> float | None:
    if isinstance(model_info, Mapping) and model_info.get(field_name) is not None:
        return None
    value = getattr(deployment, field_name, None)
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    # Deployment defaults use 0.0 even when no price was configured. A non-zero
    # dataclass-only value is retained for older hand-built deployment objects;
    # explicit zero prices must be represented in model_info.
    return parsed if parsed not in (None, 0.0) else None


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
        if result.missing_pricing_fields:
            metadata["missing_pricing_fields"] = list(result.missing_pricing_fields)
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
