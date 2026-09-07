from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class RouteGroupIdentityNotFoundError(LookupError):
    """The requested group is absent from a subsequent database snapshot."""


@dataclass(frozen=True)
class RouteGroupIdentity:
    group_key: str
    route_group_id: str


@dataclass(frozen=True)
class RouteGroupLookup:
    column: Literal["group_key", "route_group_id"]
    value: str


class RouteGroupIdentityMixin:
    _identity: RouteGroupIdentity | None = None

    def _group_lookup(self, group_key: str) -> RouteGroupLookup:
        # An ID-addressed request must not follow a deleted group's reused key.
        # Other keys (for example simulation fallbacks) retain their own identity.
        if self._identity is not None and self._identity.group_key == group_key:
            return RouteGroupLookup("route_group_id", self._identity.route_group_id)
        return RouteGroupLookup("group_key", group_key)
