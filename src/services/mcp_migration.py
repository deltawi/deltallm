from __future__ import annotations

from typing import Any

from src.db.mcp import MCPServerRecord

ORGANIZATION_ROLLOUT_STATES = {
    "needs_org_bootstrap",
    "needs_scope_backfill",
    "ready_for_enforce",
}

ROLLOUT_STATE_ALIASES = {
    "ready_for_shadow": "ready_for_enforce",
}


async def build_mcp_migration_report(
    *,
    db: Any,
    repository: Any,
    policy_repository: Any | None,
    organization_id: str | None = None,
    rollout_states: set[str] | None = None,
) -> dict[str, Any]:
    org_rows = await _list_organizations(db, organization_id=organization_id)
    team_rows = await _list_teams(db, organization_id=organization_id)
    key_rows = await _list_keys(db, organization_id=organization_id)
    user_rows = await _list_users(db, organization_id=organization_id)

    enabled_servers = await _list_enabled_servers(repository)
    servers_by_id = {server.mcp_server_id: server for server in enabled_servers}

    org_bindings = await _list_mcp_bindings_by_scope(repository, scope_type="organization")
    team_bindings = await _list_mcp_bindings_by_scope(repository, scope_type="team")
    key_bindings = await _list_mcp_bindings_by_scope(repository, scope_type="api_key")
    user_bindings = await _list_mcp_bindings_by_scope(repository, scope_type="user")
    team_policies = await _list_scope_policies(policy_repository, scope_type="team")
    key_policies = await _list_scope_policies(policy_repository, scope_type="api_key")
    user_policies = await _list_scope_policies(policy_repository, scope_type="user")

    teams_by_org: dict[str, list[dict[str, Any]]] = {}
    for row in team_rows:
        teams_by_org.setdefault(str(row["organization_id"]), []).append(row)

    keys_by_org: dict[str, list[dict[str, Any]]] = {}
    for row in key_rows:
        keys_by_org.setdefault(str(row["organization_id"]), []).append(row)

    users_by_org: dict[str, list[dict[str, Any]]] = {}
    for row in user_rows:
        users_by_org.setdefault(str(row["organization_id"]), []).append(row)

    filters = _normalize_rollout_states(rollout_states)
    organizations: list[dict[str, Any]] = []

    for org_row in org_rows:
        org_id = str(org_row["organization_id"])
        existing_org_server_ids = sorted(org_bindings.get(org_id, set()))

        team_items = _build_child_scope_items(
            rows=teams_by_org.get(org_id, []),
            bindings_by_scope=team_bindings,
            policies_by_scope=team_policies,
            scope_key="team_id",
            label_key="team_alias",
            scope_type="team",
            servers_by_id=servers_by_id,
        )
        key_items = _build_child_scope_items(
            rows=keys_by_org.get(org_id, []),
            bindings_by_scope=key_bindings,
            policies_by_scope=key_policies,
            scope_key="token",
            label_key="key_name",
            scope_type="api_key",
            servers_by_id=servers_by_id,
            extra_keys=("team_id",),
        )
        user_items = _build_child_scope_items(
            rows=users_by_org.get(org_id, []),
            bindings_by_scope=user_bindings,
            policies_by_scope=user_policies,
            scope_key="user_id",
            label_key="user_email",
            scope_type="user",
            servers_by_id=servers_by_id,
            extra_keys=("team_id",),
        )

        desired_org_server_ids = sorted(
            {
                *(
                    server.mcp_server_id
                    for server in enabled_servers
                    if server.owner_scope_type == "organization" and server.owner_scope_id == org_id
                ),
                *(server_id for item in team_items for server_id in item["binding_server_ids"]),
                *(server_id for item in key_items for server_id in item["binding_server_ids"]),
                *(server_id for item in user_items for server_id in item["binding_server_ids"]),
            }
        )
        missing_org_server_ids = sorted(set(desired_org_server_ids) - set(existing_org_server_ids))
        org_rollout_state = _classify_org_rollout_state(
            missing_org_server_ids=missing_org_server_ids,
            child_items=[*team_items, *key_items, *user_items],
        )
        if filters and org_rollout_state not in filters:
            continue

        organizations.append(
            {
                "organization_id": org_id,
                "organization_name": org_row.get("organization_name"),
                "org_binding_count": len(existing_org_server_ids),
                "org_binding_server_ids": existing_org_server_ids,
                "org_binding_server_keys": _server_keys(existing_org_server_ids, servers_by_id),
                "desired_org_server_ids": desired_org_server_ids,
                "desired_org_server_keys": _server_keys(desired_org_server_ids, servers_by_id),
                "missing_org_server_ids": missing_org_server_ids,
                "missing_org_server_keys": _server_keys(missing_org_server_ids, servers_by_id),
                "rollout_state": org_rollout_state,
                "teams": team_items,
                "api_keys": key_items,
                "users": user_items,
            }
        )

    return {
        "organization_id": organization_id,
        "filters": {"rollout_states": sorted(filters) if filters else []},
        "server_catalog": {
            "total": len(enabled_servers),
            "server_keys": sorted(server.server_key for server in enabled_servers),
        },
        "summary": _build_summary(organizations),
        "organizations": organizations,
    }


