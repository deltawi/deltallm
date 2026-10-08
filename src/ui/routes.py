from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

from src.ui.config import UIMountSettings

ui_router = APIRouter(tags=["UI"], include_in_schema=False)
_RESERVED = ("ui/api", "v1", "auth", "health", "metrics", "spend", "global", "console")


def _dist_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "ui" / "dist"


def _mount(request: Request) -> UIMountSettings:
    configured = getattr(request.app.state, "ui_mount", None)
    if configured is not None:
        return configured
    config = getattr(request.app.state, "app_config", None)
    general = getattr(config, "general_settings", None)
    return getattr(general, "ui_mount", UIMountSettings())


def _index(request: Request) -> Response:
    index = _dist_dir() / "index.html"
    if not index.is_file():
        raise HTTPException(
            status_code=404, detail="UI bundle not found. Run: npm --prefix ui run build"
        )
    mount = _mount(request)
    config = json.dumps(mount.model_dump(), separators=(",", ":"))
    source = index.read_text(encoding="utf-8").replace('"/ui/', f'"{mount.mount_path}/ui/')
    source = source.replace(
        "<head>",
        '<head><script id="deltallm-ui-config" type="application/json">' + config + "</script>",
        1,
    )
    return HTMLResponse(source, headers={"Cache-Control": "no-store"})


def _serve(request: Request, path: str) -> Response:
    if any(path == prefix or path.startswith(prefix + "/") for prefix in _RESERVED):
        raise HTTPException(status_code=404, detail="Not Found")
    dist = _dist_dir().resolve()
    requested = (dist / path).resolve()
    try:
        requested.relative_to(dist)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc
    if requested.name == "index.html" and requested.is_file():
        return _index(request)
    if requested.is_file():
        return FileResponse(requested)
    # Missing static assets must not receive HTML or cache an authentication page.
    if path.startswith(("assets/", "brand/")) or Path(path).suffix:
        raise HTTPException(status_code=404, detail="Not Found")
    return _index(request)


@ui_router.get("/ui")
async def serve_ui_root(request: Request) -> Response:
    return _index(request)


@ui_router.get("/ui/{path:path}")
async def serve_ui(request: Request, path: str) -> Response:
    if path == "api" or path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    return _serve(request, path)


def install_ui_fallback(app: FastAPI) -> None:
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(request: Request, full_path: str) -> Response:
        return _serve(request, full_path)
