from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.performance import gateway_concurrency_dependencies as dependencies
from tests.performance import gateway_concurrency_metrics as metrics
from tests.performance import run_gateway_concurrency as workload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload", [(403, {"error": {"message": "private"}}), (200, {"invalid": True})]
)
async def test_invalid_workload_precheck_closes_clients_without_recording_load(
    tmp_path, monkeypatch, status, payload
):
    closed = asyncio.Event()
    database, redis = SimpleNamespace(query_raw=AsyncMock()), SimpleNamespace(info=AsyncMock())

    @asynccontextmanager
    async def local_dependencies():
        try:
            yield SimpleNamespace(database=database, redis=redis)
        finally:
            closed.set()

    real_client = httpx.AsyncClient
    client = real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    )
    generator = AsyncMock()
    monkeypatch.setattr(workload, "local_dependencies", local_dependencies)
    monkeypatch.setattr(workload, "read_manifest", lambda path: SimpleNamespace(api_processes=1))
    monkeypatch.setattr(workload, "fixture_key", lambda: "sk-concurrency-private-test")
    monkeypatch.setattr(workload.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(workload, "run_constant_arrival", generator)
    with pytest.raises(ValueError, match="Workload precheck failed") as error:
        await workload.measure(
            SimpleNamespace(
                rate=100,
                duration=10,
                server_manifest=tmp_path / "manifest.json",
                url="http://127.0.0.1/v1/chat/completions",
                metrics_url=["http://127.0.0.1/metrics"],
                output_dir=tmp_path / "results",
            )
        )
    assert "private" not in str(error.value)
    assert closed.is_set() and client.is_closed
    generator.assert_not_called()
    database.query_raw.assert_not_called()
    redis.info.assert_not_called()
    assert not list((tmp_path / "results").iterdir())


class Database:
    def __init__(self, *, fail: bool = False, block: bool = False) -> None:
        self.fail = fail
        self.block = block
        self.entered = asyncio.Event()
        self.closed = False

    async def connect(self, *, timeout: timedelta) -> None:
        assert timeout.total_seconds() > 0
        self.entered.set()
        if self.fail:
            raise OSError("connect failed")
        if self.block:
            await asyncio.Event().wait()

    async def disconnect(self, *, timeout: timedelta) -> None:
        assert timeout.total_seconds() > 0
        self.closed = True


@pytest.fixture
def local_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:fixture@127.0.0.1/deltallm_concurrency")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1/0")


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_urls")
async def test_failed_database_connect_still_closes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(fail=True)
    monkeypatch.setattr(dependencies, "Prisma", lambda **kwargs: db)
    with pytest.raises(OSError, match="connect failed"):
        async with dependencies.local_database():
            pytest.fail("Failed connect must not yield")
    assert db.closed


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_urls")
async def test_cancelled_connect_still_closes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    db = Database(block=True)
    monkeypatch.setattr(dependencies, "Prisma", lambda **kwargs: db)

    async def connect() -> None:
        async with dependencies.local_database():
            pytest.fail("Cancelled connect must not yield")

    task = asyncio.create_task(connect())
    try:
        await asyncio.wait_for(db.entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert db.closed
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.usefixtures("local_urls")
async def test_failed_redis_close_does_not_skip_database_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = Database()
    monkeypatch.setattr(dependencies, "Prisma", lambda **kwargs: db)

    class Redis:
        async def aclose(self) -> None:
            raise OSError("close failed")

    monkeypatch.setattr(dependencies.Redis, "from_url", lambda *args, **kwargs: Redis())
    with pytest.raises(OSError, match="close failed"):
        async with dependencies.local_dependencies() as clients:
            assert clients.database is db
    assert db.closed


@pytest.mark.asyncio
async def test_metrics_client_constructor_failure_closes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(**kwargs: object) -> None:
        raise OSError("client construction failed")

    monkeypatch.setattr(metrics.httpx, "AsyncClient", fail)
    recorder = metrics.MetricsRecorder(["http://127.0.0.1/metrics"], tmp_path / "metrics.jsonl")
    with pytest.raises(OSError):
        async with recorder:
            pytest.fail("Failed constructor must not yield")
    assert recorder._file is not None and recorder._file.closed


@pytest.mark.asyncio
async def test_failed_metrics_export_stops_workload_and_closes_everything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        metrics.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="deltallm_event_loop_samplers 1\n")
            ),
            **kwargs,
        ),
    )
    entered = asyncio.Event()
    released = asyncio.Event()
    failure = OSError("export failed")

    async def fail_export(self: metrics.MetricsRecorder) -> None:
        await entered.wait()
        raise failure

    monkeypatch.setattr(metrics.MetricsRecorder, "_run", fail_export)

    async def workload() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    recorder = metrics.MetricsRecorder(["http://127.0.0.1/metrics"], tmp_path / "metrics.jsonl")
    with pytest.raises(OSError) as caught:
        async with recorder:
            await asyncio.wait_for(recorder.run_workload(workload), 1)
    assert caught.value is failure
    assert released.is_set()
    assert recorder._task is not None and recorder._task.done()
    assert recorder._workload is None
    assert recorder._client is not None and recorder._client.is_closed
    assert recorder._file is not None and recorder._file.closed


@pytest.mark.asyncio
async def test_workload_cancellation_drains_owned_tasks_and_prevents_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        metrics.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="deltallm_event_loop_samplers 1\n")
            ),
            **kwargs,
        ),
    )
    entered = asyncio.Event()
    released = asyncio.Event()

    async def workload() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    recorder = metrics.MetricsRecorder(["http://127.0.0.1/metrics"], tmp_path / "metrics.jsonl")

    async def run() -> None:
        async with recorder:
            await recorder.run_workload(workload)

    task = asyncio.create_task(run())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert released.is_set()
        assert recorder._task is not None and recorder._task.done()
        assert recorder._workload is None
        assert recorder._client is not None and recorder._client.is_closed
        assert recorder._file is not None and recorder._file.closed
        with pytest.raises(RuntimeError, match="running metrics recorder"):
            await recorder.run_workload(workload)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_metrics_http_close_failure_still_closes_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_client = httpx.AsyncClient

    class FailingCloseClient(real_client):
        async def aclose(self) -> None:
            await super().aclose()
            raise OSError("client close failed")

    monkeypatch.setattr(
        metrics.httpx,
        "AsyncClient",
        lambda **kwargs: FailingCloseClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="deltallm_event_loop_samplers 1\n")
            ),
            **kwargs,
        ),
    )
    recorder = metrics.MetricsRecorder(["http://127.0.0.1/metrics"], tmp_path / "metrics.jsonl")
    with pytest.raises(OSError, match="client close failed"):
        async with recorder:
            assert await recorder.run_workload(lambda: asyncio.sleep(0, result=42)) == 42
    assert recorder._file is not None and recorder._file.closed
