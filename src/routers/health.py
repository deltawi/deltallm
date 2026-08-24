from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


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


@router.get("/health/fallback-events")
async def fallback_events(request: Request, limit: int = 50) -> JSONResponse:
    failover_manager = getattr(request.app.state, "failover_manager", None)
    if failover_manager is None:
        return JSONResponse(status_code=200, content={"events": []})
    events = failover_manager.get_recent_fallback_events(limit=min(limit, 200))
    return JSONResponse(status_code=200, content={"events": events})


async def _readiness_payload(request: Request) -> dict[str, object]:
    checks: dict[str, bool] = {}
    details: dict[str, dict[str, str]] = {}

    redis_client = getattr(request.app.state, "redis", None)
    if redis_client is None:
        checks["redis"] = True
    else:
        try:
            checks["redis"] = bool(await redis_client.ping())
        except Exception:
            checks["redis"] = False

    prisma_manager = getattr(request.app.state, "prisma_manager", None)
    prisma_client = getattr(prisma_manager, "client", None)
    if prisma_client is None:
        checks["database"] = True
    else:
        try:
            await prisma_client.query_raw("SELECT 1")
            checks["database"] = True
        except Exception:
            checks["database"] = False

    telemetry_modes = {
        str(getattr(request.app.state, "spend_ingestion_mode", "legacy")),
        str(getattr(request.app.state, "audit_ingestion_mode", "legacy")),
    }
    if "outbox" in telemetry_modes:
        telemetry_manager = getattr(request.app.state, "telemetry_prisma_manager", None)
        telemetry_client = getattr(telemetry_manager, "client", None)
        if telemetry_client is None:
            checks["telemetry_database"] = False
            details["telemetry_database"] = {"state": "unavailable"}
        else:
            try:
                await asyncio.wait_for(telemetry_client.query_raw("SELECT 1"), timeout=1.0)
                checks["telemetry_database"] = True
                details["telemetry_database"] = {"state": "ready"}
            except TimeoutError:
                checks["telemetry_database"] = False
                details["telemetry_database"] = {"state": "timeout"}
            except Exception:
                checks["telemetry_database"] = False
                details["telemetry_database"] = {"state": "unavailable"}

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

    email_worker = getattr(request.app.state, "email_outbox_worker", None)
    email_worker_health = getattr(email_worker, "worker_health", None)
    if email_worker_health is not None:
        checks["email_outbox_worker"] = bool(email_worker_health.ready)
        details["email_outbox_worker"] = _worker_health_payload(email_worker_health)

    status = "ok" if all(checks.values()) else "degraded"
    return {"status": status, "checks": checks, "details": details}


def _worker_health_payload(health: object) -> dict[str, str]:
    payload = {"state": str(getattr(health, "state", "failed"))}
    detail = getattr(health, "detail", None)
    if detail:
        payload["detail"] = str(detail)
    return payload
