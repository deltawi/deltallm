from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping, Sequence
from typing import Any

MAX_CLAIM_VALUES = 500
_MAX_ID_TOKEN_BYTES = 16_384


def decode_id_token_claims(id_token: object, *, client_id: str, now: int) -> dict[str, Any] | None:
    """Read claims from an ID token that the token endpoint returned over TLS.

    OIDC Core 3.1.3.7 lets a confidential client that receives the ID token
    directly from the token endpoint rely on TLS instead of the signature. The
    audience and expiry are still checked. Any malformed token gives `None`, so
    the caller falls back to userinfo.
    """

    if not isinstance(id_token, str) or len(id_token.encode("utf-8")) > _MAX_ID_TOKEN_BYTES:
        return None
    parts = id_token.split(".")
    if len(parts) != 3:
        return None
    try:
        raw = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(claims, dict):
        return None
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences:
        return None
    expires_at = claims.get("exp")
    if not isinstance(expires_at, (int, float)) or expires_at <= now:
        return None
    return claims


def _resolve_path(source: Mapping[str, Any], path: str) -> object:
    if path in source:
        return source[path]
    current: object = source
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _split_values(raw: object) -> list[str] | None:
    if isinstance(raw, str):
        # Commas only: a group name can contain spaces ("DeltaLLM Admins").
        items: Sequence[object] = raw.split(",")
    elif isinstance(raw, list):
        items = raw
    else:
        return None
    values: list[str] = []
    for item in items:
        if not isinstance(item, (str, int)) or isinstance(item, bool):
            continue
        value = str(item).strip()
        if value and value not in values:
            values.append(value)
        if len(values) >= MAX_CLAIM_VALUES:
            break
    return values


def extract_claim_values(sources: Sequence[Mapping[str, Any]], path: str) -> list[str] | None:
    """Return claim values from the first source that has the claim.

    `None` means no source has the claim. An empty list means the claim is
    present with no values. A string is split on commas (kanidm `csv` join).
    """

    for source in sources:
        raw = _resolve_path(source, path)
        if raw is None:
            continue
        values = _split_values(raw)
        if values is not None:
            return values
    return None
