from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from scripts.check_vllm_guided_json import check_guided_json


def check_with_content(content: str) -> list[httpx.Request]:
    captured = []

    def handle(request):
        captured.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"version": "0.12.0"})
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.headers["Cache-Control"] == "no-cache"
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        version = check_guided_json(
            client,
            gateway_url="https://gateway.test",
            vllm_url="https://vllm.test",
            model="private-chat",
            api_key="test-key",
        )
    assert version == "0.12.0"
    return captured


def test_live_check_exercises_both_legacy_shapes_and_standard_format():
    captured = check_with_content('{"invoice_number":"SCHEMA-432","total":432}')
    assert len(captured) == 4
    requests = [json.loads(request.content) for request in captured[1:]]
    schema = requests[0]["guided_json"]
    assert json.loads(requests[1]["guided_json"]) == schema
    assert requests[2]["response_format"]["json_schema"]["schema"] == schema


@pytest.mark.parametrize(
    "content",
    [
        "hello",
        "{}",
        '{"invoice_number":"wrong","total":432}',
        '{"invoice_number":"SCHEMA-432","total":1}',
        '{"invoice_number":"SCHEMA-432","total":432,"extra":true}',
    ],
)
def test_live_check_rejects_unconstrained_output(content):
    with pytest.raises(ValidationError):
        check_with_content(content)
