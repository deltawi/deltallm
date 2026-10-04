from __future__ import annotations

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
    runtime = getattr(request.app.state, "readiness_runtime", None)
    if runtime is None:
        return {
            "status": "degraded",
            "checks": {"process": False},
            "details": {"process": {"state": "starting"}},
        }
    return await runtime.payload()
