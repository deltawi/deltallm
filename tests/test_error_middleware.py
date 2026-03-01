from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from src.middleware.errors import register_exception_handlers


@pytest.mark.asyncio
async def test_unhandled_exception_logging_does_not_include_exception_message(caplog):
    sensitive = "api_key=super-secret-value"
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError(sensitive)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level("ERROR"):
            response = await client.get("/boom")

    assert response.status_code == 500
    assert sensitive not in caplog.text
