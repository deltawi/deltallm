"""Filters and sort order for the admin API key list."""

from __future__ import annotations

from typing import Any, Literal

KeyOwnerFilter = Literal["person", "service_account", "unassigned"]
KeyStatusFilter = Literal["active", "expired", "expiring", "budget_exhausted"]
KeyLastUsedFilter = Literal["never", "active_7d", "active_30d", "inactive_30d", "inactive_90d"]
KeySortKey = Literal["created_at", "name", "last_used", "expires", "budget_used"]
SortDirection = Literal["asc", "desc"]

# Keys that expire within this window count as "expiring".
EXPIRING_SOON_DAYS = 7

# With native accounting the key row no longer holds the live balance; finite
# budgets read the current committed window instead, as the list response does.
_BUDGET_WINDOW_JOIN = """
    LEFT JOIN LATERAL (
        SELECT w.committed_exact
        FROM deltallm_accounting_protocols p
        JOIN deltallm_accounting_budget_windows w
          ON w.protocol_name = p.protocol_name AND w.generation = p.generation
        WHERE p.protocol_name = 'primary' AND p.state = 'active'
          AND w.scope_type = 'api_key' AND w.scope_id = vt.token
          AND w.window_starts_at <= CURRENT_TIMESTAMP AND w.window_ends_at > CURRENT_TIMESTAMP
        ORDER BY w.window_id LIMIT 1
    ) bw ON TRUE
"""


def _spend_sql(native_accounting: bool) -> str:
    if native_accounting:
        return "COALESCE(bw.committed_exact, vt.spend_exact, vt.spend::numeric)"
    return "vt.spend::numeric"


def needs_budget_window(
    *, native_accounting: bool, status: KeyStatusFilter | None, sort_by: KeySortKey | None
) -> bool:
    return native_accounting and (status == "budget_exhausted" or sort_by == "budget_used")


def budget_window_join() -> str:
    return _BUDGET_WINDOW_JOIN


def append_key_list_filters(
    clauses: list[str],
    params: list[Any],
    *,
    organization_id: str | None,
    owner: KeyOwnerFilter | None,
    owner_account_id: str | None,
    status: KeyStatusFilter | None,
    last_used: KeyLastUsedFilter | None,
    native_accounting: bool,
) -> None:
    if organization_id:
        params.append(organization_id)
        clauses.append(f"t.organization_id = ${len(params)}")
    if owner_account_id:
        params.append(owner_account_id)
        clauses.append(f"vt.owner_account_id = ${len(params)}")
    if owner == "person":
        clauses.append("vt.owner_service_account_id IS NULL AND vt.owner_account_id IS NOT NULL")
    elif owner == "service_account":
        clauses.append("vt.owner_service_account_id IS NOT NULL")
    elif owner == "unassigned":
        clauses.append("vt.owner_service_account_id IS NULL AND vt.owner_account_id IS NULL")
    if status == "active":
        clauses.append("(vt.expires IS NULL OR vt.expires > NOW())")
    elif status == "expired":
        clauses.append("vt.expires <= NOW()")
    elif status == "expiring":
        clauses.append(
            f"vt.expires > NOW() AND vt.expires <= NOW() + INTERVAL '{EXPIRING_SOON_DAYS} days'"
        )
    elif status == "budget_exhausted":
        clauses.append(
            f"vt.max_budget IS NOT NULL AND {_spend_sql(native_accounting)} >= vt.max_budget::numeric"
        )
    if last_used == "never":
        clauses.append("lu.last_used_at IS NULL")
    elif last_used in ("active_7d", "active_30d"):
        days = 7 if last_used == "active_7d" else 30
        clauses.append(f"lu.last_used_at >= NOW() - INTERVAL '{days} days'")
    elif last_used in ("inactive_30d", "inactive_90d"):
        days = 30 if last_used == "inactive_30d" else 90
        clauses.append(
            f"(lu.last_used_at IS NULL OR lu.last_used_at < NOW() - INTERVAL '{days} days')"
        )


def key_list_order_sql(
    sort_by: KeySortKey, direction: SortDirection, *, native_accounting: bool
) -> str:
    order = "ASC" if direction == "asc" else "DESC"
    if sort_by == "name":
        expression = "lower(vt.key_name)"
    elif sort_by == "last_used":
        expression = "lu.last_used_at"
    elif sort_by == "expires":
        expression = "vt.expires"
    elif sort_by == "budget_used":
        expression = f"CASE WHEN vt.max_budget > 0 THEN {_spend_sql(native_accounting)} / vt.max_budget::numeric END"
    else:
        expression = "vt.created_at"
    # Empty values (never used, no expiry, no budget) always go last, and the
    # token keeps page boundaries stable when values tie.
    return f"ORDER BY {expression} {order} NULLS LAST, vt.created_at DESC, vt.token ASC"
