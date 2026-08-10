from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, cast

from src.auth.roles import Permission
from src.billing.spend_read import SpendReadSource


SPEND_VISIBILITY_PERMISSIONS = (
    Permission.SPEND_READ,
    Permission.SPEND_READ_TEAM,
    Permission.SPEND_READ_SELF,
)

OWNER_DIMENSIONS = ("organization", "team", "user")
SpendView = Literal["platform", "organization", "team", "self"]


@dataclass(frozen=True, slots=True)
class SpendVisibility:
    """An explicit, non-overlapping usage-report view for an authenticated account."""

    is_platform_admin: bool
    organization_ids: tuple[str, ...] = ()
    team_ids: tuple[str, ...] = ()
    self_organization_ids: tuple[str, ...] = ()
    self_team_ids: tuple[str, ...] = ()
    owner_account_id: str | None = None
    active_view: SpendView | None = None

    @property
    def available_views(self) -> tuple[SpendView, ...]:
        if self.is_platform_admin:
            return ("platform",)
        views: list[SpendView] = []
        if self.organization_ids:
            views.append("organization")
        if self.team_ids:
            views.append("team")
        if (
            self.owner_account_id
            and (self.self_organization_ids or self.self_team_ids)
        ):
            views.append("self")
        return tuple(views)

    @property
    def default_view(self) -> SpendView:
        views = self.available_views
        if not views:
            # The route permission dependency should prevent this state. Keeping
            # a fail-closed view here protects direct/internal callers as well.
            return "self"
        return views[0]

    @property
    def view(self) -> SpendView:
        return self.active_view or self.default_view

    @property
    def level(self) -> str:
        return self.view

    @property
    def is_self_only(self) -> bool:
        return self.view == "self"

    @property
    def allowed_dimensions(self) -> tuple[str, ...]:
        if self.view in {"platform", "organization"}:
            return OWNER_DIMENSIONS
        if self.view == "team":
            return ("team", "user")
        if self.view == "self":
            # These dimensions only partition usage that has already passed
            # the immutable owner-account predicate. They do not grant access
            # to organization- or team-wide usage from other accounts.
            return ("organization", "team")
        return ()

    @property
    def allowed_groupings(self) -> tuple[str, ...]:
        common = ("day", "model", "provider")
        if self.view in {"platform", "organization"}:
            return (*common, *OWNER_DIMENSIONS, "api_key")
        return (*common, *self.allowed_dimensions)

    @property
    def can_view_request_logs(self) -> bool:
        # Detailed operational logs remain limited to platform and organization
        # views. A user with mixed roles can deliberately switch back to one of
        # those views without broadening the team/self report predicates.
        return self.view in {"platform", "organization"}

    def select_view(self, requested_view: str | None) -> SpendVisibility:
        if requested_view is None:
            return replace(self, active_view=self.default_view)
        normalized = requested_view.strip().lower()
        if normalized not in self.available_views:
            raise ValueError("The requested usage view is outside your reporting scope")
        return replace(self, active_view=cast(SpendView, normalized))

    def cache_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": 5,
            "active_view": self.view,
        }
        if self.view == "organization":
            payload["organization_ids"] = list(self.organization_ids)
        elif self.view == "team":
            payload["team_ids"] = list(self.team_ids)
        elif self.view == "self":
            payload.update({
                "owner_account_id": self.owner_account_id,
                "organization_ids": list(self.self_organization_ids),
                "team_ids": list(self.self_team_ids),
            })
        return payload

    def capabilities(self, *, user_identity_labels: bool) -> dict[str, Any]:
        return {
            # visibility_level is retained for existing clients while the
            # explicit view fields provide the scope-switching contract.
            "visibility_level": self.view,
            "active_view": self.view,
            "default_view": self.default_view,
            "available_views": list(self.available_views),
            "self_scoped": self.is_self_only,
            "allowed_dimensions": list(self.allowed_dimensions),
            "request_logs": self.can_view_request_logs,
            "user_identity_labels": user_identity_labels,
        }


