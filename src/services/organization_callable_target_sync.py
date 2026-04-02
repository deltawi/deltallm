from __future__ import annotations

from typing import Any

from src.services.asset_binding_mirror import (
    list_all_callable_target_bindings,
    list_all_route_group_bindings,
)
from src.services.callable_targets import CallableTarget

_CALLABLE_TARGET_ACCESS_METADATA_KEY = "_callable_target_access"
_AUTO_FOLLOW_CATALOG_METADATA_KEY = "auto_follow_catalog"


def organization_auto_follow_catalog(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    settings = metadata.get(_CALLABLE_TARGET_ACCESS_METADATA_KEY)
    if not isinstance(settings, dict):
        return False
    value = settings.get(_AUTO_FOLLOW_CATALOG_METADATA_KEY, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return False


def _organization_scope_id(scope_type: str | None, scope_id: str | None) -> str | None:
    normalized_scope_type = str(scope_type or "").strip().lower()
    if normalized_scope_type not in {"organization", "org"}:
        return None
    normalized_scope_id = str(scope_id or "").strip()
    return normalized_scope_id or None


def with_organization_auto_follow_catalog(
    metadata: dict[str, Any] | None,
    *,
    enabled: bool,
) -> dict[str, Any] | None:
    next_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    raw_settings = next_metadata.get(_CALLABLE_TARGET_ACCESS_METADATA_KEY)
    settings = dict(raw_settings) if isinstance(raw_settings, dict) else {}

    if enabled:
        settings[_AUTO_FOLLOW_CATALOG_METADATA_KEY] = True
        next_metadata[_CALLABLE_TARGET_ACCESS_METADATA_KEY] = settings
        return next_metadata

    settings.pop(_AUTO_FOLLOW_CATALOG_METADATA_KEY, None)
    if settings:
        next_metadata[_CALLABLE_TARGET_ACCESS_METADATA_KEY] = settings
    else:
        next_metadata.pop(_CALLABLE_TARGET_ACCESS_METADATA_KEY, None)
    return next_metadata or None


async def get_organization_auto_follow_catalog(db: Any, organization_id: str) -> bool:
    rows = await db.query_raw(
        """
        SELECT metadata
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        return False
    return organization_auto_follow_catalog(rows[0].get("metadata") if isinstance(rows[0], dict) else None)


async def set_organization_auto_follow_catalog(
    db: Any,
    organization_id: str,
    *,
    enabled: bool,
) -> None:
    rows = await db.query_raw(
        """
        SELECT metadata
        FROM deltallm_organizationtable
        WHERE organization_id = $1
        LIMIT 1
        """,
        organization_id,
    )
    if not rows:
        return

    current_metadata = rows[0].get("metadata") if isinstance(rows[0], dict) else None
    next_metadata = with_organization_auto_follow_catalog(
        current_metadata if isinstance(current_metadata, dict) else None,
        enabled=enabled,
    )
    await db.execute_raw(
        """
        UPDATE deltallm_organizationtable
        SET metadata = $2::jsonb,
            updated_at = NOW()
        WHERE organization_id = $1
        """,
        organization_id,
        next_metadata,
    )


async def disable_organization_auto_follow_catalog(db: Any, organization_id: str) -> bool:
    if not await get_organization_auto_follow_catalog(db, organization_id):
        return False
    await set_organization_auto_follow_catalog(
        db,
        organization_id,
        enabled=False,
    )
    return True


async def maybe_disable_organization_auto_follow_for_scope_mutation(
    db: Any,
    *,
    scope_type: str | None,
    scope_id: str | None,
) -> bool:
    organization_id = _organization_scope_id(scope_type, scope_id)
    if organization_id is None:
        return False
    return await disable_organization_auto_follow_catalog(db, organization_id)


async def list_organization_ids_with_auto_follow_catalog(db: Any) -> list[str]:
    rows = await db.query_raw(
        """
        SELECT organization_id, metadata
        FROM deltallm_organizationtable
        """
    )
    organization_ids: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        organization_id = str(row.get("organization_id") or "").strip()
        if not organization_id:
            continue
        if not organization_auto_follow_catalog(row.get("metadata") if isinstance(row.get("metadata"), dict) else None):
            continue
        organization_ids.append(organization_id)
    return organization_ids


async def sync_auto_follow_organization_bindings(
    *,
    db: Any | None,
    callable_target_binding_repository: Any | None,
    route_group_repository: Any | None,
    callable_target_catalog: dict[str, CallableTarget] | None,
) -> int:
    if db is None or callable_target_binding_repository is None or callable_target_catalog is None:
        return 0

    organization_ids = await list_organization_ids_with_auto_follow_catalog(db)
    if not organization_ids:
        return 0

    desired_keys = set(callable_target_catalog.keys())
    route_group_keys = {
        callable_key
        for callable_key, target in callable_target_catalog.items()
        if isinstance(target, CallableTarget) and target.target_type == "route_group"
    }
    changed = 0
    for organization_id in organization_ids:
        bindings = await list_all_callable_target_bindings(
            callable_target_binding_repository,
            scope_type="organization",
            scope_id=organization_id,
        )
        current_bindings_by_key = {
            str(binding.callable_key or "").strip(): binding
            for binding in bindings
            if str(binding.callable_key or "").strip()
        }
        current_keys = set(current_bindings_by_key.keys())

        for callable_key in sorted(current_keys - desired_keys):
            binding = current_bindings_by_key.get(callable_key)
            if binding is None:
                continue
            await callable_target_binding_repository.delete_binding(binding.callable_target_binding_id)
            changed += 1

        for callable_key in sorted(desired_keys):
            binding = current_bindings_by_key.get(callable_key)
            if binding is not None and binding.enabled:
                continue
            metadata = (
                dict(binding.metadata)
                if binding is not None and isinstance(binding.metadata, dict)
                else {"source": "auto_follow_catalog"}
            )
            await callable_target_binding_repository.upsert_binding(
                callable_key=callable_key,
                scope_type="organization",
                scope_id=organization_id,
                enabled=True,
                metadata=metadata,
            )
            changed += 1

        if route_group_repository is None:
            continue

        route_group_bindings = await list_all_route_group_bindings(
            route_group_repository,
            scope_type="organization",
            scope_id=organization_id,
        )
        route_group_bindings_by_key = {
            str(binding.group_key or "").strip(): binding
            for binding in route_group_bindings
            if str(binding.group_key or "").strip()
        }
        current_route_group_keys = set(route_group_bindings_by_key.keys())

        for callable_key in sorted(current_route_group_keys - route_group_keys):
            binding = route_group_bindings_by_key.get(callable_key)
            if binding is None:
                continue
            await route_group_repository.delete_binding(binding.route_group_binding_id)
            changed += 1

        for callable_key in sorted(route_group_keys):
            binding = route_group_bindings_by_key.get(callable_key)
            if binding is not None and binding.enabled:
                continue
            metadata = (
                dict(binding.metadata)
                if binding is not None and isinstance(binding.metadata, dict)
                else {"source": "auto_follow_catalog"}
            )
            await route_group_repository.upsert_binding(
                callable_key,
                scope_type="organization",
                scope_id=organization_id,
                enabled=True,
                metadata=metadata,
            )
            changed += 1
    return changed
