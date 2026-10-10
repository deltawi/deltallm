from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.billing.charges.realtime_pricing import RealtimePrices
from src.billing.pricing.tier_pricing import resolve_deployment_tier_pricing
from src.providers.openai_realtime import OpenAIRealtimeTarget, resolve_realtime_target
from src.realtime.contracts import RealtimeRequest
from src.realtime.errors import RealtimeError
from src.router.candidates import ROUTING_MODE_CONTEXT_KEY
from src.router.initial_selection import require_initial_deployment
from src.router.router import Deployment
from src.router.runtime_generation import RoutingRuntimeGeneration, RoutingRuntimeGenerationStore
from src.services.access.callable_target_grants import CallableTargetGrantService
from src.services.access.model_visibility import ensure_model_allowed
from src.services.tiers.tier_policy_service import TierPolicyService


@dataclass(frozen=True, slots=True)
class RealtimeRoute:
    generation: RoutingRuntimeGeneration
    deployment: Deployment
    target: OpenAIRealtimeTarget
    customer_prices: RealtimePrices
    provider_prices: RealtimePrices
    usage_type: Literal["tokens", "duration"]


@dataclass(frozen=True, slots=True)
class RealtimeRouting:
    generations: RoutingRuntimeGenerationStore
    grants: CallableTargetGrantService
    tiers: TierPolicyService
    default_transcription_model: str | None = None
    policy_mode: str = "enforce"
    tier_policy_mode: str = "disabled"
    tier_missing_service_mode: str = "fail_closed"

    def authorize(self, request: RealtimeRequest, model: str) -> RoutingRuntimeGeneration:
        generation = self.generations.require_snapshot()
        if generation.requires_reconciliation:
            raise RealtimeError("routing_unavailable", "Realtime routing requires reconciliation")
        settings = generation.app_config.general_settings
        policy_mode = (
            settings.callable_target_scope_policy_mode
            if "callable_target_scope_policy_mode" in settings.model_fields_set
            else self.policy_mode
        )
        ensure_model_allowed(
            request.auth,
            model,
            callable_target_grant_service=self.grants,
            callable_target_grant_snapshot=generation.authorization_snapshot,
            creator_model_access_snapshot=generation.creator_model_access_snapshot,
            creator_route_group_access_snapshot=generation.creator_route_group_access_snapshot,
            tier_policy_service=self.tiers,
            policy_mode=policy_mode,
            tier_policy_mode=self.tier_policy_mode,
            tier_policy_missing_service_mode=self.tier_missing_service_mode,
        )
        return generation

    async def resolve(self, request: RealtimeRequest) -> RealtimeRoute:
        model = request.model or (
            self.default_transcription_model if request.profile == "transcription" else None
        )
        if not model:
            raise RealtimeError("missing_model", "No Realtime transcription model is configured")
        generation = self.authorize(request, model)
        router = generation.router
        context = {
            ROUTING_MODE_CONTEXT_KEY: "realtime",
            "realtime_profile": request.profile,
            "metadata": {},
        }
        deployment = await require_initial_deployment(
            router=router,
            failover_manager=generation.failover_manager,
            model_group=router.resolve_model_group(model),
            request_context=context,
        )
        info = deployment.model_info
        if (
            info.get("mode") != "realtime"
            or info.get("realtime_profile", "realtime") != request.profile
        ):
            raise RealtimeError(
                "unsupported_profile", "The deployment does not support this Realtime profile"
            )
        pricing = resolve_deployment_tier_pricing(
            auth=request.auth,
            model=model,
            deployment=deployment,
            tier_policy_service=self.tiers,
        )
        if pricing.tier_policy_service_mode == "enforce" and not pricing.tier_pricing_authoritative:
            raise RealtimeError("pricing_unavailable", "Realtime pricing is unavailable")
        usage_type = str(info.get("realtime_usage_type", "tokens"))
        if usage_type not in {"tokens", "duration"} or (
            request.profile == "realtime" and usage_type != "tokens"
        ):
            raise RealtimeError("pricing_unavailable", "Unsupported Realtime usage profile")
        try:
            customer = RealtimePrices.from_model_info(pricing.customer_model_info)
            provider = RealtimePrices.from_model_info(pricing.provider_model_info)
            for prices in (customer, provider):
                prices.require_profile(
                    transcription=request.profile == "transcription",
                    duration=usage_type == "duration",
                )
        except ValueError:
            raise RealtimeError("pricing_unavailable", "Realtime pricing is unavailable") from None
        return RealtimeRoute(
            generation,
            deployment,
            resolve_realtime_target(
                deployment.deltallm_params, public_model=model, profile=request.profile
            ),
            customer,
            provider,
            usage_type,
        )
