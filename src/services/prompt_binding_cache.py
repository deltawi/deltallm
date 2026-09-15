"""Untrusted binding-cache envelope and retained-byte bounds."""

from collections.abc import Mapping
import json

MAX_BINDING_CACHE_BYTES = 65_536
MAX_BINDING_KEY_BYTES = 1_024
MAX_BINDING_SCOPES = 5


def binding_payload_valid(payload: Mapping[str, object], scope: tuple[str, str]) -> bool:
    if payload.get("_deltallm_cache_state") == "miss":
        return payload.get("version") == 2
    if payload.get("cache_version") != 2:
        return False
    if (payload.get("scope_type"), payload.get("scope_id")) != scope:
        return False
    if payload.get("enabled") is not True or type(payload.get("priority")) is not int:
        return False
    return all(
        isinstance(payload.get(name), str) and bool(payload.get(name))
        for name in ("prompt_binding_id", "prompt_template_id", "template_key", "label")
    )


def binding_payload_fits(key: str, payload: Mapping[str, object]) -> bool:
    if len(key.encode("utf-8")) > MAX_BINDING_KEY_BYTES:
        return False
    try:
        return len(json.dumps(dict(payload)).encode("utf-8")) <= MAX_BINDING_CACHE_BYTES
    except (ValueError, TypeError, RecursionError):
        return False
