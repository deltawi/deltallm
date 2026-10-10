from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_OWNER_SCOPE_METADATA_KEY = "_asset_governance"
_OWNER_SCOPE_TYPE_KEY = "owner_scope_type"
_OWNER_SCOPE_ID_KEY = "owner_scope_id"

_OWNER_SCOPE_ALIASES = {
    "global": "global",
    "org": "organization",
    "organization": "organization",
}


@dataclass(frozen=True)
class OwnerScope:
    scope_type: str = "global"
    scope_id: str | None = None


def normalize_owner_scope_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    canonical = _OWNER_SCOPE_ALIASES.get(normalized or "global")
    if canonical is None:
        raise ValueError("owner_scope_type must be one of: global, organization")
    return canonical


def owner_scope_from_metadata(metadata: dict[str, Any] | None) -> OwnerScope:
    if not isinstance(metadata, dict):
        return OwnerScope()
    governance = metadata.get(_OWNER_SCOPE_METADATA_KEY)
    if not isinstance(governance, dict):
        return OwnerScope()

    scope_type = normalize_owner_scope_type(governance.get(_OWNER_SCOPE_TYPE_KEY))
    scope_id = _normalize_scope_id(governance.get(_OWNER_SCOPE_ID_KEY))
    if scope_type == "global":
        return OwnerScope(scope_type="global", scope_id=None)
    return OwnerScope(scope_type=scope_type, scope_id=scope_id)


def apply_owner_scope_to_metadata(
    metadata: dict[str, Any] | None,
    *,
    scope_type: str,
    scope_id: str | None,
) -> dict[str, Any] | None:
    normalized_scope_type = normalize_owner_scope_type(scope_type)
    normalized_scope_id = _normalize_scope_id(scope_id)
    payload = dict(metadata or {})

    if normalized_scope_type == "organization" and not normalized_scope_id:
        raise ValueError("owner_scope_id is required when owner_scope_type=organization")

    if normalized_scope_type == "global":
        governance = payload.get(_OWNER_SCOPE_METADATA_KEY)
        if isinstance(governance, dict):
            payload.pop(_OWNER_SCOPE_METADATA_KEY, None)
        return payload or None

    payload[_OWNER_SCOPE_METADATA_KEY] = {
        _OWNER_SCOPE_TYPE_KEY: normalized_scope_type,
        _OWNER_SCOPE_ID_KEY: normalized_scope_id,
    }
    return payload


def serialize_owner_scope(scope: OwnerScope) -> dict[str, str | None]:
    return {"owner_scope_type": scope.scope_type, "owner_scope_id": scope.scope_id}


def public_metadata_without_owner_scope(metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    payload = dict(metadata)
    payload.pop(_OWNER_SCOPE_METADATA_KEY, None)
    return payload or None


def _normalize_scope_id(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
