from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.external_auth import router
from src.auth.external_client import ExternalClientResolver
from src.auth.external_config import ExternalAuthSettings
from src.auth.external_errors import InvalidExternalAssertion, ExternalAuthError
from src.concurrency import BoundedCapacityGate
from src.models.external_auth import ExternalExchangeResponse
from src.services.identity.external.external_auth_runtime import ExternalAuthRuntime


class Runtime(ExternalAuthRuntime):
    def __init__(self):
        self.ingress = BoundedCapacityGate(concurrency=4, max_waiters=0)
        self.client_resolver = ExternalClientResolver(ExternalAuthSettings())
        self.calls = []
        self.exchange = SimpleNamespace(exchange=self.exchange_result)
        self.revocation = SimpleNamespace(revoke=self.revoke_result)

    @asynccontextmanager
    async def operation(self, name):
        yield

    async def assertion_operation(self, token, correlation, *, purposes, operation):
        self.calls.append((token, correlation, purposes))
        if token == "invalid":
            raise InvalidExternalAssertion()
        if token == "limited":
            raise ExternalAuthError("external_auth_rate_limited", status_code=429)
        return await operation(token)

    async def exchange_result(self, assertion, correlation):
        return ExternalExchangeResponse(
            session_token="psk_ext1_backend_secret",
            session_generation=1,
            account_id="account",
            organization_id="org",
            team_id="team",
            inference_user_id="runtime",
            binding_id="binding",
            expires_at=datetime.now(UTC) + timedelta(seconds=300),
            refresh_after_seconds=240,
            next_step="ready",
            mfa_required=False,
        )

    async def revoke_result(self, assertion, correlation):
        return None


@pytest.fixture
async def backend():
    app = FastAPI()
    app.state.external_auth_runtime = Runtime()
    app.include_router(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://gateway.test"
    ) as client:
        yield client, app.state.external_auth_runtime


@pytest.mark.asyncio
async def test_session_is_returned_only_in_uncached_backend_response(backend):
    client, runtime = backend
    response = await client.post(
        "/auth/external/exchange",
        json={"assertion": "valid"},
        headers={"X-Correlation-ID": "console-request-1"},
    )
    assert (
        response.status_code == 200
        and response.json()["session_token"] == "psk_ext1_backend_secret"
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-correlation-id"] == "console-request-1"
    assert (
        "set-cookie" not in response.headers
        and "access-control-allow-origin" not in response.headers
    )
    assert runtime.calls[0][1] == "console-request-1" and runtime.ingress.active == 0


@pytest.mark.parametrize(
    "headers", [{"Origin": "https://console.example.com"}, {"Cookie": "deltallm_session=anything"}]
)
@pytest.mark.asyncio
async def test_browser_exchange_is_denied_before_signature_work(backend, headers):
    client, runtime = backend
    response = await client.post(
        "/auth/external/exchange", json={"assertion": "valid"}, headers=headers
    )
    assert (
        response.status_code == 403 and not runtime.calls and "set-cookie" not in response.headers
    )


@pytest.mark.parametrize(
    "body,status",
    [
        (b'{"assertion":"invalid"}', 401),
        (b'{"assertion":"limited"}', 429),
        (b'{"assertion":"secret","assertion":"second"}', 400),
        (b'{"assertion":"secret","role":"admin"}', 400),
        (b'{"assertion":123}', 400),
        (b'{"assertion": "' + b"s" * 17000 + b'"}', 413),
        (b"[]", 400),
        (b"bad-json-secret", 400),
    ],
)
@pytest.mark.asyncio
async def test_invalid_input_is_bounded_redacted_and_uncached(backend, body, status):
    client, runtime = backend
    response = await client.post(
        "/auth/external/exchange", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "secret" not in response.text and "second" not in response.text
    assert runtime.ingress.active == 0
    if status == 429:
        assert int(response.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_ingress_overflow_does_not_read_request_body(backend):
    client, runtime = backend
    for _ in range(4):
        await runtime.ingress.acquire(timeout_seconds=0.01)
    consumed = False

    async def stream():
        nonlocal consumed
        consumed = True
        yield b'{"assertion":"valid"}'

    try:
        response = await client.post(
            "/auth/external/exchange",
            content=stream(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 503 and not consumed and not runtime.calls
        assert response.headers["retry-after"] == "1"
    finally:
        for _ in range(4):
            await runtime.ingress.release()


@pytest.mark.asyncio
async def test_revoke_accepts_backend_purposes_and_has_empty_body(backend):
    client, runtime = backend
    response = await client.post("/auth/external/revoke", json={"assertion": "valid"})
    assert response.status_code == 204 and not response.content
    assert {str(purpose) for purpose in runtime.calls[0][2]} == {
        "gateway_session_revoke",
        "gateway_subject_suspend",
    }


def test_openapi_documents_bounded_strict_requests_without_eager_body_parsing():
    from src.api.admin.endpoints.external_auth import router as admin_router

    app = FastAPI()
    app.include_router(router)
    app.include_router(admin_router)
    paths = app.openapi()["paths"]
    for path, name, limit in [
        ("/auth/external/exchange", "assertion", 8192),
        ("/auth/external/revoke", "assertion", 8192),
        ("/auth/external/inference-key", "api_key", 256),
        ("/ui/api/external-auth/subjects/link", "assertion", 8192),
    ]:
        body = paths[path]["post"]["requestBody"]
        assert body["required"]
        schema = body["content"]["application/json"]["schema"]
        assert schema["additionalProperties"] is False
        assert schema["properties"][name]["maxLength"] == limit
        route = next(item for item in app.routes if getattr(item, "path", None) == path)
        assert route.body_field is None
