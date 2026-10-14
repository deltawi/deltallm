"""Typed usage-report filters and their authorization against a reporting view.

A filter only narrows rows that already passed the view predicate. It never
widens a report, so a filter value is a lookup request and not proof of access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, get_args

from src.db.billing.spend_dimensions import SpendDimension
from src.services.reporting.spend_visibility import SpendVisibility

FILTER_DIMENSIONS: tuple[SpendDimension, ...] = get_args(SpendDimension)
MAX_FILTER_VALUE_LENGTH = 512


class UsageFilterError(ValueError):
    """The filter set is malformed (HTTP 422)."""


class UsageFilterForbidden(PermissionError):
    """A filter dimension is outside the active reporting view (HTTP 403)."""


@dataclass(frozen=True, slots=True)
class UsageFilters:
    values: tuple[tuple[SpendDimension, str], ...] = ()
    unassigned: frozenset[SpendDimension] = field(default_factory=frozenset)

    @classmethod
    def build(
        cls,
        *,
        values: dict[SpendDimension, str | None],
        unassigned: list[str] | None = None,
    ) -> UsageFilters:
        normalized: list[tuple[SpendDimension, str]] = []
        for dimension in FILTER_DIMENSIONS:
            raw = values.get(dimension)
            value = raw.strip() if isinstance(raw, str) else None
            if not value:
                continue
            if len(value) > MAX_FILTER_VALUE_LENGTH:
                raise UsageFilterError(f"The {dimension} filter is too long")
            normalized.append((dimension, value))

        unassigned_dimensions: set[SpendDimension] = set()
        for raw_dimension in unassigned or []:
            dimension = raw_dimension.strip().lower()
            if dimension not in FILTER_DIMENSIONS:
                raise UsageFilterError(f"Unknown usage filter dimension: {raw_dimension}")
            if dimension == "provider":
                raise UsageFilterError("Every request has a provider; filter by provider instead")
            unassigned_dimensions.add(dimension)  # type: ignore[arg-type]

        overlap = {dimension for dimension, _ in normalized} & unassigned_dimensions
        if overlap:
            raise UsageFilterError(
                "A dimension cannot have a value and be unassigned at the same time"
            )
        return cls(values=tuple(normalized), unassigned=frozenset(unassigned_dimensions))

    @property
    def dimensions(self) -> frozenset[SpendDimension]:
        return frozenset({dimension for dimension, _ in self.values} | self.unassigned)

    def authorize(self, visibility: SpendVisibility) -> None:
        allowed = set(visibility.allowed_groupings)
        if not self.dimensions <= allowed:
            raise UsageFilterForbidden("This usage filter is outside your reporting scope")

    def cache_payload(self) -> dict[str, Any]:
        return {
            "values": [[dimension, value] for dimension, value in self.values],
            "unassigned": sorted(self.unassigned),
        }
