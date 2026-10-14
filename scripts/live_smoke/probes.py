"""One small inference call per callable target, chosen by the target's mode."""

from __future__ import annotations

from collections.abc import Callable
import io
import json
from time import perf_counter
import wave

import httpx

from scripts.live_smoke.config import PROBED_MODES, SmokeConfig
from scripts.live_smoke.gateway_client import GatewayClient, GatewayError, error_text
from scripts.live_smoke.results import CheckResult, Status
from scripts.live_smoke.tier_sync import CallableTarget

PROMPT = "Reply with the single word: ok"


class ProbeFailure(RuntimeError):
    pass


def _http_failure(response: httpx.Response) -> ProbeFailure:
    request_id = response.headers.get("x-request-id")
    suffix = f" (request {request_id})" if request_id else ""
    return ProbeFailure(f"HTTP {response.status_code}: {error_text(response)}{suffix}")


def _post_json(client: GatewayClient, path: str, payload: dict[str, object]) -> dict:
    response = client.request("POST", path, json=payload)
    if response.status_code != 200:
        raise _http_failure(response)
    try:
        return response.json()
    except ValueError as exc:
        raise ProbeFailure("response is not JSON") from exc


def _chat(client: GatewayClient, model: str, config: SmokeConfig) -> str:
    body = _post_json(
        client,
        "/v1/chat/completions",
        {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": config.max_tokens,
        },
    )
    choices = body.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise ProbeFailure("response has no choices[0].message")
    content = choices[0]["message"].get("content")
    # Reasoning models can spend a small token budget before any visible text.
    return "" if content else "empty content (low max_tokens?)"


def _chat_stream(client: GatewayClient, model: str, config: SmokeConfig) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": PROMPT}],
        "max_tokens": config.max_tokens,
        "stream": True,
    }
    chunks = 0
    done = False
    with client.stream("/v1/chat/completions", json=payload) as response:
        if response.status_code != 200:
            response.read()
            raise _http_failure(response)
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                done = True
                break
            event = json.loads(data)
            if "error" in event:
                raise ProbeFailure(f"stream error: {str(event['error'])[:200]}")
            if event.get("choices") is not None:
                chunks += 1
    if not chunks:
        raise ProbeFailure("stream has no chunks")
    if not done:
        raise ProbeFailure("stream ended without [DONE]")
    return ""


def _embedding(client: GatewayClient, model: str, config: SmokeConfig) -> str:
    body = _post_json(client, "/v1/embeddings", {"model": model, "input": "live smoke test"})
    data = body.get("data") or []
    if not data or not data[0].get("embedding"):
        raise ProbeFailure("response has no embedding")
    return ""


def _speech(client: GatewayClient, model: str, config: SmokeConfig) -> str:
    response = client.request(
        "POST",
        "/v1/audio/speech",
        json={"model": model, "input": "Smoke test.", "voice": config.tts_voice},
    )
    if response.status_code != 200:
        raise _http_failure(response)
    if not response.content:
        raise ProbeFailure("response has no audio")
    return ""


def silent_wav(seconds: float = 1.0, rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


def _transcription(client: GatewayClient, model: str, config: SmokeConfig) -> str:
    response = client.request(
        "POST",
        "/v1/audio/transcriptions",
        data={"model": model},
        files={"file": ("smoke.wav", silent_wav(), "audio/wav")},
    )
    if response.status_code != 200:
        raise _http_failure(response)
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict) and "text" not in body:
        raise ProbeFailure("response has no text field")
    return ""


def _image(client: GatewayClient, model: str, config: SmokeConfig) -> str:
    payload: dict[str, object] = {"model": model, "prompt": "a small blue square", "n": 1}
    if config.image_size:
        payload["size"] = config.image_size
    body = _post_json(client, "/v1/images/generations", payload)
    if not body.get("data"):
        raise ProbeFailure("response has no image")
    return ""


def _rerank(client: GatewayClient, model: str, config: SmokeConfig) -> str:
    body = _post_json(
        client,
        "/v1/rerank",
        {
            "model": model,
            "query": "What is DeltaLLM?",
            "documents": ["DeltaLLM is an LLM gateway.", "Bananas are yellow."],
        },
    )
    if not body.get("results"):
        raise ProbeFailure("response has no results")
    return ""


Probe = Callable[[GatewayClient, str, SmokeConfig], str]

PROBES_BY_MODE: dict[str, tuple[tuple[str, Probe], ...]] = {
    "chat": (("chat", _chat), ("chat stream", _chat_stream)),
    "embedding": (("embeddings", _embedding),),
    "audio_speech": (("speech", _speech),),
    "audio_transcription": (("transcription", _transcription),),
    "image_generation": (("image", _image),),
    "rerank": (("rerank", _rerank),),
}
assert set(PROBES_BY_MODE) == PROBED_MODES


def target_mode(target: CallableTarget) -> str | None:
    """Targets with no declared mode run as chat, the gateway default."""

    if target.mode_conflict:
        return None
    return (target.mode or "chat").strip().lower()


def probe_target(
    client: GatewayClient, target: CallableTarget, config: SmokeConfig
) -> list[CheckResult]:
    mode = target_mode(target)
    if mode is None:
        return [CheckResult("model", target.key, Status.FAIL, "deployments disagree on mode")]
    if mode in config.skip_modes:
        return [CheckResult("model", target.key, Status.SKIP, "mode skipped", mode=mode)]
    if target.creator_scoped:
        detail = "created by an account and not shared with the test organization"
        return [CheckResult("model", target.key, Status.SKIP, detail, mode=mode)]
    probes = PROBES_BY_MODE.get(mode)
    if probes is None:
        return [CheckResult("model", target.key, Status.SKIP, f"{mode} is not covered", mode=mode)]
    results = []
    for label, probe in probes:
        started = perf_counter()
        try:
            note = probe(client, target.key, config)
        except (ProbeFailure, GatewayError) as exc:
            status, detail = Status.FAIL, f"{label}: {exc}"
        except Exception as exc:  # noqa: BLE001 - one broken target must not stop the run
            status, detail = Status.FAIL, f"{label}: {type(exc).__name__}: {str(exc)[:200]}"
        else:
            status = Status.WARN if note else Status.PASS
            detail = f"{label}: {note}" if note else label
        elapsed = int((perf_counter() - started) * 1000)
        results.append(CheckResult("model", target.key, status, detail, mode, elapsed))
    return results
