"""Names and current state for audit events.

Audit rows store IDs only. These helpers look up display names for the rows on
one page (a few indexed lookups per kind, never a scan) and, for one open
event, the current state of the things it touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.api.admin.endpoints.common import to_json_value

# Resource types whose names live in a table, keyed by the ID audit rows store.
_RESOURCE_NAME_QUERIES: dict[str, str] = {
    "model_deployment": "SELECT deployment_id AS id, model_name AS name FROM deltallm_modeldeployment WHERE deployment_id = ANY($1::text[])",
    "route_group": "SELECT group_key AS id, COALESCE(name, group_key) AS name FROM deltallm_routegroup WHERE group_key = ANY($1::text[])",
    "mcp_server": "SELECT mcp_server_id AS id, name FROM deltallm_mcpserver WHERE mcp_server_id = ANY($1::text[])",
    "prompt_template": "SELECT template_key AS id, name FROM deltallm_prompttemplate WHERE template_key = ANY($1::text[])",
    "named_credential": "SELECT credential_id AS id, name FROM deltallm_namedcredential WHERE credential_id = ANY($1::text[])",
    "tier": "SELECT tier_id AS id, name FROM deltallm_tier WHERE tier_id = ANY($1::text[])",
}


def _ids(values: Any) -> list[str]:
    return sorted({str(value) for value in values if value})


async def _rows(db: Any, query: str, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    return [dict(row) for row in await db.query_raw(query, ids)]


def _visible_email(
    account_id: str,
    email: str | None,
    memberships: dict[str, set[str]],
    scope: Any,
) -> str | None:
    """Platform admins see every email; others see their own and their organizations' members."""
    if not email:
        return None
    if getattr(scope, "is_platform_admin", False) or account_id == getattr(scope, "account_id", None):
        return email
    visible_orgs = set(getattr(scope, "org_ids", []) or [])
    return email if memberships.get(account_id, set()) & visible_orgs else None


async def _accounts(db: Any, account_ids: list[str], scope: Any) -> dict[str, dict[str, Any]]:
    rows = await _rows(
        db,
        "SELECT account_id, email, role, is_active FROM deltallm_platformaccount WHERE account_id = ANY($1::text[])",
        account_ids,
    )
    membership_rows = await _rows(
        db,
        "SELECT account_id, organization_id, role FROM deltallm_organizationmembership WHERE account_id = ANY($1::text[])",
        [str(row["account_id"]) for row in rows],
    )
    memberships: dict[str, set[str]] = {}
    org_roles: dict[str, dict[str, str]] = {}
    for row in membership_rows:
        memberships.setdefault(str(row["account_id"]), set()).add(str(row["organization_id"]))
        org_roles.setdefault(str(row["account_id"]), {})[str(row["organization_id"])] = str(row["role"])
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        account_id = str(row["account_id"])
        result[account_id] = {
            "email": _visible_email(account_id, row.get("email"), memberships, scope),
            "role": row.get("role"),
            "is_active": row.get("is_active"),
            "org_roles": org_roles.get(account_id, {}),
        }
    return result


async def _keys(db: Any, hashes: list[str]) -> dict[str, dict[str, Any]]:
    rows = await _rows(
        db,
        "SELECT token, key_name, team_id, owner_account_id, expires FROM deltallm_verificationtoken WHERE token = ANY($1::text[])",
        hashes,
    )
    return {str(row["token"]): row for row in rows}


async def _names(db: Any, query: str, ids: list[str]) -> dict[str, str]:
    return {str(row["id"]): str(row["name"]) for row in await _rows(db, query, ids) if row.get("name")}


def _key_hash(event: dict[str, Any]) -> str | None:
    if event.get("api_key"):
        return str(event["api_key"])
    if event.get("actor_type") == "api_key" and event.get("actor_id"):
        return str(event["actor_id"])
    return None


