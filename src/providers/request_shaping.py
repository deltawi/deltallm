"""Per-deployment request shaping: parameter overrides and drops, headers, outbound proxy.

The configuration is parsed once per deployment. A deployment without shaping keys
pays one lookup per field on the hot path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from typing import Any
import uuid

SHAPING_PARAM_KEYS = (
    "override_params",
    "drop_params",
    "forward_headers",
    "extra_headers",
    "session_header",
    "outbound_proxy",
)

MAX_HEADERS = 16
MAX_PARAMS = 32
MAX_HEADER_NAME_LENGTH = 64
MAX_HEADER_VALUE_BYTES = 1_024
MAX_PARAM_PATH_DEPTH = 4
MAX_OVERRIDE_BYTES = 16_384

_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_PARAM_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_PROXY_NAME = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,62}$")
# Visible ASCII and spaces/tabs inside; no CR, LF or other control characters.
_HEADER_VALUE = re.compile(r"^[\x20-\x7e\t]*$")

# Never forwarded or set: credentials, transport framing, client addresses, signed
# AWS headers and the gateway's own headers.
_BLOCKED_HEADER_NAMES = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "api-key",
        "x-goog-api-key",
        "host",
        "content-length",
        "content-type",
        "content-encoding",
        "transfer-encoding",
        "connection",
        "keep-alive",
        "accept-encoding",
        "expect",
        "te",
        "trailer",
        "upgrade",
        "forwarded",
        "x-real-ip",
        "via",
    }
)
_BLOCKED_HEADER_PREFIXES = ("proxy-", "x-forwarded-", "x-amz-", "x-deltallm-")
_BLOCKED_HEADER_WORDS = (
    "auth",
    "token",
    "secret",
    "key",
    "cookie",
    "credential",
    "password",
    "signature",
)

# Billing, admission and the canonical request shape depend on these fields.
PROTECTED_PARAMS = frozenset(
    {
        "model",
        "messages",
        "input",
        "prompt",
        "stream",
        "stream_options",
        "n",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
    }
)

# Provider-native spellings of the same limits inside nested objects, such as
# Gemini generationConfig.maxOutputTokens or Bedrock inferenceConfig.maxTokens.
_PROTECTED_NESTED_KEYS = frozenset(
    {
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "maxtokens",
        "maxoutputtokens",
        "candidatecount",
        "stream",
    }
)


class RequestShapingError(ValueError):
    pass


def header_name_blocked(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in _BLOCKED_HEADER_NAMES
        or lowered.startswith(_BLOCKED_HEADER_PREFIXES)
        or any(word in lowered for word in _BLOCKED_HEADER_WORDS)
    )


def header_value_allowed(value: str) -> bool:
    return len(value.encode("utf-8")) <= MAX_HEADER_VALUE_BYTES and bool(
        _HEADER_VALUE.fullmatch(value)
    )


def _header_name(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise RequestShapingError(f"{field_name} entries must be header names")
    name = value.strip()
    if not name or len(name) > MAX_HEADER_NAME_LENGTH or not _HEADER_NAME.fullmatch(name):
        raise RequestShapingError(f"{field_name} has an invalid header name")
    if header_name_blocked(name):
        raise RequestShapingError(
            f"{field_name} cannot use '{name}': auth, credential and transport headers are never sent"
        )
    return name


def _param_path(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise RequestShapingError(f"{field_name} entries must be parameter names")
    segments = tuple(value.strip().split("."))
    if len(segments) > MAX_PARAM_PATH_DEPTH or not all(
        _PARAM_SEGMENT.fullmatch(segment) for segment in segments
    ):
        raise RequestShapingError(f"{field_name} has an invalid parameter name '{value}'")
    if segments[0] in PROTECTED_PARAMS:
        raise RequestShapingError(
            f"{field_name} cannot change '{segments[0]}': it controls admission and billing"
        )
    if any(segment.lower() in _PROTECTED_NESTED_KEYS for segment in segments[1:]):
        raise RequestShapingError(
            f"{field_name} cannot change '{value}': it controls admission and billing"
        )
    return segments


def _protected_nested_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _PROTECTED_NESTED_KEYS:
                return str(key)
            found = _protected_nested_key(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _protected_nested_key(item)
            if found is not None:
                return found
    return None


@dataclass(frozen=True, slots=True)
class RequestShaping:
    override_params: Mapping[str, Any] = field(default_factory=dict)
    drop_params: tuple[tuple[str, ...], ...] = ()
    forward_headers: tuple[str, ...] = ()
    extra_headers: tuple[tuple[str, str], ...] = ()
    session_header: str | None = None
    outbound_proxy: str | None = None

    @property
    def shapes_body(self) -> bool:
        return bool(self.override_params or self.drop_params)

    @property
    def shapes_headers(self) -> bool:
        return bool(self.forward_headers or self.extra_headers or self.session_header)


def parse_request_shaping(params: Mapping[str, Any]) -> RequestShaping | None:
    """Validate the shaping fields of one deployment; None when it has none."""
    raw = {key: params.get(key) for key in SHAPING_PARAM_KEYS}
    if all(value in (None, "", [], {}) for value in raw.values()):
        return None

    overrides = raw["override_params"] or {}
    if not isinstance(overrides, Mapping):
        raise RequestShapingError("override_params must be an object")
    if len(overrides) > MAX_PARAMS:
        raise RequestShapingError(f"override_params allows at most {MAX_PARAMS} entries")
    for key in overrides:
        if len(_param_path(key, "override_params")) != 1:
            raise RequestShapingError(
                "override_params keys are top-level names; use an object value for nested fields"
            )
    try:
        encoded = json.dumps(overrides, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RequestShapingError("override_params must hold JSON values") from exc
    if len(encoded.encode("utf-8")) > MAX_OVERRIDE_BYTES:
        raise RequestShapingError("override_params is too large")
    nested = _protected_nested_key(dict(overrides))
    if nested is not None:
        raise RequestShapingError(
            f"override_params cannot change '{nested}': it controls admission and billing"
        )

    drops_raw = raw["drop_params"] or []
    if not isinstance(drops_raw, (list, tuple)):
        raise RequestShapingError("drop_params must be a list of parameter names")
    if len(drops_raw) > MAX_PARAMS:
        raise RequestShapingError(f"drop_params allows at most {MAX_PARAMS} entries")
    drops = tuple(dict.fromkeys(_param_path(item, "drop_params") for item in drops_raw))
    overlap = {path[0] for path in drops if len(path) == 1} & set(overrides)
    if overlap:
        raise RequestShapingError(
            f"'{sorted(overlap)[0]}' cannot be both overridden and dropped"
        )

    forward_raw = raw["forward_headers"] or []
    if not isinstance(forward_raw, (list, tuple)):
        raise RequestShapingError("forward_headers must be a list of header names")
    forward = tuple(
        dict.fromkeys(_header_name(item, "forward_headers").lower() for item in forward_raw)
    )

    extra_raw = raw["extra_headers"] or {}
    if not isinstance(extra_raw, Mapping):
        raise RequestShapingError("extra_headers must be an object of header names and values")
    extra: dict[str, tuple[str, str]] = {}
    for name, value in extra_raw.items():
        header = _header_name(name, "extra_headers")
        if not isinstance(value, str) or not header_value_allowed(value):
            raise RequestShapingError(
                f"extra_headers value for '{header}' must be text up to {MAX_HEADER_VALUE_BYTES} bytes without control characters"
            )
        extra[header.lower()] = (header, value)

    session_raw = raw["session_header"]
    session = None
    if session_raw not in (None, ""):
        session = _header_name(session_raw, "session_header").lower()

    if len(forward) + len(extra) + (1 if session else 0) > MAX_HEADERS:
        raise RequestShapingError(f"A deployment can shape at most {MAX_HEADERS} headers")
    if session and session in extra:
        raise RequestShapingError("session_header cannot also be a set header")

    proxy_raw = raw["outbound_proxy"]
    proxy = None
    if proxy_raw not in (None, ""):
        if not isinstance(proxy_raw, str) or not _PROXY_NAME.fullmatch(proxy_raw.strip()):
            raise RequestShapingError("outbound_proxy must be the name of a configured proxy")
        proxy = proxy_raw.strip()

    return RequestShaping(
        override_params=deepcopy(dict(overrides)),
        drop_params=drops,
        forward_headers=forward,
        extra_headers=tuple(extra.values()),
        session_header=session,
        outbound_proxy=proxy,
    )


def apply_body_shaping(payload: dict[str, Any], shaping: RequestShaping | None) -> None:
    """Overrides win over client values and defaults; drops run last."""
    if shaping is None or not shaping.shapes_body:
        return
    for key, value in shaping.override_params.items():
        current = payload.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            payload[key] = _merge(current, value)
        else:
            payload[key] = deepcopy(value)
    for path in shaping.drop_params:
        _drop(payload, path)


def _merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            merged[key] = _merge(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _drop(payload: dict[str, Any], path: tuple[str, ...]) -> None:
    target: Any = payload
    for segment in path[:-1]:
        target = target.get(segment) if isinstance(target, dict) else None
        if not isinstance(target, dict):
            return
    target.pop(path[-1], None)


def shaped_headers(
    shaping: RequestShaping | None,
    *,
    client_headers: Mapping[str, str],
    session_id: Callable[[], str | None],
    reserved: Mapping[str, str],
) -> dict[str, str]:
    """Headers to add upstream. Names the provider request already sets are never replaced."""
    if shaping is None or not shaping.shapes_headers:
        return {}
    taken = {name.lower() for name in reserved}
    added: dict[str, str] = {}

    def add(name: str, value: str | None) -> None:
        lowered = name.lower()
        if (
            value is None
            or lowered in taken
            or header_name_blocked(lowered)
            or not header_value_allowed(value)
        ):
            return
        added[name] = value
        taken.add(lowered)

    if shaping.session_header:
        client_value = client_headers.get(shaping.session_header)
        if client_value is not None and client_value.strip() and header_value_allowed(client_value):
            add(shaping.session_header, client_value.strip())
        else:
            add(shaping.session_header, session_id() or str(uuid.uuid4()))
    for name in shaping.forward_headers:
        value = client_headers.get(name)
        if value is not None:
            add(name, value)
    for name, value in shaping.extra_headers:
        add(name, value)
    return added


def upstream_session_id(material: str) -> str:
    """A stable, one-way ID for one conversation, shaped like a UUID."""
    digest = sha256(f"deltallm-upstream-session-v1\0{material}".encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest[:16], version=4))
