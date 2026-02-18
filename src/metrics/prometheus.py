from __future__ import annotations

import hashlib
from typing import Any

from prometheus_client import CollectorRegistry

PROMETHEUS_REGISTRY = CollectorRegistry()
UNKNOWN_LABEL = "unknown"
ANONYMOUS_LABEL = "anonymous"
DEFAULT_TEAM_LABEL = "default"


def get_prometheus_registry() -> CollectorRegistry:
    return PROMETHEUS_REGISTRY


def sanitize_label(value: Any, fallback: str = UNKNOWN_LABEL) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    text = text.replace("\n", " ").replace("\r", " ")
    if len(text) > 128:
        return text[:128]
    return text


def hash_api_key(api_key: str | None) -> str:
    key = (api_key or "").strip()
    if not key:
        return UNKNOWN_LABEL
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def infer_provider(model: str | None) -> str:
    value = (model or "").strip()
    if "/" in value:
        return sanitize_label(value.split("/", 1)[0])
    return UNKNOWN_LABEL
