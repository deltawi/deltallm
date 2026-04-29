from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_ACCESS_GROUP_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_ACCESS_GROUPS_PER_TARGET = 100


class InvalidAccessGroupError(ValueError):
    """Raised when an access group key is malformed."""


def normalize_access_group_key(value: object, *, strict: bool = False) -> str | None:
    if not isinstance(value, str):
        if strict:
            raise InvalidAccessGroupError("access group key must be a string")
        return None

    normalized = value.strip().lower()
    if not normalized:
        if strict:
            raise InvalidAccessGroupError("access group key must be non-empty")
        return None
    if not _ACCESS_GROUP_PATTERN.fullmatch(normalized):
        if strict:
            raise InvalidAccessGroupError(
                "access group key must start with a lowercase letter or digit and contain only "
                "lowercase letters, digits, '.', '_' or '-'"
            )
        return None
    return normalized


def normalize_access_groups(value: object, *, strict: bool = False) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        if strict:
            raise InvalidAccessGroupError("access_groups must be an array")
        return frozenset()

    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        group_key = normalize_access_group_key(item, strict=strict)
        if group_key is None or group_key in seen:
            continue
        if len(normalized) >= MAX_ACCESS_GROUPS_PER_TARGET:
            if strict:
                raise InvalidAccessGroupError(
                    f"access_groups must contain at most {MAX_ACCESS_GROUPS_PER_TARGET} values"
                )
            break
        seen.add(group_key)
        normalized.append(group_key)
    return frozenset(normalized)


def normalize_access_group_list(value: object, *, strict: bool = False) -> list[str]:
    return sorted(normalize_access_groups(value, strict=strict))


def access_groups_from_metadata(metadata: object) -> frozenset[str]:
    if not isinstance(metadata, Mapping):
        return frozenset()
    return normalize_access_groups(metadata.get("access_groups"))


def build_callable_keys_by_access_group(catalog: Mapping[str, Any] | None) -> dict[str, frozenset[str]]:
    if not isinstance(catalog, Mapping):
        return {}

    grouped: dict[str, set[str]] = {}
    for raw_key, target in catalog.items():
        callable_key = str(raw_key or "").strip()
        if not callable_key:
            continue
        access_groups = getattr(target, "access_groups", frozenset())
        if not isinstance(access_groups, frozenset):
            access_groups = normalize_access_groups(list(access_groups) if access_groups else [])
        for group_key in access_groups:
            grouped.setdefault(group_key, set()).add(callable_key)
    return {group_key: frozenset(sorted(keys)) for group_key, keys in grouped.items()}
