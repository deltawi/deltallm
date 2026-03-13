from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import FileResponse

ui_router = APIRouter(tags=["UI"])


def _dist_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "ui" / "dist"


@ui_router.get("/ui")
async def serve_ui_root() -> Response:
    index_file = _dist_dir() / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UI bundle not found. Run: npm --prefix ui run build")
    return FileResponse(index_file)


@ui_router.get("/ui/{path:path}")
async def serve_ui(path: str) -> Response:
    if path.startswith("api/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    dist = _dist_dir()
    if not dist.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UI bundle not found. Run: npm --prefix ui run build")

    requested = (dist / path).resolve()
    try:
        requested.relative_to(dist.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path") from exc

    if requested.exists() and requested.is_file():
        return FileResponse(requested)

    return FileResponse(dist / "index.html")
