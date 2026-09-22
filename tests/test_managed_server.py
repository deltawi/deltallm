import asyncio
import signal
import socket
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from src.lifecycle_settings import LifecycleSettings
from src.managed_server import ManagedServer, SUPPORTED_UVICORN
from src.middleware.ingress import IngressMiddleware
from src.process_lifecycle import ProcessLifecycle


def lifecycle_settings(**overrides):
    return LifecycleSettings(
        **(
            {
                "lifecycle_withdrawal_seconds": 0.3,
                "lifecycle_request_drain_seconds": 0.3,
                "lifecycle_cancellation_seconds": 0.2,
                "lifecycle_worker_drain_seconds": 0.1,
                "lifecycle_close_seconds": 0.1,
                "lifecycle_shutdown_seconds": 1,
            }
            | overrides
        )
    )


@pytest.mark.parametrize(
    "terminal",
    [
        b"data: [DONE]\n\n",
        b'event: response.completed\ndata: {"type":"response.completed"}\n\n',
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ],
)
async def test_real_http_drain_rejects_new_work_and_cancels_stream_without_success(terminal):
    lifecycle = ProcessLifecycle(lifecycle_settings())
    application = SimpleNamespace(state=SimpleNamespace(process_lifecycle=lifecycle))
    entered, upstream_closed = asyncio.Event(), asyncio.Event()
    requests = []

    async def downstream(scope, receive, send):
        if scope["type"] == "lifespan":
            assert (await receive())["type"] == "lifespan.startup"
            lifecycle.mark_serving()
            await send({"type": "lifespan.startup.complete"})
            assert (await receive())["type"] == "lifespan.shutdown"
            assert upstream_closed.is_set()
            await send({"type": "lifespan.shutdown.complete"})
            return
        if scope["path"] == "/health/readiness":
            await send(
                {
                    "type": "http.response.start",
                    "status": 200 if lifecycle.ready else 503,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b"health"})
            return
        requests.append(scope["path"])
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send({"type": "http.response.body", "body": b"data: first\n\n", "more_body": True})
        entered.set()
        try:
            await asyncio.Event().wait()
            await send({"type": "http.response.body", "body": terminal})
        finally:
            upstream_closed.set()

    middleware = IngressMiddleware(downstream)

    async def app(scope, receive, send):
        scope["app"] = application
        await middleware(scope, receive, send)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = sock.getsockname()
    server = ManagedServer(uvicorn.Config(app, lifespan="on", access_log=False), lifecycle)
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{address[1]}") as client:
                async with client.stream("POST", "/v1/chat/completions") as response:
                    chunks = response.aiter_bytes()
                    assert await anext(chunks) == b"data: first\n\n"
                    await entered.wait()
                    server.handle_exit(signal.SIGTERM, None)
                    deadlines = lifecycle.deadlines
                    server.handle_exit(signal.SIGTERM, None)
                    assert lifecycle.deadlines is deadlines
                    assert (await client.get("/health/readiness")).status_code == 503
                    rejected = await client.post("/v1/chat/completions", content=b"unused")
                    assert rejected.status_code == 503
                    assert rejected.headers["retry-after"] == "1"
                    assert rejected.json()["error"]["code"] == "gateway_draining"
                    received = b""
                    with pytest.raises(httpx.RemoteProtocolError):
                        async for chunk in chunks:
                            received += chunk
                    assert terminal not in received
            await task
        assert upstream_closed.is_set()
        assert requests == ["/v1/chat/completions"]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        sock.close()


def test_locked_server_version_and_unsupported_process_options():
    assert uvicorn.__version__ == SUPPORTED_UVICORN
    lifecycle = ProcessLifecycle(lifecycle_settings())
    for options in ({"workers": 2}, {"reload": True}, {"lifespan": "off"}):
        config = uvicorn.Config(lambda *_: None, **({"lifespan": "on"} | options))
        with pytest.raises(ValueError, match="one process"):
            ManagedServer(config, lifecycle)


def test_managed_command_imports_in_a_fresh_interpreter():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "src.server", "--help"], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert "--port" in result.stdout


def test_signal_during_startup_migration_kills_owned_cli_and_never_serves(tmp_path):
    import os
    import subprocess
    import sys
    import textwrap
    import time

    marker = tmp_path / "migration-pid"
    script = textwrap.dedent("""
        from pathlib import Path
        from types import SimpleNamespace
        import sys
        from src import server
        from src.lifecycle_settings import LifecycleSettings
        from src.migration_process import run_migration_process
        server.StartupConfig.load = lambda: SimpleNamespace(
            lifecycle=LifecycleSettings(), app_config=None, settings=None)
        server.resolve_database_settings = lambda *_: SimpleNamespace(url='fixture')
        def migration(**kwargs):
            run_migration_process([sys.executable, '-c',
                'import os,sys,time; from pathlib import Path; '
                'Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)',
                sys.argv[1]], timeout=60)
        server.run_prisma_bootstrap = migration
        server.main([])
    """)
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(marker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.01)
        assert marker.exists()
        child_pid = int(marker.read_text())
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 128 + signal.SIGTERM, stderr
        assert "Application startup complete" not in stdout + stderr
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
