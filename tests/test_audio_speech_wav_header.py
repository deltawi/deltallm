from __future__ import annotations

import struct

import httpx
import pytest

from src.routers.audio_speech import finalize_wav_header

_PCM = b"\x01\x00" * 50


def _wav(*, riff_size: int, data_size: int, extra: bytes = b"") -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)
    info = b"LIST" + struct.pack("<I", 4) + b"INFO"
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVE"
        + b"fmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + info
        + b"data"
        + struct.pack("<I", data_size)
        + _PCM
        + extra
    )


def _sizes(audio: bytes) -> tuple[int, int]:
    data_at = audio.index(b"data")
    return struct.unpack_from("<I", audio, 4)[0], struct.unpack_from("<I", audio, data_at + 4)[0]


@pytest.mark.parametrize("placeholder", [0xFFFFFFFF, 0])
def test_finalize_wav_header_replaces_streaming_placeholders(placeholder: int) -> None:
    streamed = _wav(riff_size=placeholder, data_size=placeholder)

    fixed = finalize_wav_header(streamed)

    assert len(fixed) == len(streamed)
    assert _sizes(fixed) == (len(streamed) - 8, len(_PCM))
    assert fixed.endswith(_PCM)


def test_finalize_wav_header_keeps_complete_files_unchanged() -> None:
    complete = _wav(riff_size=0, data_size=len(_PCM))
    complete = complete[:4] + struct.pack("<I", len(complete) - 8) + complete[8:]
    trailing_chunk = _wav(riff_size=0, data_size=len(_PCM), extra=b"LIST\x00\x00\x00\x00")

    assert finalize_wav_header(complete) is complete
    assert finalize_wav_header(trailing_chunk) is trailing_chunk
    assert finalize_wav_header(b"ID3 not a wav") == b"ID3 not a wav"
    assert finalize_wav_header(b"RIFF\xff\xff\xff\xffWAVE") == b"RIFF\xff\xff\xff\xffWAVE"


@pytest.mark.asyncio
async def test_audio_speech_returns_wav_with_real_header_sizes(client, test_app) -> None:  # noqa: ANN001
    deployment = test_app.state.router.deployment_registry["gpt-4o-mini"][0]
    deployment.model_info = {"mode": "audio_speech"}
    streamed = _wav(riff_size=0xFFFFFFFF, data_size=0xFFFFFFFF)

    async def post(url: str, headers: dict[str, str], json: dict, timeout: int):  # noqa: ANN001, ANN201
        del headers, json, timeout
        return httpx.Response(200, content=streamed, request=httpx.Request("POST", url))

    test_app.state.http_client.post = post

    response = await client.post(
        "/v1/audio/speech",
        headers={"Authorization": f"Bearer {test_app.state._test_key}"},
        json={"model": "gpt-4o-mini", "input": "hello", "voice": "fahad", "response_format": "wav"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert _sizes(response.content) == (len(streamed) - 8, len(_PCM))