async def apply_mcp_migration_backfill(
    *,
    db: Any,
    repository: Any,
    policy_repository: Any,
    organization_id: str | None = None,
    rollout_states: set[str] | None = None,
) -> dict[str, Any]:
    initial_report = await build_mcp_migration_report(
        db=db,
        repository=repository,
        policy_repository=policy_repository,
        organization_id=organization_id,
        rollout_states=rollout_states,
    )

    applied = {
        "organizations_bootstrapped": 0,
        "organization_bindings_created": 0,
        "team_policies_created": 0,
        "api_key_policies_created": 0,
        "user_policies_created": 0,
    }

    for organization in initial_report["organizations"]:
        org_id = str(organization["organization_id"])
        for server_id in organization.get("missing_org_server_ids") or []:
            binding = await repository.upsert_binding(
                server_id=server_id,
                scope_type="organization",
                scope_id=org_id,
                enabled=True,
                tool_allowlist=None,
                metadata={"source": "mcp_org_ceiling_backfill"},
            )
            if binding is not None:
                applied["organization_bindings_created"] += 1
        if organization.get("missing_org_server_ids"):
            applied["organizations_bootstrapped"] += 1

        for item in organization.get("teams") or []:
            if item.get("binding_count", 0) > 0 and item.get("scope_policy_mode") != "restrict":
                await policy_repository.upsert_policy(
                    scope_type="team",
                    scope_id=str(item["team_id"]),
                    mode="restrict",
                    metadata={"source": "mcp_scope_policy_backfill"},
                )
                applied["team_policies_created"] += 1

        for item in organization.get("api_keys") or []:
            if item.get("binding_count", 0) > 0 and item.get("scope_policy_mode") != "restrict":
                await policy_repository.upsert_policy(
                    scope_type="api_key",
                    scope_id=str(item["token"]),
                    mode="restrict",
                    metadata={"source": "mcp_scope_policy_backfill"},
                )
                applied["api_key_policies_created"] += 1

        for item in organization.get("users") or []:
            if item.get("binding_count", 0) > 0 and item.get("scope_policy_mode") != "restrict":
                await policy_repository.upsert_policy(
                    scope_type="user",
                    scope_id=str(item["user_id"]),
                    mode="restrict",
                    metadata={"source": "mcp_scope_policy_backfill"},
                )
                applied["user_policies_created"] += 1

    report = await build_mcp_migration_report(
        db=db,
        repository=repository,
        policy_repository=policy_repository,
        organization_id=organization_id,
        rollout_states=None,
    )
    report["applied"] = applied
    return report


async def _list_enabled_servers(repository: Any) -> list[MCPServerRecord]:
    if repository is None:
        return []
    items: list[MCPServerRecord] = []
    offset = 0
    limit = 500
    while True:
        page, total = await repository.list_servers(enabled=True, limit=limit, offset=offset)
        items.extend(page)
        offset += len(page)
        if not page or offset >= total:
            break
    return items


