"""Extra model-form hints read from a vLLM server.

vLLM's ``/v1/models`` returns each served model's context window
(``max_model_len``), its weights path (``root``) and, for LoRA adapters, the
base model (``parent``). It does not say which kinds of requests the server
answers. Recent vLLM versions only register the routes for the tasks the
loaded model supports, and list them in the unauthenticated ``/openapi.json``,
so that document tells us whether the server serves chat, embeddings, rerank
or transcription. Servers started with ``--disable-fastapi-docs`` hide it; the
form then falls back to showing every model for every mode.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.config import ModelMode

# Paths DeltaLLM calls for each mode on a vLLM api_base that ends in /v1.
_MODE_PATHS: tuple[tuple[ModelMode, str], ...] = (
    ("chat", "/v1/chat/completions"),
    ("embedding", "/v1/embeddings"),
    ("rerank", "/v1/rerank"),
    ("audio_transcription", "/v1/audio/transcriptions"),
    ("audio_speech", "/v1/audio/speech"),
    ("image_generation", "/v1/images/generations"),
)

# The OpenAPI document of a full vLLM server is well under 1 MiB.
MAX_OPENAPI_BYTES = 5 * 1024 * 1024

MODE_LABELS: dict[str, str] = {
    "chat": "chat",
    "embedding": "embeddings",
    "rerank": "rerank",
    "audio_transcription": "transcription",
    "audio_speech": "speech",
    "image_generation": "image generation",
}


def openapi_url(api_base: str) -> str:
    """The OpenAPI document sits at the server root, next to ``/v1``."""
    base = api_base.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/openapi.json"


def served_modes(openapi: Any) -> list[ModelMode] | None:
    """Modes the server has routes for, or ``None`` when the document is unusable."""
    paths = openapi.get("paths") if isinstance(openapi, Mapping) else None
    if not isinstance(paths, Mapping):
        return None
    modes = [mode for mode, path in _MODE_PATHS if path in paths]
    return modes or None


def inherit_parent_context(items: list[Any]) -> None:
    """LoRA adapters have no ``max_model_len``; they share the base model's window."""
    windows = {
        str(item.get("id")): item.get("max_model_len")
        for item in items
        if isinstance(item, dict) and item.get("max_model_len") is not None
    }
    for item in items:
        if not isinstance(item, dict) or item.get("max_model_len") is not None:
            continue
        parent = item.get("parent")
        if isinstance(parent, str) and parent in windows:
            item["max_model_len"] = windows[parent]


def model_label(item: Mapping[str, Any], model_id: str) -> str:
    """Shows the weights path when the served name hides it (``--served-model-name``)."""
    root = item.get("root")
    if isinstance(root, str):
        root = root.strip()
        if root and root != model_id and "/" in root and len(root) <= 200:
            return f"{model_id} ({root})"
    return model_id
