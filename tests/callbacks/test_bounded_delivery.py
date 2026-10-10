from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from threading import Event

import pytest

from src.callbacks import CallbackManager, CustomLogger, build_standard_logging_payload
from src.request_work_settings import RequestWorkSettings

pytestmark = [pytest.mark.hermetic, pytest.mark.asyncio]


def payload():
    now = datetime.now(UTC)
    return build_standard_logging_payload(
        call_type="completion",
        request_id="test",
        model="test",
        deployment_model="test",
        request_payload={"messages": [{"role": "user", "content": "original"}]},
        response_obj={"choices": []},
        user_api_key_dict={},
        start_time=now,
        end_time=now,
        api_base=None,
    )


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


async def test_callbacks_bound_task_count_bytes_and_snapshot_request_values():
    manager = CallbackManager(
        RequestWorkSettings(callback_max_pending=2, callback_max_concurrency=1)
    )
    entered, release = asyncio.Event(), asyncio.Event()
    recorded = []

    class Recorder(CustomLogger):
        async def async_log_success_event(self, kwargs, **_):
            recorded.append(kwargs["messages"][0]["content"])
            entered.set()
            await release.wait()

    manager.register_callback(Recorder())
    event = payload()
    try:
        for _ in range(100):
            manager.dispatch_success_callbacks(event)
        event.messages[0]["content"] = "changed"
        await entered.wait()
        assert manager.delivery.pending == 2
        assert 0 < manager.delivery.retained_bytes <= manager.delivery.settings.callback_max_bytes
        release.set()
        await until(lambda: manager.delivery.pending == 0)
        assert recorded == ["original", "original"]
        assert manager.delivery.retained_bytes == 0
    finally:
        release.set()
        await manager.shutdown()


async def test_oversized_event_and_closed_delivery_do_not_create_tasks():
    manager = CallbackManager(RequestWorkSettings(callback_max_payload_bytes=1024))
    manager.register_callback(CustomLogger())
    event = payload()
    event.messages[0]["content"] = "x" * 2048
    manager.dispatch_success_callbacks(event)
    assert manager.delivery.pending == manager.delivery.retained_bytes == 0
    await manager.shutdown()
    manager.dispatch_success_callbacks(payload())
    assert manager.delivery.pending == 0


async def test_existing_keyword_only_callback_contract_is_preserved():
    received = []

    class KeywordOnly(CustomLogger):
        async def async_log_success_event(self, *, kwargs, response_obj, start_time, end_time):
            received.append((kwargs["model"], response_obj, start_time, end_time))

    manager = CallbackManager()
    manager.register_callback(KeywordOnly())
    event = payload()
    try:
        await manager.execute_success_callbacks(event)
        assert received == [(event.model, event.response_obj, event.start_time, event.end_time)]
    finally:
        await manager.shutdown()


async def test_callback_timeout_does_not_free_unfinished_sync_capacity():
    manager = CallbackManager(
        RequestWorkSettings(
            callback_max_concurrency=1,
            callback_timeout_seconds=0.03,
            callback_shutdown_seconds=0.03,
        )
    )
    started, finish = Event(), Event()
    executions = 0

    class Slow(CustomLogger):
        def log_success_event(self, *_):
            nonlocal executions
            executions += 1
            started.set()
            finish.wait(2)

    manager.register_callback(Slow())
    try:
        manager.dispatch_success_callbacks(payload())
        await until(started.is_set)
        await until(lambda: manager.delivery.pending == 0)
        assert manager.delivery.blocking.pending == 1
        manager.dispatch_success_callbacks(payload())
        await until(lambda: manager.delivery.pending == 0)
        assert executions == 1
        async with asyncio.timeout(0.5):
            await manager.shutdown()
        assert manager.delivery.blocking.pending == 1
    finally:
        finish.set()
        await until(lambda: manager.delivery.blocking.pending == 0)
        await manager.shutdown()


async def test_shutdown_does_not_free_cancellation_resistant_callback():
    manager = CallbackManager(RequestWorkSettings(callback_shutdown_seconds=0.02))
    entered, release = asyncio.Event(), asyncio.Event()

    class Resistant(CustomLogger):
        async def async_log_success_event(self, **_):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()

    manager.register_callback(Resistant())
    try:
        manager.dispatch_success_callbacks(payload())
        await entered.wait()
        async with asyncio.timeout(0.5):
            await manager.shutdown()
        assert manager.delivery.pending == 1
        assert manager.delivery.retained_bytes > 0
        manager.dispatch_success_callbacks(payload())
        assert manager.delivery.pending == 1
    finally:
        release.set()
        await until(lambda: manager.delivery.pending == 0)
