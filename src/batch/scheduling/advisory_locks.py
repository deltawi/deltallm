from __future__ import annotations

import hashlib
from typing import Literal

_INT64_SIGN_BIT = 1 << 63
_UINT64_MODULUS = 1 << 64
_COMPATIBILITY_MODE = "dual"
AdvisoryLockMode = Literal["dual", "canonical"]


def set_advisory_lock_mode(mode: AdvisoryLockMode | str) -> None:
    global _COMPATIBILITY_MODE
    normalized = str(mode or "dual").strip().lower()
    if normalized not in {"dual", "canonical"}:
        raise ValueError("advisory lock mode must be dual or canonical")
    _COMPATIBILITY_MODE = normalized


def advisory_lock_mode() -> AdvisoryLockMode:
    return "canonical" if _COMPATIBILITY_MODE == "canonical" else "dual"


def advisory_lock_acquires_legacy() -> bool:
    return advisory_lock_mode() == "dual"


def _normalize_lock_part(value: object) -> str:
    normalized = str(value or "").strip() or "unknown"
    return normalized.replace("\\", "\\\\").replace(":", "\\:")


def advisory_lock_name(namespace: str, *parts: object) -> str:
    normalized_namespace = str(namespace or "").strip()
    if not normalized_namespace:
        raise ValueError("advisory lock namespace is required")
    normalized_parts = [_normalize_lock_part(part) for part in parts]
    return ":".join((normalized_namespace, *normalized_parts))


def advisory_lock_legacy_parts(first: object, second: object) -> tuple[str, str]:
    return (str(first or "").strip() or "unknown", str(second or "").strip() or "unknown")


def parse_advisory_lock_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "t", "true", "y", "yes", "on"}
    return False


def advisory_lock_key(namespace: str, *parts: object) -> int:
    digest = hashlib.sha256(advisory_lock_name(namespace, *parts).encode("utf-8")).digest()
    unsigned_value = int.from_bytes(digest[:8], byteorder="big", signed=False)
    if unsigned_value >= _INT64_SIGN_BIT:
        return unsigned_value - _UINT64_MODULUS
    return unsigned_value
