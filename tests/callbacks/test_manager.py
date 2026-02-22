from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from src.callbacks import CallbackManager, CustomLogger, build_standard_logging_payload


class RecordingCallback(CustomLogger):
    def __init__(self) -> None:
        self.success_calls = 0
        self.failure_calls = 0

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        del kwargs, response_obj, start_time, end_time
        self.success_calls += 1

    async def async_log_failure_event(self, kwargs, exception, start_time, end_time):
        del kwargs, exception, start_time, end_time
        self.failure_calls += 1


@pytest.mark.asyncio
async def test_callback_manager_dispatches_success_and_failure() -> None:
    manager = CallbackManager()
    callback = RecordingCallback()
    manager.register_callback(callback, callback_type="both")

    start = datetime.now(tz=UTC)
    end = datetime.now(tz=UTC)
    payload = build_standard_logging_payload(
        call_type="completion",
        request_id="req-1",
        model="gpt-4o-mini",
        deployment_model="openai/gpt-4o-mini",
        request_payload={"messages": [{"role": "user", "content": "hello"}]},
        response_obj={"usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        user_api_key_dict={"api_key": "hashed", "user_id": "u1", "team_id": "t1"},
        start_time=start,
        end_time=end,
        api_base="https://api.openai.com/v1",
    )

    manager.dispatch_success_callbacks(payload)
    manager.dispatch_failure_callbacks(payload, RuntimeError("boom"))

    await asyncio.sleep(0.05)

    assert callback.success_calls == 1
    assert callback.failure_calls == 1


@pytest.mark.asyncio
async def test_callback_manager_loads_success_failure_from_settings() -> None:
    manager = CallbackManager()
    manager.load_from_settings(
        success_callbacks=["does-not-exist"],
        failure_callbacks=["does-not-exist"],
        callbacks=None,
        callback_settings=None,
    )
    assert manager.success_callbacks == []
    assert manager.failure_callbacks == []