async def label_events(db: Any, events: list[dict[str, Any]], scope: Any) -> None:
    """Add a ``labels`` object (display names) to each event in place."""
    account_ids = _ids(
        [event.get("actor_id") for event in events if event.get("actor_type") == "platform_account"]
        + [event.get("resource_id") for event in events if event.get("resource_type") == "account"]
    )
    key_hashes = _ids(
        [_key_hash(event) for event in events]
        + [event.get("resource_id") for event in events if event.get("resource_type") == "api_key"]
    )
    accounts = await _accounts(db, account_ids, scope)
    keys = await _keys(db, key_hashes)
    team_ids = _ids(
        [row.get("team_id") for row in keys.values()]
        + [event.get("resource_id") for event in events if event.get("resource_type") == "team"]
    )
    teams = await _names(
        db,
        "SELECT team_id AS id, team_alias AS name FROM deltallm_teamtable WHERE team_id = ANY($1::text[])",
        team_ids,
    )
    org_ids = _ids(
        [event.get("organization_id") for event in events]
        + [event.get("resource_id") for event in events if event.get("resource_type") == "organization"]
    )
    orgs = await _names(
        db,
        "SELECT organization_id AS id, organization_name AS name FROM deltallm_organizationtable WHERE organization_id = ANY($1::text[])",
        org_ids,
    )
    resource_names: dict[str, dict[str, str]] = {}
    for resource_type, query in _RESOURCE_NAME_QUERIES.items():
        ids = _ids(event.get("resource_id") for event in events if event.get("resource_type") == resource_type)
        resource_names[resource_type] = await _names(db, query, ids)

    for event in events:
        key = keys.get(_key_hash(event) or "")
        team_id = str(key["team_id"]) if key and key.get("team_id") else None
        actor_label: str | None = None
        if event.get("actor_type") == "platform_account":
            actor_label = (accounts.get(str(event.get("actor_id"))) or {}).get("email")
        elif event.get("actor_type") == "api_key" and key:
            actor_label = key.get("key_name")
        resource_type = event.get("resource_type")
        resource_id = str(event.get("resource_id") or "")
        resource_label: str | None = None
        if resource_type == "api_key":
            resource_label = ((keys.get(resource_id) or {}).get("key_name")) if resource_id else None
        elif resource_type == "team":
            resource_label = teams.get(resource_id)
        elif resource_type == "organization":
            resource_label = orgs.get(resource_id)
        elif resource_type == "account":
            resource_label = (accounts.get(resource_id) or {}).get("email")
        elif resource_type in resource_names:
            resource_label = resource_names[resource_type].get(resource_id)
        event["labels"] = {
            "actor": actor_label,
            "key_name": key.get("key_name") if key else None,
            "team_id": team_id,
            "team": teams.get(team_id) if team_id else None,
            "organization": orgs.get(str(event.get("organization_id") or "")),
            "resource": resource_label,
        }


def _key_state(key: dict[str, Any] | None) -> str:
    if key is None:
        return "missing"
    expires = key.get("expires")
    if isinstance(expires, str):
        try:
            expires = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        except ValueError:
            expires = None
    if isinstance(expires, datetime):
        moment = expires if expires.tzinfo else expires.replace(tzinfo=UTC)
        if moment <= datetime.now(tz=UTC):
            return "expired"
    return "active"


