from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

from src.config import (
    ModelMode,
    SUPPORTED_MODEL_MODES,
    validate_context_routing_workload_mode,
)


@dataclass(frozen=True, slots=True)
class RouteGroupModeResolution:
    groups: list[dict[str, Any]]
    inferred_group_keys: tuple[str, ...]


def normalize_route_group_mode(value: object) -> ModelMode:
    mode = str(value or "chat").strip().lower() or "chat"
    if mode not in SUPPORTED_MODEL_MODES:
        allowed = ", ".join(sorted(SUPPORTED_MODEL_MODES))
        raise ValueError(f"mode must be one of: {allowed}")
    return cast(ModelMode, mode)


def deployment_modes_by_id(
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, ModelMode]:
    modes: dict[str, ModelMode] = {}
    for entry in entries:
        deployment_id = str(entry.get("deployment_id") or "").strip()
        if not deployment_id:
            continue
        model_info = entry.get("model_info")
        raw_mode = model_info.get("mode") if isinstance(model_info, Mapping) else None
        modes[deployment_id] = normalize_route_group_mode(raw_mode)
    return modes


def validate_route_group_member_modes(
    *,
    group_key: str,
    group_mode: object,
    member_ids: Iterable[str],
    deployment_modes: Mapping[str, ModelMode],
) -> ModelMode:
    normalized_group_mode = normalize_route_group_mode(group_mode)
    mismatched = sorted(
        deployment_id
        for deployment_id in {str(item or "").strip() for item in member_ids}
        if deployment_id
        and deployment_id in deployment_modes
        and deployment_modes[deployment_id] != normalized_group_mode
    )
    if mismatched:
        raise ValueError(
            f"route group '{group_key}' mode '{normalized_group_mode}' is incompatible with "
            f"members: {', '.join(mismatched)}"
        )
    return normalized_group_mode


def resolve_route_group_modes(
    groups: Iterable[Mapping[str, Any]],
    *,
    deployment_modes: Mapping[str, ModelMode],
) -> RouteGroupModeResolution:
    """Resolve omitted legacy file modes before building any live runtime state."""

    resolved_groups: list[dict[str, Any]] = []
    inferred_keys: list[str] = []
    for raw_group in groups:
        group = deepcopy(dict(raw_group))
        group_key = str(group.get("key") or "").strip()
        raw_mode = group.get("mode")
        if raw_mode not in (None, ""):
            group["mode"] = normalize_route_group_mode(raw_mode)
            if group.get("context") is not None:
                validate_context_routing_workload_mode(group["mode"])
            resolved_groups.append(group)
            continue

        member_modes = {
            deployment_modes[deployment_id]
            for member in group.get("members") or []
            if isinstance(member, Mapping)
            and bool(member.get("enabled", True))
            and (deployment_id := str(member.get("deployment_id") or "").strip())
            and deployment_id in deployment_modes
        }
        if len(member_modes) > 1:
            modes = ", ".join(sorted(member_modes))
            raise ValueError(
                f"route group '{group_key}' omits mode but enabled members use multiple "
                f"workload modes: {modes}"
            )
        group["mode"] = next(iter(member_modes), "chat")
        if group.get("context") is not None:
            validate_context_routing_workload_mode(group["mode"])
        inferred_keys.append(group_key)
        resolved_groups.append(group)

    return RouteGroupModeResolution(
        groups=resolved_groups,
        inferred_group_keys=tuple(key for key in inferred_keys if key),
    )


def resolve_route_group_modes_for_registry(
    groups: Iterable[Mapping[str, Any]],
    model_registry: Mapping[str, Iterable[Mapping[str, Any]]],
) -> RouteGroupModeResolution:
    entries = (entry for model_entries in model_registry.values() for entry in model_entries)
    return resolve_route_group_modes(
        groups,
        deployment_modes=deployment_modes_by_id(entries),
    )
