from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata


_CROCKFORD_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_MODEL_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_ROUTE_GROUP_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_COMPACT_ASSET_CODE_PATTERN = re.compile(r"^[0-9a-hjkmnp-tv-z]{4}$")


def _ascii_slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_value.lower())).strip("-")


def suggested_model_slug(display_name: str) -> str:
    """Return a short, readable callable slug without silently making it unique."""
    normalized = _ascii_slug(display_name)[:64].rstrip("-")
    return normalized or "model"


def suggested_route_group_slug(display_name: str) -> str:
    """Return a short callable group suffix derived from its friendly name."""

    normalized = _ascii_slug(display_name)[:64].rstrip("-")
    return normalized or "group"


def _stable_short_code(account_id: str, length: int = 5) -> str:
    digest = hashlib.sha256(account_id.encode("utf-8")).digest()
    value = int.from_bytes(digest, "big")
    encoded: list[str] = []
    for _ in range(length):
        encoded.append(_CROCKFORD_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(encoded))


def suggested_creator_namespace(email: str, account_id: str) -> str:
    """Suggest a non-secret, stable creator namespace from the account label."""
    local_part = email.partition("@")[0]
    tokens = [token for token in _ascii_slug(local_part).split("-") if token]
    if len(tokens) >= 2:
        stem = f"{tokens[0][:3]}-{tokens[-1][:3]}"
    elif tokens:
        token = tokens[0]
        stem = f"{token[:3]}-{token[-3:]}" if len(token) > 3 else token
    else:
        stem = "usr"
    stem = stem[:26].rstrip("-") or "usr"
    return f"{stem}-{_stable_short_code(account_id)}"


def normalize_creator_namespace(value: str) -> str:
    normalized = value.strip().lower()
    if (
        not 3 <= len(normalized) <= 32
        or not _NAMESPACE_PATTERN.fullmatch(normalized)
        or "--" in normalized
    ):
        raise ValueError(
            "Creator namespace must be 3-32 characters using lowercase letters, numbers, and "
            "single hyphens, and it cannot start or end with a hyphen"
        )
    return normalized


def normalize_model_slug(value: str) -> str:
    normalized = value.strip().lower()
    if not _MODEL_SLUG_PATTERN.fullmatch(normalized) or "--" in normalized:
        raise ValueError(
            "API model slug must be 1-64 characters using lowercase letters, numbers, and "
            "single hyphens, and it cannot start or end with a hyphen"
        )
    return normalized


def creator_api_model_id(namespace: str, model_slug: str) -> str:
    return f"{normalize_creator_namespace(namespace)}/{normalize_model_slug(model_slug)}"


def normalize_route_group_slug(value: str) -> str:
    normalized = value.strip().lower()
    if not _ROUTE_GROUP_SLUG_PATTERN.fullmatch(normalized) or "--" in normalized:
        raise ValueError(
            "Group key must be 1-64 characters using lowercase letters, numbers, and single "
            "hyphens, and it cannot start or end with a hyphen"
        )
    return normalized


def generate_compact_asset_code() -> str:
    """Generate a compact, URL-safe code for a creator-owned asset."""

    return "".join(secrets.choice(_CROCKFORD_ALPHABET) for _ in range(4))


def _normalize_compact_asset_code(value: str) -> str:
    normalized_code = value.strip().lower()
    if not _COMPACT_ASSET_CODE_PATTERN.fullmatch(normalized_code):
        raise ValueError("Asset code must contain exactly four URL-safe characters")
    return normalized_code


def creator_route_group_key(group_code: str, group_slug: str) -> str:
    """Return a compact creator group key without embedding the creator identity."""

    normalized_code = _normalize_compact_asset_code(group_code)
    return f"grp-{normalized_code}-{normalize_route_group_slug(group_slug)}"


def creator_prompt_template_key(prompt_code: str, template_key: str) -> str:
    """Return a compact creator prompt key without embedding the creator identity."""

    normalized_code = _normalize_compact_asset_code(prompt_code)
    return f"prm-{normalized_code}-{template_key}"