async def _resource_state(db: Any, resource_type: str | None, resource_id: str | None) -> dict[str, Any] | None:
    """Whether the touched thing still exists, and its state where the table has one."""
    if not resource_type or not resource_id:
        return None
    if resource_type == "api_key":
        rows = await db.query_raw("SELECT expires FROM deltallm_verificationtoken WHERE token = $1 LIMIT 1", resource_id)
        return {"state": _key_state(dict(rows[0]) if rows else None)}
    if resource_type == "batch":
        rows = await db.query_raw(
            "SELECT status::text AS status, total_items, completed_items, failed_items, cancelled_items "
            "FROM deltallm_batch_job WHERE batch_id = $1 LIMIT 1",
            resource_id,
        )
        if not rows:
            return {"state": "missing"}
        row = dict(rows[0])
        total = int(row.get("total_items") or 0)
        done = sum(int(row.get(name) or 0) for name in ("completed_items", "failed_items", "cancelled_items"))
        return {
            "state": str(row.get("status") or "unknown"),
            "progress": round(done * 100 / total) if total else None,
            "total_items": total,
        }
    if resource_type == "organization":
        rows = await db.query_raw(
            "SELECT lifecycle_state FROM deltallm_organizationtable WHERE organization_id = $1 LIMIT 1", resource_id
        )
        return {"state": str(dict(rows[0]).get("lifecycle_state") or "active") if rows else "missing"}
    if resource_type == "account":
        rows = await db.query_raw("SELECT is_active FROM deltallm_platformaccount WHERE account_id = $1 LIMIT 1", resource_id)
        if not rows:
            return {"state": "missing"}
        return {"state": "active" if dict(rows[0]).get("is_active") else "inactive"}
    if resource_type == "model":
        rows = await db.query_raw("SELECT 1 AS found FROM deltallm_modeldeployment WHERE model_name = $1 LIMIT 1", resource_id)
        return {"state": "active" if rows else "missing"}
    if resource_type == "team":
        rows = await db.query_raw("SELECT blocked FROM deltallm_teamtable WHERE team_id = $1 LIMIT 1", resource_id)
        if not rows:
            return {"state": "missing"}
        return {"state": "blocked" if dict(rows[0]).get("blocked") else "active"}
    if resource_type in _RESOURCE_NAME_QUERIES:
        rows = await _rows(db, _RESOURCE_NAME_QUERIES[resource_type], [resource_id])
        return {"state": "active" if rows else "missing"}
    return None


async def event_context(db: Any, event: dict[str, Any], scope: Any) -> dict[str, Any]:
    """Names, the actor's current role, the API key and the resource's current state for one event."""
    copy = dict(event)
    await label_events(db, [copy], scope)
    labels = copy["labels"]
    context: dict[str, Any] = {"labels": labels, "actor": None, "key": None, "resource": None}

    if event.get("actor_type") == "platform_account" and event.get("actor_id"):
        account = (await _accounts(db, [str(event["actor_id"])], scope)).get(str(event["actor_id"]))
        if account is not None:
            org_id = str(event.get("organization_id") or "")
            context["actor"] = {
                "email": account["email"],
                "platform_role": account["role"],
                "organization_role": account["org_roles"].get(org_id) if org_id else None,
                "is_active": account["is_active"],
            }
        else:
            context["actor"] = {"state": "missing"}

    key_hash = _key_hash(event)
    if key_hash:
        key = (await _keys(db, [key_hash])).get(key_hash)
        owner_email = None
        if key and key.get("owner_account_id"):
            owner = (await _accounts(db, [str(key["owner_account_id"])], scope)).get(str(key["owner_account_id"]))
            owner_email = owner["email"] if owner else None
        context["key"] = {
            "name": key.get("key_name") if key else None,
            "tail": key_hash[-4:],
            "team_id": labels["team_id"],
            "team": labels["team"],
            "owner": owner_email,
            "state": _key_state(key),
        }

    resource_state = await _resource_state(db, event.get("resource_type"), event.get("resource_id"))
    if resource_state is not None or labels["resource"]:
        context["resource"] = {"label": labels["resource"], **(resource_state or {})}
        if event.get("resource_type") == "api_key" and event.get("resource_id"):
            target = (await _keys(db, [str(event["resource_id"])])).get(str(event["resource_id"]))
            if target is not None:
                team_id = str(target["team_id"]) if target.get("team_id") else None
                team_names = await _names(
                    db,
                    "SELECT team_id AS id, team_alias AS name FROM deltallm_teamtable WHERE team_id = ANY($1::text[])",
                    [team_id] if team_id else [],
                )
                owner = None
                if target.get("owner_account_id"):
                    owner_row = (await _accounts(db, [str(target["owner_account_id"])], scope)).get(str(target["owner_account_id"]))
                    owner = owner_row["email"] if owner_row else None
                context["resource"].update({"team_id": team_id, "team": team_names.get(team_id or ""), "owner": owner})
    return to_json_value(context)
