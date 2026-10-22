from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from prisma.errors import RawQueryError

from src.db.catalog.managed_assets import ManagedAssetAudienceNotFoundError
from src.middleware.errors import (
    anthropic_proxy_error_response,
    proxy_error_response,
    register_exception_handlers,
)
from src.providers.base import map_standard_provider_status_error, sanitize_provider_proxy_error
from src.models.errors import (
    AuthenticationUnavailableError,
    FailureClassification,
    InvalidRequestError,
    RateLimitError,
    ServiceUnavailableError,
)


@pytest.mark.parametrize(
    "code,message,expected",
    [
        ("55000", "accounting_budget_policy_requires_drain", 409),
        ("55000", "secret internal failure", 500),
        ("P0001", "accounting_budget_policy_requires_drain", 500),
        ("P0001", "accounting_budget_policy_below_debits", 409),
        ("55000", "accounting_budget_policy_below_debits", 500),
    ],
)
async def test_only_known_budget_policy_conflicts_are_exposed(code, message, expected):
    app = FastAPI()
    register_exception_handlers(app)

    @app.put("/ui/api/organizations/org")
    async def edit():
        raise RawQueryError(
            {
                "user_facing_error": {
                    "error_code": "P2010",
                    "meta": {"code": code, "message": message},
                }
            }
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as client:
        response = await client.put("/ui/api/organizations/org")
    assert response.status_code == expected
    assert "secret internal failure" not in response.text
    if expected == 409:
        assert response.json()["code"] == message.removeprefix("accounting_")


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
async def test_unhandled_exception_logging_names_route_type_and_code_location(caplog):
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/items/{item_id}")
    async def boom(item_id: str):
        try:
            raise KeyError(item_id)
        except KeyError as exc:
            raise RuntimeError("api_key=super-secret-value") from exc

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level("ERROR"):
            response = await client.post("/items/private-id", headers={"x-request-id": "req-123"})

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "req-123"
    record = next(r for r in caplog.records if r.getMessage().startswith("unhandled exception"))
    message = record.getMessage()
    assert "POST /items/{item_id}" in message
    assert "request_id=req-123" in message
    assert "error_type=builtins.RuntimeError" in message
    assert "cause_type=KeyError" in message
    assert "raised_at=test_error_middleware.py:" in message
    assert "in boom" in message
    assert "super-secret-value" not in caplog.text
    assert "private-id" not in caplog.text
    assert record.exc_info is None


@pytest.mark.asyncio
async def test_unhandled_exception_in_full_app_returns_logged_request_id(caplog):
    from src.main import create_app

    app = create_app(include_routes=False)

    @app.post("/ui/api/things/{thing_id}")
    async def boom(thing_id: str):
        raise RuntimeError("api_key=super-secret-value")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level("ERROR"):
            response = await client.post("/ui/api/things/private-id")

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    message = next(
        r.getMessage() for r in caplog.records if r.getMessage().startswith("unhandled exception")
    )
    assert f"request_id={request_id}" in message
    assert "cause_type" not in message


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


@pytest.mark.asyncio
async def test_missing_managed_asset_audience_is_a_client_validation_error() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/access")
    async def update_access():
        raise ManagedAssetAudienceNotFoundError(
            "Selected team audience does not exist: missing-team"
        )

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/access")

    assert response.status_code == 400
    assert response.json() == {"detail": "Selected team audience does not exist: missing-team"}


@pytest.mark.parametrize("response_factory", [proxy_error_response, anthropic_proxy_error_response])
@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_provider_5xx_hint_is_not_sent_to_clients(response_factory, status_code):
    error = sanitize_provider_proxy_error(
        map_standard_provider_status_error(status_code, retry_after_header="3600")
    )

    response = response_factory(error)

    assert response.status_code == 503
    assert "retry-after" not in response.headers
    assert error.retry_after == 3600


@pytest.mark.parametrize("response_factory", [proxy_error_response, anthropic_proxy_error_response])
def test_authentication_unavailable_response_keeps_short_retry_hint(response_factory):
    response = response_factory(AuthenticationUnavailableError())

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"


@pytest.mark.parametrize("response_factory", [proxy_error_response, anthropic_proxy_error_response])
def test_output_usage_unknown_response_keeps_gateway_retry_hint(response_factory):
    from src.services.admission.output_limit_redis import OutputUsageUnknownError
    from src.services.admission.output_limit_types import OutputPolicy, OutputScope

    policy = OutputPolicy((OutputScope("key_output_tpm", "test-key", 100),))
    response = response_factory(OutputUsageUnknownError(policy, 0, reset=120, now=90))

    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
