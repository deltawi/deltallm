from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from src.middleware.errors import (
    anthropic_proxy_error_response,
    proxy_error_response,
    register_exception_handlers,
)
from src.models.errors import (
    FailureClassification,
    InvalidRequestError,
    RateLimitError,
    ServiceUnavailableError,
)


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


@pytest.mark.parametrize("response_factory", [proxy_error_response, anthropic_proxy_error_response])
def test_rate_limit_response_preserves_zero_retry_after(response_factory) -> None:  # noqa: ANN001
    response = response_factory(RateLimitError(retry_after=0))

    assert response.headers["retry-after"] == "0"


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


@pytest.mark.asyncio
async def test_messages_path_renders_proxy_errors_in_anthropic_dialect() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/v1/messages")
    async def messages():
        raise ServiceUnavailableError(message="Provider unavailable")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/messages")

    assert response.status_code == 503
    assert response.json() == {
        "type": "error",
        "error": {"type": "overloaded_error", "message": "Provider unavailable"},
    }


@pytest.mark.asyncio
async def test_non_messages_http_errors_keep_fastapi_contract() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/ordinary")
    async def ordinary():
        raise HTTPException(status_code=418, detail="ordinary detail", headers={"x-test": "yes"})

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ordinary")

    assert response.status_code == 418
    assert response.json() == {"detail": "ordinary detail"}
    assert response.headers["x-test"] == "yes"
