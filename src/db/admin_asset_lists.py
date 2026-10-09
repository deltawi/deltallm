from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from src.services.admin_list_health import ListHealthSnapshot

ListDirection = Literal["asc", "desc"]
GroupSortKey = Literal[
    "name", "routing", "members", "health", "created_by", "updated_at", "visibility"
]
PromptSortKey = Literal[
    "name", "versions", "labels", "bindings", "created_by", "updated_at", "visibility"
]


class ListQueryReader(Protocol):
    async def query_raw(self, query: str, *params: object) -> list[dict[str, object]]: ...


@dataclass(frozen=True)
class AssetListRows:
    rows: list[dict[str, object]]
    total: int


_GRANTS_JOIN = """
LEFT JOIN deltallm_managedasset asset ON asset.asset_id = {alias}.managed_asset_id
LEFT JOIN LATERAL (
    SELECT CASE
        WHEN COUNT(*) = 0 THEN 'private'
        WHEN COUNT(DISTINCT subject_type) > 1 THEN 'shared'
        ELSE MIN(subject_type)
    END AS visibility
    FROM deltallm_assetgrant
    WHERE managed_asset_id = asset.asset_id
) audience ON TRUE
"""

_GROUP_STATS = """
LEFT JOIN LATERAL (
    SELECT COUNT(*)::int AS member_count,
           COUNT(*) FILTER (WHERE m.enabled)::int AS active_member_count,
           COUNT(*) FILTER (WHERE m.enabled AND m.deployment_id = ANY({healthy}::text[]))::int AS healthy_member_count,
           COUNT(*) FILTER (WHERE m.enabled AND (NOT {known} OR m.deployment_id = ANY({unknown}::text[])))::int AS unknown_member_count
    FROM deltallm_routegroupmember m
    WHERE m.route_group_id = g.route_group_id
) members ON TRUE
"""

_GROUP_HEALTH = """
CASE WHEN NOT g.enabled THEN 'paused'
     WHEN members.active_member_count = 0 THEN 'empty'
     WHEN members.unknown_member_count > 0 THEN 'unknown'
     WHEN members.healthy_member_count = 0 THEN 'unhealthy'
     WHEN members.healthy_member_count < members.active_member_count THEN 'degraded'
     ELSE 'healthy' END
"""

_GROUP_SORT = {
    "name": "COALESCE(name, group_key)",
    "routing": "COALESCE(routing_strategy, 'simple-shuffle')",
    "members": "member_count",
    "health": "health_rank",
    "created_by": "created_by_user_id",
    "updated_at": "updated_at",
    "visibility": "visibility",
    "created_at": "created_at",
}
_PROMPT_SORT = {
    "name": "name",
    "versions": "version_count",
    "labels": "label_count",
    "bindings": "binding_count",
    "created_by": "created_by_user_id",
    "updated_at": "updated_at",
    "visibility": "visibility",
    "created_at": "created_at",
}


async def list_asset_rows(
    reader: ListQueryReader,
    *,
    kind: Literal["group", "prompt"],
    search: str | None,
    limit: int,
    offset: int,
    managed_asset_ids: list[str] | None,
    sort_by: GroupSortKey | PromptSortKey | Literal["created_at"],
    sort_direction: ListDirection,
    health: ListHealthSnapshot | None = None,
) -> AssetListRows:
    columns = _GROUP_SORT if kind == "group" else _PROMPT_SORT
    if sort_by not in columns or sort_direction not in {"asc", "desc"}:
        raise ValueError("Unsupported list sort")
    if managed_asset_ids is not None and not managed_asset_ids:
        return AssetListRows([], 0)
    alias, table, key = (
        ("g", "deltallm_routegroup", "group_key")
        if kind == "group"
        else ("t", "deltallm_prompttemplate", "template_key")
    )
    params: list[object] = []
    clauses: list[str] = []
    if search:
        params.append(f"%{search}%")
        token = f"${len(params)}"
        extra = f" OR COALESCE({alias}.description, '') ILIKE {token}" if kind == "prompt" else ""
        clauses.append(
            f"({alias}.{key} ILIKE {token} OR COALESCE({alias}.name, '') ILIKE {token}{extra})"
        )
    if managed_asset_ids is not None:
        params.append(managed_asset_ids)
        clauses.append(f"{alias}.managed_asset_id = ANY(${len(params)}::text[])")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    counts = await reader.query_raw(
        f"SELECT COUNT(*)::int AS total FROM {table} {alias}{where}", *params
    )
    total = int(str(counts[0]["total"])) if counts else 0
    select, joins = _list_select(kind, alias, params, health)
    params.extend([limit, offset])
    rows = await reader.query_raw(
        f"""SELECT * FROM (
            SELECT {select} FROM {table} {alias}
            {joins} {where}
        ) listed
        ORDER BY {columns[sort_by]} {sort_direction.upper()} NULLS LAST, {key} ASC
        LIMIT ${len(params) - 1} OFFSET ${len(params)}""",
        *params,
    )
    return AssetListRows(rows, total)


def _list_select(
    kind: Literal["group", "prompt"],
    alias: str,
    params: list[object],
    health: ListHealthSnapshot | None,
) -> tuple[str, str]:
    select = f"{alias}.*, asset.created_by_account_id AS created_by_user_id, CASE WHEN asset.asset_id IS NULL THEN 'platform' ELSE audience.visibility END AS visibility"
    joins = _GRANTS_JOIN.format(alias=alias)
    if kind == "group":
        snapshot = health or ListHealthSnapshot([], [])
        params.extend([snapshot.healthy_ids, snapshot.unknown_ids, health is not None])
        joins += _GROUP_STATS.format(
            healthy=f"${len(params) - 2}", unknown=f"${len(params) - 1}", known=f"${len(params)}"
        )
        select += f""", members.member_count, members.active_member_count,
            members.healthy_member_count, {_GROUP_HEALTH} AS health_status,
            CASE ({_GROUP_HEALTH}) WHEN 'unhealthy' THEN 0 WHEN 'degraded' THEN 1
                 WHEN 'unknown' THEN 2 WHEN 'empty' THEN 3 WHEN 'paused' THEN 4 ELSE 5 END AS health_rank"""
    else:
        for table, column, field in [
            ("deltallm_promptversion", "prompt_template_id", "version_count"),
            ("deltallm_promptlabel", "prompt_template_id", "label_count"),
            ("deltallm_promptbinding", "prompt_template_id", "binding_count"),
        ]:
            select += f", (SELECT COUNT(*)::int FROM {table} child WHERE child.{column} = t.prompt_template_id) AS {field}"
    return select, joins
