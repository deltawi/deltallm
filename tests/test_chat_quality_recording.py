from __future__ import annotations

import httpx
import pytest

from src.router.quality import DeploymentQualityTracker
from src.router.runtime_generation import require_routing_runtime_generation
from tests.test_chat_response_validation import _add_fallback, _completion, _headers


def _track(test_app) -> DeploymentQualityTracker:  # noqa: ANN001
    tracker = DeploymentQualityTracker()
    require_routing_runtime_generation(test_app.state).router.quality = tracker
    return tracker


@pytest.mark.asyncio
async def test_chat_records_rejected_answer_and_fallback_success(client, test_app):
    tracker = _track(test_app)
    primary, fallback = _add_fallback(test_app)

    async def post(url, headers, json, timeout):  # noqa: ANN001, ANN202
        del timeout
        content = "" if headers.get("Authorization") == "Bearer provider-key" else "ok"
        return httpx.Response(
            200,
            json=_completion(json["model"], {"content": content}),
            request=httpx.Request("POST", url),
        )

    test_app.state.http_client.post = post
    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 200
    snapshot = tracker.local_snapshot()
    assert (
        snapshot.get(primary.deployment_id).calls,
        snapshot.get(primary.deployment_id).empty_answers,
    ) == (1, 1)
    assert snapshot.get(fallback.deployment_id).failed_calls == 0
    assert snapshot.get(fallback.deployment_id).output_tokens == 1


@pytest.mark.asyncio
async def test_stream_records_first_token_and_call(client, test_app):
    tracker = _track(test_app)

    response = await client.post(
        "/v1/chat/completions",
        headers=_headers(test_app),
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert "data: [DONE]" in response.text
    quality = tracker.local_snapshot().get(response.headers["x-deltallm-route-deployment"])
    assert (quality.calls, quality.failed_calls, quality.first_token_samples) == (1, 0, 1)
