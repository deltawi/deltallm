from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.chat.executor import _open_grpc_stream
from src.models.requests import ChatCompletionRequest
from src.providers.grpc_stream import GrpcStreamHandle


class _FakeGrpcAdapter:
    def __init__(self) -> None:
        self.closed = False

    async def translate_request(self, canonical_request, provider_config):  # noqa: ANN001, ANN202
        del canonical_request, provider_config
        return {"messages": [{"role": "user", "content": "hello"}], "stream": True}

    async def open_grpc_stream(self, address, payload, **kwargs):  # noqa: ANN001, ANN202
        del address, payload, kwargs

        async def lines():
            yield 'data: {"id":"chunk-1"}'
            yield "data: [DONE]"

        async def aclose() -> None:
            self.closed = True

        return GrpcStreamHandle(lines=lines(), aclose=aclose)


@pytest.mark.asyncio
async def test_open_grpc_stream_closes_underlying_handle() -> None:
    adapter = _FakeGrpcAdapter()
    opened = await _open_grpc_stream(
        request=SimpleNamespace(),
        payload=ChatCompletionRequest.model_validate(
            {
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            }
        ),
        deployment=SimpleNamespace(
            deployment_id="dep-1",
            deltallm_params={"provider": "vllm"},
            model_info={},
        ),
        upstream=SimpleNamespace(
            adapter=adapter,
            grpc_address="localhost:50051",
            grpc_metadata={},
            timeout=30,
            api_base="",
        ),
    )

    assert opened.first_line == 'data: {"id":"chunk-1"}'
    await opened.close()

    assert adapter.closed is True
