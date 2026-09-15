from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from starlette.datastructures import State

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class FallbackEventResponse(BaseModel):
    timestamp: float
    model_group: str
    from_deployment: str | None
    to_deployment: str | None
    reason: str
    error_classification: str
    error_message: str
    attempt: int
    success: bool


class FallbackEventsResponse(BaseModel):
    events: list[FallbackEventResponse]


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    ready_payload = await _readiness_payload(request)
    status = 200 if ready_payload["status"] == "ok" else 503
    payload = {"liveliness": "ok", "readiness": ready_payload}
    return JSONResponse(status_code=status, content=payload)


@router.get("/health/liveliness")
async def liveliness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/readiness")
async def readiness(request: Request) -> JSONResponse:
    payload = await _readiness_payload(request)
    status = 200 if payload["status"] == "ok" else 503
    return JSONResponse(status_code=status, content=payload)


@router.get("/health/deployments")
async def deployments_health(request: Request, model: str | None = None) -> JSONResponse:
    handler = getattr(request.app.state, "router_health_handler", None)
    if handler is None:
        payload = {
            "status": "healthy",
            "timestamp": 0,
            "healthy_count": 0,
            "total_count": 0,
            "deployments": [],
        }
    else:
        payload = await handler.get_health_status(model_filter=model)

    status_code = 200 if payload["status"] in {"healthy", "degraded"} else 503
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/health/fallback-events", response_model=FallbackEventsResponse)
async def fallback_events(request: Request, limit: int = 50) -> FallbackEventsResponse:
    failover_manager = getattr(request.app.state, "failover_manager", None)
    if failover_manager is None:
        return FallbackEventsResponse(events=[])
    events = failover_manager.get_recent_fallback_events(limit=min(limit, 200))
    return FallbackEventsResponse.model_validate({"events": events})


async def _readiness_payload(request: Request) -> dict[str, object]:
    checks, details = await _dependency_readiness(request.app.state)

    if bool(getattr(request.app.state, "batch_webhook_worker_expected", False)):
        worker_task = getattr(request.app.state, "batch_webhook_outbox_task", None)
        checks["batch_webhook_worker"] = bool(worker_task is not None and not worker_task.done())

    routing_manager = getattr(request.app.state, "model_hot_reload_manager", None)
    routing_state_getter = getattr(routing_manager, "get_applied_routing_state", None)
    if callable(routing_state_getter):
        routing_state = routing_state_getter()
        routing_ready = not bool(routing_state.requires_reconciliation)
        checks["routing_runtime"] = routing_ready
        details["routing_runtime"] = {
            "state": "ready" if routing_ready else "stale",
        }

    spend_service = getattr(request.app.state, "spend_tracking_service", None)
    spend_health = getattr(spend_service, "worker_health", None)
    if spend_health is not None:
        checks["spend_ingestion_worker"] = bool(spend_health.ready)
        details["spend_ingestion_worker"] = _worker_health_payload(spend_health)

    audit_service = getattr(request.app.state, "audit_service", None)
    audit_health = getattr(audit_service, "worker_health", None)
    if audit_health is not None:
        checks["audit_ingestion_worker"] = bool(audit_health.ready)
        details["audit_ingestion_worker"] = _worker_health_payload(audit_health)
    policy_listener_health = getattr(audit_service, "policy_listener_health", None)
    if policy_listener_health is not None:
        # Pub/sub reduces policy-cache staleness but PostgreSQL remains the
        # authoritative privacy decision on every content-bearing write.
        details["audit_policy_listener"] = _worker_health_payload(policy_listener_health)

    budget_notification_worker = getattr(request.app.state, "budget_notification_worker", None)
    if budget_notification_worker is not None:
        # Optional alerts expose degradation without withdrawing inference capacity.
        details["budget_notification_worker"] = _worker_health_payload(
            budget_notification_worker.worker_health
        )
    email_worker = getattr(request.app.state, "email_outbox_worker", None)
    email_worker_health = getattr(email_worker, "worker_health", None)
    if email_worker_health is not None:
        checks["email_outbox_worker"] = bool(email_worker_health.ready)
        details["email_outbox_worker"] = _worker_health_payload(email_worker_health)

    if bool(getattr(request.app.state, "organization_lifecycle_refresher_expected", False)):
        lifecycle_task = getattr(request.app.state, "organization_lifecycle_task", None)
        lifecycle_authorizer = getattr(
            request.app.state,
            "organization_lifecycle_authorizer",
            None,
        )
        checks["organization_lifecycle_refresher"] = bool(
            lifecycle_task is not None
            and not lifecycle_task.done()
            and lifecycle_authorizer is not None
            and lifecycle_authorizer.is_ready()
        )

    if bool(getattr(request.app.state, "organization_deletion_worker_expected", False)):
        deletion_task = getattr(request.app.state, "organization_deletion_task", None)
        deletion_worker = getattr(request.app.state, "organization_deletion_worker", None)
        checks["organization_deletion_worker"] = bool(
            deletion_task is not None
            and not deletion_task.done()
            and deletion_worker is not None
            and deletion_worker.is_ready()
        )

    status = "ok" if all(checks.values()) else "degraded"
    return {"status": status, "checks": checks, "details": details}


async def _dependency_readiness(state: State) -> tuple[dict[str, bool], dict[str, dict[str, str]]]:
    redis_client = getattr(state, "redis", None)
    probes = {"redis": _probe_dependency(redis_client.ping if redis_client is not None else None)}
    databases = {
        "database": "prisma_manager",
        "foreground_database": "foreground_prisma_manager",
    }
    if "outbox" in {
        getattr(state, "spend_ingestion_mode", "legacy"),
        getattr(state, "audit_ingestion_mode", "legacy"),
    }:
        databases.update(
            telemetry_database="telemetry_prisma_manager",
            telemetry_worker_database="telemetry_worker_prisma_manager",
        )
    for name, manager_name in databases.items():
        client = getattr(getattr(state, manager_name, None), "client", None)
        probes[name] = _probe_dependency(
            (lambda db=client: db.query_raw("SELECT 1")) if client is not None else None
        )
    # At most five owned probes, each with the same independent one-second bound.
    # Probe cancellation propagates to the adapters; database owners retain any
    # native work still draining without admitting work beyond their allocation.
    results = await asyncio.gather(*probes.values())
    return (
        {name: result[0] for name, result in zip(probes, results)},
        {name: {"state": result[1]} for name, result in zip(probes, results)},
    )


async def _probe_dependency(
    operation: Callable[[], Awaitable[object]] | None,
) -> tuple[bool, str]:
    if operation is None:
        return False, "unavailable"
    try:
        async with asyncio.timeout(1.0):
            result = await operation()
        return (False, "unavailable") if result is False else (True, "ready")
    except TimeoutError:
        return False, "timeout"
    except Exception:
        return False, "unavailable"


def _worker_health_payload(health: object) -> dict[str, str]:
    payload = {"state": str(getattr(health, "state", "failed"))}
    detail = getattr(health, "detail", None)
    if detail:
        payload["detail"] = str(detail)
    return payload
