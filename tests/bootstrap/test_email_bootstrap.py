from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from src.bootstrap import BootstrapStatus
from src.bootstrap.email import init_email_runtime, shutdown_email_runtime


def _app_state() -> SimpleNamespace:
    return SimpleNamespace(
        email_outbox_repository=None,
        prisma_manager=SimpleNamespace(client="db-client"),
        http_client=httpx.AsyncClient(),
    )


def _config(**overrides):
    general_settings = SimpleNamespace(
        email_enabled=True,
        email_provider="smtp",
        email_from_address="noreply@example.com",
        email_from_name="DeltaLLM",
        email_base_url="https://gateway.example.com",
        email_worker_enabled=True,
        email_worker_poll_interval_seconds=5.0,
        email_worker_max_concurrency=3,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username=None,
        smtp_password=None,
        smtp_use_tls=False,
        smtp_use_starttls=True,
    )
    for key, value in overrides.items():
        setattr(general_settings, key, value)
    return SimpleNamespace(general_settings=general_settings)


@pytest.mark.asyncio
async def test_init_email_runtime_marks_disabled_when_feature_off() -> None:
    app = SimpleNamespace(state=_app_state())

    runtime = await init_email_runtime(app, _config(email_enabled=False))

    assert runtime.statuses == (
        BootstrapStatus("email", "disabled"),
        BootstrapStatus("email_worker", "disabled"),
    )
    assert getattr(app.state, "email_delivery_service", None) is not None
    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_init_email_runtime_marks_invalid_config_degraded() -> None:
    app = SimpleNamespace(state=_app_state())

    runtime = await init_email_runtime(app, _config(email_from_address=None))

    assert runtime.statuses == (
        BootstrapStatus("email", "degraded", "email_from_address is required when email is enabled"),
        BootstrapStatus("email_worker", "disabled"),
    )
    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_init_email_runtime_requires_absolute_email_base_url() -> None:
    app = SimpleNamespace(state=_app_state())

    runtime = await init_email_runtime(app, _config(email_base_url=None))

    assert runtime.statuses == (
        BootstrapStatus("email", "degraded", "email_base_url is required when email is enabled"),
        BootstrapStatus("email_worker", "disabled"),
    )
    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_init_email_runtime_starts_worker_and_shutdown_cancels_task(monkeypatch: pytest.MonkeyPatch) -> None:
    created_tasks: list[object] = []
    stopped = {"value": False}

    class FakeWorker:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

        async def run(self) -> None:
            return None

        def stop(self) -> None:
            stopped["value"] = True

    class FakeTask:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

        def __await__(self):
            async def _inner():
                raise asyncio.CancelledError

            return _inner().__await__()

    import asyncio

    fake_task = FakeTask()

    monkeypatch.setattr("src.bootstrap.email.EmailOutboxWorker", FakeWorker)

    def _create_task(coro):  # noqa: ANN001, ANN202
        created_tasks.append(coro)
        coro.close()
        return fake_task

    monkeypatch.setattr("src.bootstrap.email.create_task", _create_task)

    app = SimpleNamespace(state=_app_state())
    runtime = await init_email_runtime(app, _config())

    assert runtime.statuses == (
        BootstrapStatus("email", "ready"),
        BootstrapStatus("email_worker", "ready"),
    )
    assert created_tasks
    assert runtime.worker is not None
    assert runtime.worker.kwargs["config"].max_concurrency == 3
    assert runtime.worker.kwargs["feedback_repository"] is app.state.email_feedback_repository

    await shutdown_email_runtime(runtime)

    assert stopped["value"] is True
    assert fake_task.cancelled is True
    await app.state.http_client.aclose()
