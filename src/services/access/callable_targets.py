from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from src.governance.access_groups import access_groups_from_metadata, normalize_access_groups
from src.router.callable_key_ownership import resolve_enabled_route_group_owners
from src.router.route_group_validation import normalize_route_group_mode

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CallableTarget:
    key: str
    target_type: Literal["model", "route_group"]
    access_groups: frozenset[str] = frozenset()
    mode: str | None = None
    mode_conflict: bool = False


def build_callable_target_catalog(
    model_registry: dict[str, list[dict[str, object]]],
    route_groups: list[dict[str, object]] | None = None,
) -> dict[str, CallableTarget]:
    catalog: dict[str, CallableTarget] = {}
    for model_name, entries in model_registry.items():
        key = str(model_name).strip()
        if not key:
            continue
        mode, mode_conflict = _model_mode(entries)
        catalog[key] = CallableTarget(
            key=key,
            target_type="model",
            access_groups=_model_access_groups(key, entries),
            mode=mode,
            mode_conflict=mode_conflict,
        )

    if not route_groups:
        return catalog

    for group_key, group in resolve_enabled_route_group_owners(route_groups).items():
        catalog[group_key] = CallableTarget(
            key=group_key,
            target_type="route_group",
            access_groups=_route_group_access_groups(group),
            mode=normalize_route_group_mode(group.get("mode")),
        )

    return catalog


def list_callable_target_ids(
    model_registry: dict[str, list[dict[str, object]]],
    route_groups: list[dict[str, object]] | None = None,
) -> list[str]:
    return list(build_callable_target_catalog(model_registry, route_groups).keys())


def _model_access_groups(model_name: str, entries: list[dict[str, object]]) -> frozenset[str]:
    resolved: frozenset[str] | None = None
    for entry in entries:
        model_info = entry.get("model_info") if isinstance(entry, Mapping) else None
        groups = access_groups_from_metadata(model_info)
        if resolved is None:
            resolved = groups
            continue
        if groups != resolved:
            logger.warning(
                "callable target access_groups conflict for model_name=%s; "
                "group expansion disabled for this model",
                model_name,
            )
            return frozenset()
    return resolved or frozenset()


def _model_mode(entries: list[dict[str, object]]) -> tuple[str | None, bool]:
    modes = {
        str(model_info.get("mode") or "").strip().lower()
        for entry in entries
        if isinstance(entry, Mapping)
        for model_info in [entry.get("model_info")]
        if isinstance(model_info, Mapping) and str(model_info.get("mode") or "").strip()
    }
    if len(modes) == 1:
        return next(iter(modes)), False
    return None, len(modes) > 1


def _route_group_access_groups(group: Mapping[str, object]) -> frozenset[str]:
    if isinstance(group, Mapping) and "access_groups" in group:
        return normalize_access_groups(group.get("access_groups"))
    metadata = group.get("metadata")
    return access_groups_from_metadata(metadata)
