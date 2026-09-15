"""HTTP composition of spend intent and frozen pricing; batch keeps its own owner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from fastapi import Request

from src.billing.frozen_pricing import freeze_operation_pricing
from src.billing.spend_operations import (
    OperationAttempt,
    OperationHandle,
    OperationPrincipal,
    SpendOperationIntent,
    SpendPersistenceUnavailable,
)
from src.billing.spend_ingestion import SpendIngestionService
from src.billing.tier_pricing import PricingResolution, resolve_deployment_tier_pricing
from src.providers.resolution import resolve_provider
from src.models.responses import UserAPIKeyAuth
from src.router.router import Deployment
from src.telemetry.event_identity import get_or_create_billing_event_id

T = TypeVar("T")


def operation_handle(request: Request) -> OperationHandle | None:
    value = getattr(request.state, "spend_operation_handle", None)
    if value is not None and not isinstance(value, OperationHandle):
        raise SpendPersistenceUnavailable()
    return value


def billing_write_context(request: Request) -> dict[str, object]:
    context: dict[str, object] = {"event_id": get_or_create_billing_event_id(request)}
    handle = operation_handle(request)
    if handle is not None:
        context["operation"] = handle
    return context


def operation_pricing(
    request: Request, *, auth: UserAPIKeyAuth, model: str, deployment: Deployment
) -> PricingResolution:
    snapshots = getattr(request.state, "spend_operation_pricing", {})
    found = snapshots.get((model, deployment.deployment_id))
    if found is not None:
        return found
    return resolve_deployment_tier_pricing(
        auth=auth,
        model=model,
        deployment=deployment,
        tier_policy_service=getattr(request.app.state, "tier_policy_service", None),
        mode="sync",
    )


def _pricing_snapshot(pricing: PricingResolution) -> dict[str, str | int | bool | None]:
    # Only billing dimensions and version identifiers; never arbitrary model_info.
    result: dict[str, str | int | bool | None] = {
        "source": pricing.source,
        "currency": "USD",
        "rounding": "ROUND_HALF_EVEN:1e-18",
        "tier_version_id": pricing.tier_version_id,
        "tier_assignment_id": pricing.tier_assignment_id,
    }
    for view, fields in (
        ("customer", pricing.customer_model_info),
        ("provider", pricing.provider_model_info),
    ):
        for field in (
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_token_cache_hit",
            "output_cost_per_token_cache_hit",
            "input_cost_per_character",
            "output_cost_per_character",
            "input_cost_per_second",
            "output_cost_per_second",
            "input_cost_per_image",
            "output_cost_per_image",
            "input_cost_per_audio_token",
            "output_cost_per_audio_token",
            "cost_per_request",
        ):
            value = fields.get(field)
            if value is not None:
                result[f"{view}.{field}"] = str(value)
    catalog = pricing.catalog_token_pricing
    if catalog is not None:
        for field in (
            "input_cost_per_token",
            "output_cost_per_token",
            "input_cost_per_token_cache_hit",
            "output_cost_per_token_cache_hit",
            "cost_per_request",
        ):
            value = getattr(catalog, field)
            if value is not None:
                result[f"catalog.{field}"] = str(value)
    return result


async def durable_provider_call(
    request: Request,
    *,
    model: str,
    call_type: str,
    deployment: Deployment,
    execute: Callable[[], Awaitable[T]],
) -> T:
    service = getattr(request.app.state, "spend_tracking_service", None)
    if not isinstance(service, SpendIngestionService) or service.operations is None:
        return await execute()
    if not service.worker_health.ready or service._closed:
        raise SpendPersistenceUnavailable()
    auth = request.state.user_api_key
    previous = operation_handle(request)
    pricing = freeze_operation_pricing(
        operation_pricing(request, auth=auth, model=model, deployment=deployment)
    )
    try:
        attempt = OperationAttempt(
            deployment_id=deployment.deployment_id,
            provider=resolve_provider(deployment.deltallm_params),
            model=model,
            pricing=_pricing_snapshot(pricing),
        )
        principal = OperationPrincipal.model_validate(
            {name: getattr(auth, name, None) for name in OperationPrincipal.model_fields}
        )
        intent = SpendOperationIntent(
            principal=principal,
            model=model,
            call_type=call_type,
            started_at=previous.intent.started_at if previous else datetime.now(UTC),
            attempts=(*previous.intent.attempts, attempt) if previous else (attempt,),
        )
        handle = OperationHandle(
            event_id=UUID(get_or_create_billing_event_id(request)),
            owner_token=previous.owner_token if previous else uuid4(),
            intent=intent,
            expires_at=previous.expires_at
            if previous
            else datetime.now(UTC) + timedelta(minutes=15),
        )
    except (ValueError, TypeError):
        raise SpendPersistenceUnavailable() from None
    await service.operations.begin(
        handle,
        capacity=service.config.max_pending_events,
        max_attempts=service.config.max_attempts,
        expires_at=asyncio.get_running_loop().time() + 0.25,
    )
    request.state.spend_operation_handle = handle
    snapshots = getattr(request.state, "spend_operation_pricing", {})
    snapshots[(model, deployment.deployment_id)] = pricing
    request.state.spend_operation_pricing = snapshots
    return await execute()
