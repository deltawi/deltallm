from __future__ import annotations

import asyncio

import httpx
import pytest

from src.callbacks import CallbackManager, CustomLogger


class RecordingCallback(CustomLogger):
    def __init__(self):
        self.success = 0
        self.failure = 0

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        del kwargs, response_obj, start_time, end_time
        self.success += 1

    async def async_log_failure_event(self, kwargs, exception, start_time, end_time):
        del kwargs, exception, start_time, end_time
        self.failure += 1


@pytest.mark.asyncio
async def test_embeddings_runs_success_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="success")
    test_app.state.callback_manager = manager

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    response = await client.post("/v1/embeddings", headers=headers, json=body)
    assert response.status_code == 200
    await asyncio.sleep(0.05)
    assert recorder.success == 1


@pytest.mark.asyncio
async def test_embeddings_runs_failure_callback(client, test_app):
    recorder = RecordingCallback()
    manager = CallbackManager()
    manager.register_callback(recorder, callback_type="failure")
    test_app.state.callback_manager = manager

    async def failing_post(url, headers, json, timeout):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(500, json={"error": "boom"}, request=httpx.Request("POST", url))

    test_app.state.http_client.post = failing_post

    headers = {"Authorization": f"Bearer {test_app.state._test_key}"}
    body = {"model": "text-embedding-3-small", "input": "hello"}

    response = await client.post("/v1/embeddings", headers=headers, json=body)
    assert response.status_code == 400
    await asyncio.sleep(0.05)
    assert recorder.failure == 1
