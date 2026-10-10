import json
import os
from unittest.mock import AsyncMock

import httpx
import pytest

from tests.providers.test_output_usage import chunk
from tests.providers.test_anthropic_output_stream_evidence import final_events
from tests.test_cache import _refresh_runtime_registry
from tests.test_output_tpm_text_redis import bucket_key, output_app as _output_app

output_app = _output_app

pytestmark = [
    pytest.mark.redis,
    pytest.mark.skipif(not os.getenv("DELTALLM_TEST_REDIS_URL"), reason="Redis URL is required"),
]


@pytest.mark.parametrize(
    "endpoint,body",
    [
        (
            "/v1/chat/completions",
            {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 80},
        ),
        ("/v1/completions", {"prompt": "hello", "max_tokens": 80}),
        ("/v1/responses", {"input": "hello", "max_output_tokens": 80}),
        ("/v1/messages", {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 80}),
    ],
)
@pytest.mark.parametrize("actual", [0, 7, 101])
@pytest.mark.parametrize("layout", ["terminal-choice", "usage-only", "anthropic"])
async def test_complete_usage_before_read_error_accounts_once_and_controls_next_call(
    output_app, endpoint, body, actual, layout
):
    app, redis, record, environment = output_app
    accounting = AsyncMock(wraps=app.state.limit_counter.account_output)
    app.state.limit_counter.account_output = accounting
    frames = (
        [chunk(), chunk([], usage={"completion_tokens": actual})]
        if layout == "usage-only"
        else [chunk(usage={"completion_tokens": actual})]
    )
    if layout == "anthropic":
        app.state.model_registry["gpt-4o-mini"][0]["deltallm_params"].update(
            provider="anthropic",
            model="anthropic/claude-sonnet-4-5",
            api_base="https://api.anthropic.com/v1",
        )
        _refresh_runtime_registry(app)
        frames = final_events(actual)

    class FailedStream(httpx.AsyncByteStream):
        closes = 0

        async def __aiter__(self):
            for frame in frames:
                yield f"data: {json.dumps(frame)}\n\n".encode()
            raise httpx.ReadError("Connection failed after final usage")

        async def aclose(self):
            self.closes += 1

    stream = FailedStream()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))
    ) as provider:
        app.state.http_client = provider
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            headers = {"Authorization": f"Bearer {app.state._test_key}"}
            request_body = {"model": "gpt-4o-mini", **body, "stream": True}
            first = await client.post(endpoint, headers=headers, json=request_body)
            assert first.status_code == 200, first.text
            assert stream.closes == 1
            if actual == 0:
                accounting.assert_not_awaited()
                assert not await redis.exists(bucket_key(record, environment))
            else:
                accounting.assert_awaited_once()
                assert accounting.call_args.args[0].actual == actual
                assert await redis.hmget(bucket_key(record, environment), "used", "unknown") == [
                    str(actual),
                    "0",
                ]
            second = await client.post(endpoint, headers=headers, json=request_body)
            assert second.status_code == (429 if actual >= 100 else 200), second.text
