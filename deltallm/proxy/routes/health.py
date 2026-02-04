"""Health check routes."""

from fastapi import APIRouter, Depends, Request

from deltallm.router import Router

router = APIRouter(tags=["health"])


def get_router(request: Request) -> Router:
    """Get the router from app state."""
    return request.app.state.router


@router.get("/health")
async def health_check():
    """Basic health check."""
    return {
        "status": "healthy",
        "version": "0.1.0",
    }


@router.get("/health/readiness")
async def readiness_check():
    """Readiness probe for Kubernetes."""
    return {
        "status": "ready",
    }


@router.get("/health/liveness")
async def liveness_check():
    """Liveness probe for Kubernetes."""
    return {
        "status": "alive",
    }


@router.get("/health/detailed")
async def detailed_health(
    router: Router = Depends(get_router),
):
    """Detailed health check with deployment stats."""
    return {
        "status": "healthy",
        "version": "0.1.0",
        "deployments": router.get_deployment_stats(),
    }
