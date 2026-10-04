import httpx
import pytest
from fastapi import FastAPI

from src.ui import routes


@pytest.mark.parametrize("path", ["/", "/ui", "/ui/settings/keys", "/settings/keys", "/login"])
async def test_single_bundle_owner_preserves_root_and_nested_refresh(tmp_path, monkeypatch, path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("fixture application")
    monkeypatch.setattr(routes, "_dist_dir", lambda: tmp_path)
    app = FastAPI()
    app.include_router(routes.ui_router)
    routes.mount_ui_bundle(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://fixture"
    ) as client:
        response = await client.get(path)
        assert response.status_code == 200
        assert response.text == "fixture application"
        assert (await client.get("/ui/api/missing")).status_code == 404
        assert (await client.get("/v1/missing")).status_code == 404
        assert (await client.get("/%2e%2e/RULES.md")).status_code == 400