def resolve_spend_visibility(
    scope: Any,
    requested_view: str | None = None,
    *,
    scoped_views_enabled: bool = False,
) -> SpendVisibility:
    if bool(getattr(scope, "is_platform_admin", False)):
        return SpendVisibility(is_platform_admin=True).select_view(requested_view)

    org_permissions = getattr(scope, "org_permissions_by_id", {}) or {}
    team_permissions = getattr(scope, "team_permissions_by_id", {}) or {}
    effective_permissions = set(getattr(scope, "effective_permissions", set()) or set())

    organization_ids = tuple(sorted(
        str(organization_id)
        for organization_id, permissions in org_permissions.items()
        if organization_id and Permission.SPEND_READ in permissions
    ))
    team_ids = tuple(sorted(
        str(team_id)
        for team_id, permissions in team_permissions.items()
        if scoped_views_enabled and team_id and Permission.SPEND_READ_TEAM in permissions
    ))
    self_organization_ids = tuple(sorted(
        str(organization_id)
        for organization_id, permissions in org_permissions.items()
        if scoped_views_enabled and organization_id and Permission.SPEND_READ_SELF in permissions
    ))
    self_team_ids = tuple(sorted(
        str(team_id)
        for team_id, permissions in team_permissions.items()
        if scoped_views_enabled and team_id and Permission.SPEND_READ_SELF in permissions
    ))

    # Compatibility for tests and legacy integrations that construct an
    # AuthScope with already-authorized IDs but without permission maps.
    if not org_permissions and getattr(scope, "org_ids", None):
        organization_ids = tuple(sorted({str(value) for value in scope.org_ids if value}))
    if scoped_views_enabled and not team_permissions and getattr(scope, "team_ids", None):
        team_ids = tuple(sorted({str(value) for value in scope.team_ids if value}))

    account_id = str(getattr(scope, "account_id", "") or "").strip() or None
    owner_account_id = (
        account_id
        if account_id
        and Permission.SPEND_READ_SELF in effective_permissions
        and (self_organization_ids or self_team_ids)
        else None
    )

    return SpendVisibility(
        is_platform_admin=False,
        organization_ids=organization_ids,
        team_ids=team_ids,
        self_organization_ids=self_organization_ids,
        self_team_ids=self_team_ids,
        owner_account_id=owner_account_id,
    ).select_view(requested_view)


def _append_in_predicate(
    *,
    predicates: list[str],
    params: list[Any],
    column: str,
    values: tuple[str, ...],
) -> None:
    if not values:
        return
    placeholders = ", ".join(
        f"${len(params) + index + 1}"
        for index in range(len(values))
    )
    params.extend(values)
    predicates.append(f"{column} IN ({placeholders})")


def apply_spend_visibility(
    *,
    clauses: list[str],
    params: list[Any],
    visibility: SpendVisibility,
    source: SpendReadSource,
    table_alias: str | None = None,
) -> None:
    if visibility.view == "platform" and visibility.is_platform_admin:
        return

    if visibility.view == "organization":
        org_column = source.column("organization_column", table_alias=table_alias)
        predicates: list[str] = []
        _append_in_predicate(
            predicates=predicates,
            params=params,
            column=org_column,
            values=visibility.organization_ids,
        )
        clauses.append(predicates[0] if predicates else "1 = 0")
        return

    if visibility.view == "team":
        team_column = "team_id" if table_alias is None else f"{table_alias}.team_id"
        predicates = []
        _append_in_predicate(
            predicates=predicates,
            params=params,
            column=team_column,
            values=visibility.team_ids,
        )
        clauses.append(predicates[0] if predicates else "1 = 0")
        return

    if visibility.view != "self" or not visibility.owner_account_id or not source.owner_account_column:
        clauses.append("1 = 0")
        return

    owner_column = source.column("owner_account_column", table_alias=table_alias)
    params.append(visibility.owner_account_id)
    owner_predicate = f"{owner_column} = ${len(params)}"
    membership_predicates: list[str] = []
    org_column = source.column("organization_column", table_alias=table_alias)
    _append_in_predicate(
        predicates=membership_predicates,
        params=params,
        column=org_column,
        values=visibility.self_organization_ids,
    )
    team_column = "team_id" if table_alias is None else f"{table_alias}.team_id"
    _append_in_predicate(
        predicates=membership_predicates,
        params=params,
        column=team_column,
        values=visibility.self_team_ids,
    )
    if not membership_predicates:
        clauses.append("1 = 0")
        return
    clauses.append(f"({owner_predicate} AND ({' OR '.join(membership_predicates)}))")
