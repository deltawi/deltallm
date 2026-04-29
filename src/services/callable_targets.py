from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from src.governance.access_groups import access_groups_from_metadata, normalize_access_groups

logger = logging.getLogger(__name__)


class DuplicateCallableTargetError(ValueError):
    """Raised when two callable assets publish the same external key."""


@dataclass(frozen=True)
class CallableTarget:
    key: str
    target_type: Literal["model", "route_group"]
    access_groups: frozenset[str] = frozenset()


def build_callable_target_catalog(
    model_registry: dict[str, list[dict[str, object]]],
    route_groups: list[dict[str, object]] | None = None,
) -> dict[str, CallableTarget]:
    catalog: dict[str, CallableTarget] = {}
    deployment_ids = _collect_deployment_ids(model_registry)

    for model_name, entries in model_registry.items():
        key = str(model_name).strip()
        if not key:
            continue
        catalog[key] = CallableTarget(
            key=key,
            target_type="model",
            access_groups=_model_access_groups(key, entries),
        )

    if not route_groups:
        return catalog

    for group in route_groups:
        group_key = str(group.get("key") or "").strip()
        if not group_key or not bool(group.get("enabled", True)):
            continue
        if not _route_group_has_live_members(group, deployment_ids):
            continue
        existing = catalog.get(group_key)
        if existing is not None:
            raise DuplicateCallableTargetError(
                f"Callable target key '{group_key}' is declared by both a {existing.target_type} and a route_group"
            )
        catalog[group_key] = CallableTarget(
            key=group_key,
            target_type="route_group",
            access_groups=_route_group_access_groups(group),
        )

    return catalog


def list_callable_target_ids(
    model_registry: dict[str, list[dict[str, object]]],
    route_groups: list[dict[str, object]] | None = None,
) -> list[str]:
    return list(build_callable_target_catalog(model_registry, route_groups).keys())


def _collect_deployment_ids(model_registry: dict[str, list[dict[str, object]]]) -> set[str]:
    deployment_ids: set[str] = set()
    for model_name, entries in model_registry.items():
        for index, entry in enumerate(entries):
            deployment_id = str(entry.get("deployment_id") or f"{model_name}-{index}").strip()
            if deployment_id:
                deployment_ids.add(deployment_id)
    return deployment_ids


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


def _route_group_access_groups(group: dict[str, object]) -> frozenset[str]:
    if isinstance(group, Mapping) and "access_groups" in group:
        return normalize_access_groups(group.get("access_groups"))
    metadata = group.get("metadata") if isinstance(group, Mapping) else None
    return access_groups_from_metadata(metadata)


def _route_group_has_live_members(group: dict[str, object], deployment_ids: set[str]) -> bool:
    members = group.get("members") or []
    if not isinstance(members, list):
        return False

    for member in members:
        if not isinstance(member, dict) or not bool(member.get("enabled", True)):
            continue
        deployment_id = str(member.get("deployment_id") or "").strip()
        if deployment_id and deployment_id in deployment_ids:
            return True
    return False
