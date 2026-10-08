from __future__ import annotations

from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.ui.config import UIMountSettings
from src.ui.routes import install_ui_fallback, ui_router


@pytest.mark.parametrize("path", ["//evil.test", "/gateway/", "/a/../b", "/a%2fb", "/a?b"])
def test_ui_mount_rejects_unsafe_paths(path):
    with pytest.raises(ValidationError):
        UIMountSettings(mount_path=path, external_console=True)


async def test_one_static_owner_serves_mount_and_protects_api_fallback(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<html><head><script type="module" src="/ui/assets/main.js"></script></head></html>'
    )
    (dist / "assets/main.js").write_text("export const ready = true;")
    (tmp_path / "secret").write_text("private")
    monkeypatch.setattr("src.ui.routes._dist_dir", lambda: dist)
    app = FastAPI()
    app.state.app_config = SimpleNamespace(
        general_settings=SimpleNamespace(
            ui_mount=UIMountSettings(mount_path="/gateway", external_console=True)
        )
    )
    app.include_router(ui_router)
    install_ui_fallback(app)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://gateway.test"
    ) as client:
        for path in ["/ui", "/ui/models/model-id/edit", "/models/model-id/edit"]:
            response = await client.get(path)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert (
                '"external_console":true' in response.text
                and "/gateway/ui/assets/main.js" in response.text
            )
        assert (await client.get("/ui/assets/main.js")).status_code == 200
        for path in [
            "/ui/api/unknown",
            "/auth/unknown",
            "/metrics/unknown",
            "/console/login",
            "/ui/assets/missing.js",
        ]:
            assert (await client.get(path)).status_code == 404
        assert (await client.get("/ui/%2e%2e%2fsecret")).status_code == 400