async def _list_mcp_bindings_by_scope(repository: Any, *, scope_type: str) -> dict[str, set[str]]:
    if repository is None:
        return {}
    bindings_by_scope: dict[str, set[str]] = {}
    offset = 0
    limit = 1000
    while True:
        page, total = await repository.list_bindings(scope_type=scope_type, limit=limit, offset=offset)
        for binding in page:
            scope_id = str(binding.scope_id or "").strip()
            server_id = str(binding.mcp_server_id or "").strip()
            if not scope_id or not server_id or not binding.enabled:
                continue
            bindings_by_scope.setdefault(scope_id, set()).add(server_id)
        offset += len(page)
        if not page or offset >= total:
            break
    return bindings_by_scope


async def _list_scope_policies(repository: Any | None, *, scope_type: str) -> dict[str, str]:
    if repository is None:
        return {}
    policies_by_scope: dict[str, str] = {}
    offset = 0
    limit = 1000
    while True:
        page, total = await repository.list_policies(scope_type=scope_type, limit=limit, offset=offset)
        for policy in page:
            scope_id = str(policy.scope_id or "").strip()
            if scope_id:
                policies_by_scope[scope_id] = str(policy.mode or "inherit")
        offset += len(page)
        if not page or offset >= total:
            break
    return policies_by_scope


def _build_child_scope_items(
    *,
    rows: list[dict[str, Any]],
    bindings_by_scope: dict[str, set[str]],
    policies_by_scope: dict[str, str],
    scope_key: str,
    label_key: str,
    scope_type: str,
    servers_by_id: dict[str, MCPServerRecord],
    extra_keys: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in rows:
        scope_id = str(row[scope_key])
        binding_server_ids = sorted(bindings_by_scope.get(scope_id, set()))
        scope_policy_mode = policies_by_scope.get(scope_id, "inherit")
        item = {
            scope_key: row[scope_key],
            label_key: row.get(label_key),
            "binding_count": len(binding_server_ids),
            "binding_server_ids": binding_server_ids,
            "binding_server_keys": _server_keys(binding_server_ids, servers_by_id),
            "scope_policy_mode": scope_policy_mode,
            "rollout_state": _classify_child_scope_rollout_state(
                binding_count=len(binding_server_ids),
                scope_policy_mode=scope_policy_mode,
            ),
        }
        for extra_key in extra_keys:
            item[extra_key] = row.get(extra_key)
        items.append(item)
    items.sort(key=lambda item: str(item[scope_key]))
    return items


def _server_keys(server_ids: list[str], servers_by_id: dict[str, MCPServerRecord]) -> list[str]:
    out: list[str] = []
    for server_id in server_ids:
        server = servers_by_id.get(server_id)
        if server is not None:
            out.append(server.server_key)
    return sorted(out)


def _classify_child_scope_rollout_state(*, binding_count: int, scope_policy_mode: str) -> str:
    if binding_count == 0:
        return "ready_for_enforce"
    if scope_policy_mode != "restrict":
        return "needs_scope_backfill"
    return "ready_for_enforce"


def _classify_org_rollout_state(*, missing_org_server_ids: list[str], child_items: list[dict[str, Any]]) -> str:
    if missing_org_server_ids:
        return "needs_org_bootstrap"
    if any(str(item.get("rollout_state") or "") == "needs_scope_backfill" for item in child_items):
        return "needs_scope_backfill"
    return "ready_for_enforce"


def _build_summary(organizations: list[dict[str, Any]]) -> dict[str, Any]:
    organizations_by_rollout_state = {state: 0 for state in sorted(ORGANIZATION_ROLLOUT_STATES)}
    organization_ids_by_rollout_state = {state: [] for state in sorted(ORGANIZATION_ROLLOUT_STATES)}
    teams_needing_scope_backfill = 0
    api_keys_needing_scope_backfill = 0
    users_needing_scope_backfill = 0

    for organization in organizations:
        rollout_state = str(organization.get("rollout_state") or "ready_for_enforce")
        organizations_by_rollout_state.setdefault(rollout_state, 0)
        organizations_by_rollout_state[rollout_state] += 1
        organization_ids_by_rollout_state.setdefault(rollout_state, [])
        organization_ids_by_rollout_state[rollout_state].append(str(organization.get("organization_id") or ""))

        for team in organization.get("teams") or []:
            if team.get("rollout_state") == "needs_scope_backfill":
                teams_needing_scope_backfill += 1
        for api_key in organization.get("api_keys") or []:
            if api_key.get("rollout_state") == "needs_scope_backfill":
                api_keys_needing_scope_backfill += 1
        for user in organization.get("users") or []:
            if user.get("rollout_state") == "needs_scope_backfill":
                users_needing_scope_backfill += 1

    return {
        "organizations_total": len(organizations),
        "organizations_needing_bootstrap": organizations_by_rollout_state.get("needs_org_bootstrap", 0),
        "teams_needing_scope_backfill": teams_needing_scope_backfill,
        "api_keys_needing_scope_backfill": api_keys_needing_scope_backfill,
        "users_needing_scope_backfill": users_needing_scope_backfill,
        "organizations_by_rollout_state": organizations_by_rollout_state,
        "organization_ids_by_rollout_state": organization_ids_by_rollout_state,
    }


def _normalize_rollout_states(rollout_states: set[str] | None) -> set[str]:
    if not rollout_states:
        return set()
    normalized = {
        ROLLOUT_STATE_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())
        for value in rollout_states
        if str(value or "").strip()
    }
    return {value for value in normalized if value in ORGANIZATION_ROLLOUT_STATES}


