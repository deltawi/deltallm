from __future__ import annotations

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


async def _readiness_payload(request: Request) -> dict[str, object]:
    checks: dict[str, bool] = {}

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

    status = "ok" if all(checks.values()) else "degraded"
    return {"status": status, "checks": checks}
