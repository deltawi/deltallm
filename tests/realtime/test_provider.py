import pytest

from src.providers import openai_realtime
from src.providers.openai_realtime import OpenAIRealtimeConnector, resolve_realtime_target
from src.realtime.contracts import RealtimeError, RealtimeLimits
from tests.realtime.fakes import target


def test_target_uses_provider_credentials_and_does_not_render_secrets():
    binding = target()
    assert binding.url == "wss://api.openai.com/v1/realtime?model=provider-model"
    assert binding.headers == {"Authorization": "Bearer provider-secret"}
    assert "provider-secret" not in repr(binding)
    with pytest.raises(TypeError):
        binding.headers["Authorization"] = "other"
    assert target("transcription").url == "wss://api.openai.com/v1/realtime?intent=transcription"


@pytest.mark.parametrize(
    "params",
    [
        {"model": "azure/model", "api_key": "secret"},
        {"model": "openai/model"},
        *[
            {"model": "openai/model", "api_key": "secret", "api_base": base}
            for base in [
                "http://api.openai.com/v1",
                "https://user:secret@api.openai.com/v1",
                "https://api.openai.com.evil.example/v1",
                "https://127.0.0.1/v1",
                "https://api.openai.com/v1?key=secret",
                "https://api.openai.com/v1#secret",
            ]
        ],
    ],
)
def test_unqualified_provider_and_egress_destinations_are_rejected(params):
    with pytest.raises(RealtimeError):
        resolve_realtime_target(params, public_model="voice", profile="realtime")


async def test_connector_is_single_attempt_bounded_and_does_not_follow_redirects(monkeypatch):
    calls = []
    closed = []

    class Connect:
        def __init__(self, url, **kwargs):
            calls.append((self, url, kwargs))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def recv(self):
            return '{"type":"session.created"}'

        async def send(self, message):
            pass

    monkeypatch.setattr(openai_realtime, "connect", Connect)
    async with OpenAIRealtimeConnector().open(target(), RealtimeLimits()) as socket:
        assert await socket.receive_text() == '{"type":"session.created"}'
    assert len(calls) == 1 and len(closed) == 1
    instance, url, options = calls[0]
    assert options["max_queue"] == 4
    assert options["max_size"] == 1024 * 1024
    assert options["proxy"] is None and options["compression"] is None
    redirect = RuntimeError("redirect with credentials")
    assert instance.process_redirect(redirect) is redirect


@pytest.mark.parametrize("operation", ["receive_text", "send_text"])
async def test_abnormal_close_is_not_reported_as_normal_completion(operation):
    from websockets.exceptions import ConnectionClosedError
    from websockets.frames import Close
    from src.providers.openai_realtime import OpenAIRealtimeSocket

    class BrokenConnection:
        async def recv(self):
            raise ConnectionClosedError(Close(1011, "provider-secret"), None)

        async def send(self, message):
            await self.recv()

    socket = OpenAIRealtimeSocket(BrokenConnection())
    with pytest.raises(RealtimeError) as caught:
        if operation == "receive_text":
            await socket.receive_text()
        else:
            await socket.send_text("test")
    assert caught.value.code == "upstream_disconnected"
    assert "provider-secret" not in str(caught.value)