async def _list_organizations(db: Any, *, organization_id: str | None) -> list[dict[str, Any]]:
    if organization_id:
        rows = await db.query_raw(
            """
            SELECT organization_id, organization_name
            FROM deltallm_organizationtable
            WHERE organization_id = $1
            ORDER BY organization_id ASC
            """,
            organization_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT organization_id, organization_name
            FROM deltallm_organizationtable
            ORDER BY organization_id ASC
            """
        )
    return [dict(row) for row in rows]


async def _list_teams(db: Any, *, organization_id: str | None) -> list[dict[str, Any]]:
    if organization_id:
        rows = await db.query_raw(
            """
            SELECT team_id, team_alias, organization_id
            FROM deltallm_teamtable
            WHERE organization_id = $1
            ORDER BY organization_id ASC, team_id ASC
            """,
            organization_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT team_id, team_alias, organization_id
            FROM deltallm_teamtable
            ORDER BY organization_id ASC, team_id ASC
            """
        )
    return [dict(row) for row in rows]


async def _list_keys(db: Any, *, organization_id: str | None) -> list[dict[str, Any]]:
    if organization_id:
        rows = await db.query_raw(
            """
            SELECT vt.token, vt.key_name, vt.team_id, t.organization_id
            FROM deltallm_verificationtoken vt
            JOIN deltallm_teamtable t ON vt.team_id = t.team_id
            WHERE t.organization_id = $1
            ORDER BY t.organization_id ASC, vt.token ASC
            """,
            organization_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT vt.token, vt.key_name, vt.team_id, t.organization_id
            FROM deltallm_verificationtoken vt
            JOIN deltallm_teamtable t ON vt.team_id = t.team_id
            ORDER BY t.organization_id ASC, vt.token ASC
            """
        )
    return [dict(row) for row in rows]


async def _list_users(db: Any, *, organization_id: str | None) -> list[dict[str, Any]]:
    if organization_id:
        rows = await db.query_raw(
            """
            SELECT DISTINCT ON (u.user_id, t.organization_id)
                u.user_id,
                u.user_email,
                COALESCE(u.team_id, vt.team_id) AS team_id,
                t.organization_id
            FROM deltallm_usertable u
            LEFT JOIN deltallm_verificationtoken vt
                ON vt.user_id = u.user_id
            JOIN deltallm_teamtable t
                ON t.team_id = COALESCE(u.team_id, vt.team_id)
            WHERE t.organization_id = $1
            ORDER BY u.user_id ASC, t.organization_id ASC, COALESCE(u.team_id, vt.team_id) ASC
            """,
            organization_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT DISTINCT ON (u.user_id, t.organization_id)
                u.user_id,
                u.user_email,
                COALESCE(u.team_id, vt.team_id) AS team_id,
                t.organization_id
            FROM deltallm_usertable u
            LEFT JOIN deltallm_verificationtoken vt
                ON vt.user_id = u.user_id
            JOIN deltallm_teamtable t
                ON t.team_id = COALESCE(u.team_id, vt.team_id)
            ORDER BY u.user_id ASC, t.organization_id ASC, COALESCE(u.team_id, vt.team_id) ASC
            """
        )
    return [dict(row) for row in rows]
