"""Optional second pass that calls each model with the official OpenAI Python SDK.

The HTTP probes check the gateway answers field by field. This pass checks that the SDK
that most customers use can send each call and read each real provider answer. The
gateway does not depend on the SDK, so the suite imports it only with --openai-sdk.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from types import ModuleType
from typing import Any

from scripts.live_smoke.config import SmokeConfig
from scripts.live_smoke.gateway_client import error_text
from scripts.live_smoke.probes import PROMPT, ProbeFailure, silent_wav

INSTALL_HINT = (
    "uv run --with-requirements tests/openai_sdk/requirements.txt "
    "python -m scripts.live_smoke --openai-sdk ..."
)


def load_openai() -> ModuleType:
    try:
        import openai
    except ImportError as exc:
        raise ValueError(f"--openai-sdk needs the openai package. Run: {INSTALL_HINT}") from exc
    return openai


def _chat(client: Any, model: str, config: SmokeConfig) -> str:
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT}],
        max_tokens=config.max_tokens,
    )
    choices = getattr(completion, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    if message is None:
        raise ProbeFailure("completion has no choices[0].message")
    return "" if getattr(message, "content", None) else "empty content (low max_tokens?)"


def _chat_stream(client: Any, model: str, config: SmokeConfig) -> str:
    chunks = 0
    with client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": PROMPT}],
        max_tokens=config.max_tokens,
        stream=True,
    ) as stream:
        for chunk in stream:
            if getattr(chunk, "choices", None) is not None:
                chunks += 1
    if not chunks:
        raise ProbeFailure("stream has no chunks")
    return ""


def _embedding(client: Any, model: str, config: SmokeConfig) -> str:
    # The SDK asks for base64 by default and decodes the answer to floats.
    response = client.embeddings.create(model=model, input="live smoke test")
    data = getattr(response, "data", None) or []
    vector = getattr(data[0], "embedding", None) if data else None
    if not vector or not isinstance(vector[0], float):
        raise ProbeFailure("response has no embedding")
    return ""


def _speech(client: Any, model: str, config: SmokeConfig) -> str:
    audio = client.audio.speech.create(model=model, voice=config.tts_voice, input="Smoke test.")
    if not audio.content:
        raise ProbeFailure("response has no audio")
    return ""


def _transcription(client: Any, model: str, config: SmokeConfig) -> str:
    result = client.audio.transcriptions.create(
        model=model, file=("smoke.wav", silent_wav(), "audio/wav")
    )
    if not isinstance(getattr(result, "text", None), str):
        raise ProbeFailure("response has no text field")
    return ""


def _image(client: Any, model: str, config: SmokeConfig) -> str:
    options = {"size": config.image_size} if config.image_size else {}
    result = client.images.generate(model=model, prompt="a small blue square", n=1, **options)
    if not getattr(result, "data", None):
        raise ProbeFailure("response has no image")
    return ""


SdkProbe = Callable[[Any, str, SmokeConfig], str]

# The SDK has no rerank call, so rerank models get only the HTTP probe.
SDK_PROBES_BY_MODE: dict[str, tuple[tuple[str, SdkProbe], ...]] = {
    "chat": (("sdk chat", _chat), ("sdk chat stream", _chat_stream)),
    "embedding": (("sdk embeddings", _embedding),),
    "audio_speech": (("sdk speech", _speech),),
    "audio_transcription": (("sdk transcription", _transcription),),
    "image_generation": (("sdk image", _image),),
}


class SdkCaller:
    """One OpenAI SDK client for the test key. Safe to share between threads."""

    def __init__(
        self,
        openai: ModuleType,
        base_url: str,
        api_key: str,
        *,
        timeout: float,
        transport: Any | None = None,
    ) -> None:
        self._openai = openai
        self.version = str(openai.__version__)
        http_client = None if transport is None else openai.DefaultHttpxClient(transport=transport)
        self._client = openai.OpenAI(
            base_url=f"{base_url}/v1",
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )

    def close(self) -> None:
        self._client.close()

    def calls(
        self, mode: str, model: str, config: SmokeConfig
    ) -> list[tuple[str, Callable[[], str]]]:
        return [
            (label, partial(self._call, probe, model, config))
            for label, probe in SDK_PROBES_BY_MODE.get(mode, ())
        ]

    def _call(self, probe: SdkProbe, model: str, config: SmokeConfig) -> str:
        try:
            return probe(self._client, model, config)
        except self._openai.APIStatusError as exc:
            suffix = f" (request {exc.request_id})" if exc.request_id else ""
            raise ProbeFailure(
                f"HTTP {exc.status_code}: {error_text(exc.response)}{suffix}"
            ) from exc
        except self._openai.APIError as exc:
            raise ProbeFailure(f"{type(exc).__name__}: {str(exc)[:200]}") from exc
