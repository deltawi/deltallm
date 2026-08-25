from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from src.middleware.errors import proxy_error_response, register_exception_handlers
from src.models.errors import FailureClassification, InvalidRequestError


def test_provider_failure_classification_is_not_serialized() -> None:
    response = proxy_error_response(
        InvalidRequestError(
            message="Provider rejected request",
            failure_classification=FailureClassification.CONTEXT_WINDOW,
        )
    )

    payload = json.loads(response.body)

    assert payload == {
        "error": {
            "message": "Provider rejected request",
            "type": "invalid_request_error",
            "param": None,
            "code": None,
        }
    }


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
