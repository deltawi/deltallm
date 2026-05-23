from __future__ import annotations

import logging

import httpx
import pytest
from pydantic import SecretStr

from src.notifications.webhook import post_webhook

_SECRET_URL = "https://hooks.slack.com/services/T000/B000/XXXXSECRETXXXX"


@pytest.mark.asyncio
async def test_post_webhook_success() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await post_webhook(url=SecretStr(_SECRET_URL), json_body={"text": "hi"}, client=client)

    assert result.ok is True
    assert result.status_code == 200
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_post_webhook_retries_once_on_server_error(caplog: pytest.LogCaptureFixture) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    caplog.set_level(logging.WARNING)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await post_webhook(url=SecretStr(_SECRET_URL), json_body={"text": "hi"}, client=client)

    assert result.ok is False
    assert result.error == "http_500"
    assert calls["n"] == 2
    # the secret webhook URL must never be logged
    assert _SECRET_URL not in caplog.text


@pytest.mark.asyncio
async def test_post_webhook_never_raises_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await post_webhook(url=SecretStr(_SECRET_URL), json_body={"text": "hi"}, client=client)

    assert result.ok is False
    assert result.error == "ConnectError"
