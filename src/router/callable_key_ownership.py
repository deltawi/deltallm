from __future__ import annotations

from collections.abc import Mapping, Sequence


def resolve_enabled_route_group_owners(
    route_groups: Sequence[Mapping[str, object]] | None,
) -> dict[str, Mapping[str, object]]:
    """Return route groups that own public callable keys.

    Ownership depends only on the group being enabled, not on current member
    health or deployment availability. That keeps routing and authorization
    semantics aligned when a group is temporarily empty.
    """

    owners: dict[str, Mapping[str, object]] = {}
    for group in route_groups or ():
        group_key = str(group.get("key") or "").strip()
        if not group_key or not bool(group.get("enabled", True)):
            continue
        owners[group_key] = group
    return owners
