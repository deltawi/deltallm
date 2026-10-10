from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response
from src.bootstrap.metrics import metrics_snapshot_response

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    service = getattr(request.app.state, "prometheus_snapshot_service", None)
    return metrics_snapshot_response(service)
