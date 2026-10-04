from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ui_router = APIRouter(tags=["UI"])


def _dist_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "ui" / "dist"


@ui_router.get("/ui")
async def serve_ui_root() -> Response:
    index_file = _dist_dir() / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="UI bundle not found. Run: npm --prefix ui run build",
        )
    return FileResponse(index_file)


@ui_router.get("/ui/{path:path}")
async def serve_ui(path: str) -> Response:
    if path.startswith("api/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return _bundle_response(path)


def _bundle_response(path: str) -> Response:
    dist = _dist_dir()
    if not dist.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="UI bundle not found. Run: npm --prefix ui run build",
        )

    requested = (dist / path).resolve()
    try:
        requested.relative_to(dist.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path") from exc

    if requested.exists() and requested.is_file():
        return FileResponse(requested)

    return FileResponse(dist / "index.html")


async def serve_spa(request: Request, full_path: str):
    del request
    if full_path.startswith(("ui/api/", "v1/", "auth/", "health/")):
        raise HTTPException(status_code=404, detail="Not Found")
    return _bundle_response(full_path)


def mount_ui_bundle(app: FastAPI) -> None:
    """Register the root fallback last, after the API and legacy /ui routes."""
    dist = _dist_dir()
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=str(dist / "assets")), name="ui-assets")
        app.get("/{full_path:path}")(serve_spa)
