from __future__ import annotations

import asyncio
from threading import Event, get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.callbacks import CallbackManager, CustomLogger
from src.callbacks.integrations import opentelemetry as otel
from src.callbacks.integrations.s3 import S3Callback
from src.request_work_settings import RequestWorkSettings
from tests.callbacks.test_bounded_delivery import payload, until

pytestmark = [pytest.mark.hermetic, pytest.mark.asyncio]


async def test_retirement_waits_for_real_sdk_use_then_closes_once_off_loop():
    manager = CallbackManager(RequestWorkSettings(callback_max_concurrency=2))
    entered, finish = Event(), Event()
    closed = []
    event_loop_thread = get_ident()

    class Client(CustomLogger):
        def log_success_event(self, *_):
            entered.set()
            finish.wait(2)

        def close(self):
            assert finish.is_set()
            closed.append(get_ident())

    handler = Client()
    manager.register_callback(handler)
    try:
        manager.dispatch_success_callbacks(payload())
        await until(entered.is_set)
        manager.delivery.resources.retire(handler)
        await asyncio.sleep(0)
        assert not closed
        finish.set()
        await until(lambda: manager.delivery.resources.pending == 0)
        assert len(closed) == 1 and closed[0] != event_loop_thread
        # A queued callback must never reinitialize a client after retirement.
        await manager.execute_success_callbacks(payload())
        assert len(closed) == 1
    finally:
        finish.set()
        await manager.shutdown()


async def test_failed_sdk_close_retains_allocation_and_bounds_repeated_reload():
    manager = CallbackManager()
    resources = manager.delivery.resources

    class Broken(CustomLogger):
        def close(self):
            raise RuntimeError("SDK close failed")

    try:
        for _ in range(resources.MAX_HANDLERS):
            handler = Broken()
            resources.bind(handler)
            resources.retire(handler)
        await until(lambda: not resources._tasks and not manager.delivery.blocking.pending)
        assert resources.pending == resources.MAX_HANDLERS
        with pytest.raises(ValueError, match="resources are full"):
            resources.bind(Broken())
    finally:
        await manager.shutdown()


async def test_shutdown_deadline_retains_stuck_close_until_real_completion():
    manager = CallbackManager(RequestWorkSettings(callback_shutdown_seconds=0.03))
    entered, finish = Event(), Event()

    class Stuck(CustomLogger):
        def close(self):
            entered.set()
            finish.wait(2)

    manager.register_callback(Stuck())
    try:
        async with asyncio.timeout(1):
            await manager.shutdown()
        assert entered.is_set()
        assert manager.delivery.resources.pending == manager.delivery.blocking.pending == 1
    finally:
        finish.set()
        await until(lambda: manager.delivery.resources.pending == 0)


@pytest.mark.parametrize("failed", [False, True])
async def test_otel_exports_off_loop_without_sdk_queue_and_ends_span_once(monkeypatch, failed):
    loop_thread = get_ident()
    span = Mock()
    export_threads = []
    span.end.side_effect = lambda **_: export_threads.append(get_ident())
    tracer = Mock()
    tracer.start_span.return_value = span
    provider = Mock()
    provider.get_tracer.return_value = tracer
    factory = Mock(return_value=provider)
    exporter = Mock()
    processor = Mock()
    for key, value in {
        "OPENTELEMETRY_AVAILABLE": True,
        "TracerProvider": factory,
        "OTLPSpanExporter": exporter,
        "SimpleSpanProcessor": processor,
        "Resource": Mock(),
        "Status": Mock(),
        "StatusCode": SimpleNamespace(OK=1, ERROR=2),
        "TraceContextTextMapPropagator": Mock(),
    }.items():
        monkeypatch.setattr(otel, key, value, raising=False)
    manager = CallbackManager()
    handler = otel.OpenTelemetryCallback(endpoint="https://collector.example/v1/traces")
    manager.register_callback(handler, "both")
    event = payload()
    try:
        if failed:
            await manager.execute_failure_callbacks(event, RuntimeError("private provider error"))
        else:
            await manager.execute_success_callbacks(event)
        factory.assert_called_once()
        assert factory.call_args.kwargs["shutdown_on_exit"] is False
        assert exporter.call_args.kwargs["timeout"] == 5
        processor.assert_called_once_with(exporter.return_value)
        assert tracer.start_span.call_args.kwargs["start_time"] == int(
            event.start_time.timestamp() * 1e9
        )
        span.end.assert_called_once_with(end_time=int(event.end_time.timestamp() * 1e9))
        assert len(export_threads) == 1 and export_threads[0] != loop_thread
        if failed:
            assert str(span.record_exception.call_args.args[0]) == "Gateway request failed"
    finally:
        await manager.shutdown()
    provider.shutdown.assert_called_once()


async def test_s3_owned_client_closes_on_shutdown_without_creating_unused_client():
    manager = CallbackManager()
    initialized = S3Callback(bucket="test")
    initialized._s3 = Mock()
    client = initialized._s3
    manager.register_callback(initialized)
    manager.register_callback(S3Callback(bucket="never-used"))
    await manager.shutdown()
    client.close.assert_called_once()


async def test_s3_uncompressed_delivery_uses_valid_sdk_parameters():
    import boto3
    from botocore.stub import ANY, Stubber

    sdk = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="local-test",
        aws_secret_access_key="local-test",
    )
    handler = S3Callback(bucket="test-bucket")
    handler._s3 = sdk
    manager = CallbackManager()
    manager.register_callback(handler)
    with Stubber(sdk) as stubber:
        stubber.add_response(
            "put_object",
            {},
            {
                "Bucket": "test-bucket",
                "Key": ANY,
                "Body": ANY,
                "ContentType": "application/json",
                "Metadata": ANY,
            },
        )
        try:
            await manager.execute_success_callbacks(payload())
            stubber.assert_no_pending_responses()
        finally:
            await manager.shutdown()
