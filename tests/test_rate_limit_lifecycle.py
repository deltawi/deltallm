from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.middleware.rate_limit_lifecycle as lifecycle_module
from src.middleware.rate_limit_lifecycle import RateLimitLeaseLifecycleMiddleware


def _http_scope() -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "state": {},
        "app": SimpleNamespace(state=SimpleNamespace()),
    }


async def _receive_disconnect() -> dict[str, object]:
    return {"type": "http.disconnect"}


@pytest.mark.asyncio
async def test_rate_limit_lease_is_released_only_after_final_response_body(monkeypatch) -> None:
    response_started = asyncio.Event()
    finish_response = asyncio.Event()
    releases: list[object] = []
    sent: list[dict[str, object]] = []

    async def release(request):  # noqa: ANN001, ANN202
        releases.append(request)

    async def app(scope, receive, send):  # noqa: ANN001, ANN202
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        response_started.set()
        await finish_response.wait()
        await send({"type": "http.response.body", "body": b"last", "more_body": False})

    async def send(message):  # noqa: ANN001, ANN202
        sent.append(dict(message))

    monkeypatch.setattr(lifecycle_module, "_release_rate_limits", release)
    scope = _http_scope()
    task = asyncio.create_task(
        RateLimitLeaseLifecycleMiddleware(app)(scope, _receive_disconnect, send)
    )

    await response_started.wait()
    await asyncio.sleep(0)
    assert releases == []

    finish_response.set()
    await task

    assert len(releases) == 1
    assert sent[-1] == {
        "type": "http.response.body",
        "body": b"last",
        "more_body": False,
    }
    assert scope["state"]["_rate_limit_lifecycle_managed"] is True  # type: ignore[index]


@pytest.mark.asyncio
async def test_rate_limit_lease_is_released_when_stream_is_cancelled(monkeypatch) -> None:
    response_started = asyncio.Event()
    releases: list[object] = []

    async def release(request):  # noqa: ANN001, ANN202
        releases.append(request)

    async def app(scope, receive, send):  # noqa: ANN001, ANN202
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        response_started.set()
        await asyncio.Event().wait()

    async def send(message):  # noqa: ANN001, ANN202
        del message

    monkeypatch.setattr(lifecycle_module, "_release_rate_limits", release)
    task = asyncio.create_task(
        RateLimitLeaseLifecycleMiddleware(app)(
            _http_scope(),
            _receive_disconnect,
            send,
        )
    )

    await response_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(releases) == 1
