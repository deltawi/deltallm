from __future__ import annotations

from typing import Any

ORGANIZATION_ROLLOUT_STATES = {
    "missing_catalog_keys",
    "needs_org_bootstrap",
    "needs_scope_backfill",
    "ready_for_enforce",
}

ROLLOUT_STATE_ALIASES = {
    "ready_for_shadow": "ready_for_enforce",
}


async def list_callable_target_bindings_by_scope(
    repository: Any,
    *,
    scope_type: str,
) -> dict[str, set[str]]:
    if repository is None:
        return {}

    bindings_by_scope: dict[str, set[str]] = {}
    offset = 0
    limit = 1000
    while True:
        bindings, total = await repository.list_bindings(
            scope_type=scope_type,
            limit=limit,
            offset=offset,
        )
        for binding in bindings:
            scope_id = str(binding.scope_id or "").strip()
            callable_key = str(binding.callable_key or "").strip()
            if not scope_id or not callable_key:
                continue
            bindings_by_scope.setdefault(scope_id, set()).add(callable_key)
        offset += len(bindings)
        if not bindings or offset >= total:
            break
    return bindings_by_scope


async def list_callable_target_scope_policies_by_scope_type(
    repository: Any,
    *,
    scope_type: str,
) -> dict[str, str]:
    if repository is None:
        return {}

    policies_by_scope: dict[str, str] = {}
    offset = 0
    limit = 1000
    while True:
        policies, total = await repository.list_policies(
            scope_type=scope_type,
            limit=limit,
            offset=offset,
        )
        for policy in policies:
            scope_id = str(policy.scope_id or "").strip()
            if scope_id:
                policies_by_scope[scope_id] = str(policy.mode or "inherit")
        offset += len(policies)
        if not policies or offset >= total:
            break
    return policies_by_scope


async def build_callable_target_migration_report(
    *,
    db: Any,
    callable_catalog: dict[str, Any],
    binding_repository: Any,
    policy_repository: Any,
    route_group_repository: Any | None = None,
    organization_id: str | None = None,
    rollout_states: set[str] | None = None,
) -> dict[str, Any]:
    del route_group_repository
    org_rows = await _list_organizations(db, organization_id=organization_id)
    team_rows = await _list_teams(db, organization_id=organization_id)
    key_rows = await _list_keys(db, organization_id=organization_id)
    user_rows = await _list_users(db, organization_id=organization_id)

    catalog_keys = sorted(str(key) for key in callable_catalog.keys())
    catalog_key_set = set(catalog_keys)

    org_bindings = await list_callable_target_bindings_by_scope(binding_repository, scope_type="organization")
    team_bindings = await list_callable_target_bindings_by_scope(binding_repository, scope_type="team")
    key_bindings = await list_callable_target_bindings_by_scope(binding_repository, scope_type="api_key")
    user_bindings = await list_callable_target_bindings_by_scope(binding_repository, scope_type="user")
    team_policies = await list_callable_target_scope_policies_by_scope_type(policy_repository, scope_type="team")
    key_policies = await list_callable_target_scope_policies_by_scope_type(policy_repository, scope_type="api_key")

    teams_by_org: dict[str, list[dict[str, Any]]] = {}
    for row in team_rows:
        teams_by_org.setdefault(str(row["organization_id"]), []).append(row)

    keys_by_org: dict[str, list[dict[str, Any]]] = {}
    for row in key_rows:
        keys_by_org.setdefault(str(row["organization_id"]), []).append(row)

    users_by_org: dict[str, list[dict[str, Any]]] = {}
    for row in user_rows:
        users_by_org.setdefault(str(row["organization_id"]), []).append(row)

    organizations: list[dict[str, Any]] = []
    filters = _normalize_rollout_states(rollout_states)

    for org_row in org_rows:
        org_id = str(org_row["organization_id"])
        existing_org_bindings = sorted(org_bindings.get(org_id, set()))
        will_bootstrap_org_bindings = len(existing_org_bindings) == 0 and bool(catalog_keys)

        team_items: list[dict[str, Any]] = []
        for team_row in teams_by_org.get(org_id, []):
            legacy_models = _normalize_models(team_row.get("models"))
            valid_callable_keys = sorted(model for model in legacy_models if model in catalog_key_set)
            missing_callable_keys = sorted(model for model in legacy_models if model not in catalog_key_set)
            existing_binding_keys = sorted(team_bindings.get(str(team_row["team_id"]), set()))
            team_rollout_state = _classify_scope_rollout_state(
                legacy_models=legacy_models,
                valid_callable_keys=valid_callable_keys,
                missing_callable_keys=missing_callable_keys,
                existing_binding_keys=existing_binding_keys,
                scope_policy_mode=team_policies.get(str(team_row["team_id"]), "inherit"),
            )
            team_items.append(
                {
                    "team_id": team_row["team_id"],
                    "team_alias": team_row.get("team_alias"),
                    "legacy_models": legacy_models,
                    "legacy_model_count": len(legacy_models),
                    "valid_callable_keys": valid_callable_keys,
                    "missing_callable_keys": missing_callable_keys,
                    "binding_count": len(existing_binding_keys),
                    "binding_keys": existing_binding_keys,
                    "scope_policy_mode": team_policies.get(str(team_row["team_id"]), "inherit"),
                    "rollout_state": team_rollout_state,
                }
            )

        key_items: list[dict[str, Any]] = []
        for key_row in keys_by_org.get(org_id, []):
            legacy_models = _normalize_models(key_row.get("models"))
            valid_callable_keys = sorted(model for model in legacy_models if model in catalog_key_set)
            missing_callable_keys = sorted(model for model in legacy_models if model not in catalog_key_set)
            existing_binding_keys = sorted(key_bindings.get(str(key_row["token"]), set()))
            key_rollout_state = _classify_scope_rollout_state(
                legacy_models=legacy_models,
                valid_callable_keys=valid_callable_keys,
                missing_callable_keys=missing_callable_keys,
                existing_binding_keys=existing_binding_keys,
                scope_policy_mode=key_policies.get(str(key_row["token"]), "inherit"),
            )
            key_items.append(
                {
                    "token": key_row["token"],
                    "key_name": key_row.get("key_name"),
                    "team_id": key_row.get("team_id"),
                    "legacy_models": legacy_models,
                    "legacy_model_count": len(legacy_models),
                    "valid_callable_keys": valid_callable_keys,
                    "missing_callable_keys": missing_callable_keys,
                    "binding_count": len(existing_binding_keys),
                    "binding_keys": existing_binding_keys,
                    "scope_policy_mode": key_policies.get(str(key_row["token"]), "inherit"),
                    "rollout_state": key_rollout_state,
                }
            )

        user_items: list[dict[str, Any]] = []
        for user_row in users_by_org.get(org_id, []):
            legacy_models = _normalize_models(user_row.get("models"))
            valid_callable_keys = sorted(model for model in legacy_models if model in catalog_key_set)
            missing_callable_keys = sorted(model for model in legacy_models if model not in catalog_key_set)
            existing_binding_keys = sorted(user_bindings.get(str(user_row["user_id"]), set()))
            user_rollout_state = _classify_binding_only_scope_rollout_state(
                legacy_models=legacy_models,
                valid_callable_keys=valid_callable_keys,
                missing_callable_keys=missing_callable_keys,
                existing_binding_keys=existing_binding_keys,
            )
            user_items.append(
                {
                    "user_id": user_row["user_id"],
                    "user_email": user_row.get("user_email"),
                    "team_id": user_row.get("team_id"),
                    "legacy_models": legacy_models,
                    "legacy_model_count": len(legacy_models),
                    "valid_callable_keys": valid_callable_keys,
                    "missing_callable_keys": missing_callable_keys,
                    "binding_count": len(existing_binding_keys),
                    "binding_keys": existing_binding_keys,
                    "rollout_state": user_rollout_state,
                }
            )

        org_rollout_state = _classify_organization_rollout_state(
            will_bootstrap_org_bindings=will_bootstrap_org_bindings,
            team_items=team_items,
            key_items=key_items,
            user_items=user_items,
        )
        if filters and org_rollout_state not in filters:
            continue

        organizations.append(
            {
                "organization_id": org_id,
                "organization_name": org_row.get("organization_name"),
                "org_binding_count": len(existing_org_bindings),
                "org_binding_keys": existing_org_bindings,
                "will_bootstrap_org_bindings": will_bootstrap_org_bindings,
                "bootstrap_callable_keys": catalog_keys if will_bootstrap_org_bindings else [],
                "rollout_state": org_rollout_state,
                "teams": sorted(team_items, key=lambda item: str(item["team_id"])),
                "api_keys": sorted(key_items, key=lambda item: str(item["token"])),
                "users": sorted(user_items, key=lambda item: str(item["user_id"])),
            }
        )

    summary = _build_migration_summary(organizations)
    return {
        "organization_id": organization_id,
        "filters": {"rollout_states": sorted(filters) if filters else []},
        "callable_catalog": {"total": len(catalog_keys), "keys": catalog_keys},
        "summary": summary,
        "organizations": organizations,
    }


async def apply_callable_target_migration_backfill(
    *,
    db: Any,
    callable_catalog: dict[str, Any],
    binding_repository: Any,
    policy_repository: Any,
    route_group_repository: Any | None = None,
    organization_id: str | None = None,
    rollout_states: set[str] | None = None,
) -> dict[str, Any]:
    initial_report = await build_callable_target_migration_report(
        db=db,
        callable_catalog=callable_catalog,
        binding_repository=binding_repository,
        policy_repository=policy_repository,
        route_group_repository=route_group_repository,
        organization_id=organization_id,
        rollout_states=rollout_states,
    )

    applied = {
        "organization_bindings_upserted": 0,
        "team_bindings_upserted": 0,
        "api_key_bindings_upserted": 0,
        "user_bindings_upserted": 0,
        "team_policies_upserted": 0,
        "api_key_policies_upserted": 0,
        "team_legacy_models_cleared": 0,
        "api_key_legacy_models_cleared": 0,
        "user_legacy_models_cleared": 0,
        "route_group_bindings_mirrored": 0,
    }
    processed_user_scope_ids: set[str] = set()
    for organization in initial_report["organizations"]:
        org_id = str(organization["organization_id"])
        if organization["will_bootstrap_org_bindings"]:
            for callable_key in organization["bootstrap_callable_keys"]:
                await binding_repository.upsert_binding(
                    callable_key=callable_key,
                    scope_type="organization",
                    scope_id=org_id,
                    enabled=True,
                    metadata={"source": "legacy_org_bootstrap_backfill"},
                )
                applied["organization_bindings_upserted"] += 1

        for team in organization["teams"]:
            valid_callable_keys = list(team["valid_callable_keys"])
            if not valid_callable_keys:
                if team["legacy_models"] and not team["missing_callable_keys"]:
                    await _clear_team_legacy_models(db, team_id=str(team["team_id"]))
                    applied["team_legacy_models_cleared"] += 1
                continue
            await policy_repository.upsert_policy(
                scope_type="team",
                scope_id=str(team["team_id"]),
                mode="restrict",
                metadata={"source": "legacy_models_backfill"},
            )
            applied["team_policies_upserted"] += 1
            for callable_key in valid_callable_keys:
                await binding_repository.upsert_binding(
                    callable_key=callable_key,
                    scope_type="team",
                    scope_id=str(team["team_id"]),
                    enabled=True,
                    metadata={"source": "legacy_models_backfill"},
                )
                applied["team_bindings_upserted"] += 1
            if team["legacy_models"] and not team["missing_callable_keys"]:
                await _clear_team_legacy_models(db, team_id=str(team["team_id"]))
                applied["team_legacy_models_cleared"] += 1

        for key in organization["api_keys"]:
            valid_callable_keys = list(key["valid_callable_keys"])
            if not valid_callable_keys:
                if key["legacy_models"] and not key["missing_callable_keys"]:
                    await _clear_api_key_legacy_models(db, token=str(key["token"]))
                    applied["api_key_legacy_models_cleared"] += 1
                continue
            await policy_repository.upsert_policy(
                scope_type="api_key",
                scope_id=str(key["token"]),
                mode="restrict",
                metadata={"source": "legacy_models_backfill"},
            )
            applied["api_key_policies_upserted"] += 1
            for callable_key in valid_callable_keys:
                await binding_repository.upsert_binding(
                    callable_key=callable_key,
                    scope_type="api_key",
                    scope_id=str(key["token"]),
                    enabled=True,
                    metadata={"source": "legacy_models_backfill"},
                )
                applied["api_key_bindings_upserted"] += 1
            if key["legacy_models"] and not key["missing_callable_keys"]:
                await _clear_api_key_legacy_models(db, token=str(key["token"]))
                applied["api_key_legacy_models_cleared"] += 1

        for user in organization.get("users", []):
            user_scope_id = str(user["user_id"])
            if user_scope_id in processed_user_scope_ids:
                continue
            processed_user_scope_ids.add(user_scope_id)
            valid_callable_keys = list(user["valid_callable_keys"])
            for callable_key in valid_callable_keys:
                await binding_repository.upsert_binding(
                    callable_key=callable_key,
                    scope_type="user",
                    scope_id=user_scope_id,
                    enabled=True,
                    metadata={"source": "legacy_user_models_backfill"},
                )
                applied["user_bindings_upserted"] += 1
            if user["legacy_models"] and not user["missing_callable_keys"]:
                await _clear_user_legacy_models(db, user_id=user_scope_id)
                applied["user_legacy_models_cleared"] += 1

    if route_group_repository is not None:
        offset = 0
        limit = 500
        while True:
            bindings, total = await route_group_repository.list_bindings(limit=limit, offset=offset)
            for binding in bindings:
                await binding_repository.upsert_binding(
                    callable_key=str(binding.group_key),
                    scope_type=str(binding.scope_type),
                    scope_id=str(binding.scope_id),
                    enabled=bool(binding.enabled),
                    metadata=binding.metadata,
                )
                applied["route_group_bindings_mirrored"] += 1
            offset += len(bindings)
            if not bindings or offset >= total:
                break

    report = await build_callable_target_migration_report(
        db=db,
        callable_catalog=callable_catalog,
        binding_repository=binding_repository,
        policy_repository=policy_repository,
        route_group_repository=route_group_repository,
        organization_id=organization_id,
        rollout_states=rollout_states,
    )

    return {
        **report,
        "applied": applied,
    }


def _normalize_models(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in raw:
        model = str(value or "").strip()
        if not model or model in seen:
            continue
        normalized.append(model)
        seen.add(model)
    return normalized


def _normalize_rollout_states(raw: set[str] | list[str] | tuple[str, ...] | None) -> set[str]:
    if not raw:
        return set()
    normalized = {
        ROLLOUT_STATE_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())
        for value in raw
        if str(value or "").strip()
    }
    return {value for value in normalized if value in ORGANIZATION_ROLLOUT_STATES}


def _build_migration_summary(organizations: list[dict[str, Any]]) -> dict[str, Any]:
    organizations_needing_bootstrap = 0
    teams_with_legacy_models = 0
    api_keys_with_legacy_models = 0
    users_with_legacy_models = 0
    missing_callable_keys_total = 0
    organizations_by_rollout_state: dict[str, int] = {}
    organization_ids_by_rollout_state: dict[str, list[str]] = {}

    for organization in organizations:
        organization_id = str(organization["organization_id"])
        rollout_state = str(organization.get("rollout_state") or "ready_for_enforce")
        organizations_by_rollout_state[rollout_state] = organizations_by_rollout_state.get(rollout_state, 0) + 1
        organization_ids_by_rollout_state.setdefault(rollout_state, []).append(organization_id)

        if bool(organization.get("will_bootstrap_org_bindings")):
            organizations_needing_bootstrap += 1

        for team in organization.get("teams", []):
            legacy_models = team.get("legacy_models") or []
            missing_callable_keys = team.get("missing_callable_keys") or []
            if legacy_models:
                teams_with_legacy_models += 1
                missing_callable_keys_total += len(missing_callable_keys)

        for api_key in organization.get("api_keys", []):
            legacy_models = api_key.get("legacy_models") or []
            missing_callable_keys = api_key.get("missing_callable_keys") or []
            if legacy_models:
                api_keys_with_legacy_models += 1
                missing_callable_keys_total += len(missing_callable_keys)

        for user in organization.get("users", []):
            legacy_models = user.get("legacy_models") or []
            missing_callable_keys = user.get("missing_callable_keys") or []
            if legacy_models:
                users_with_legacy_models += 1
                missing_callable_keys_total += len(missing_callable_keys)

    return {
        "organizations_total": len(organizations),
        "organizations_needing_bootstrap": organizations_needing_bootstrap,
        "teams_with_legacy_models": teams_with_legacy_models,
        "api_keys_with_legacy_models": api_keys_with_legacy_models,
        "users_with_legacy_models": users_with_legacy_models,
        "missing_callable_keys_total": missing_callable_keys_total,
        "organizations_by_rollout_state": organizations_by_rollout_state,
        "organization_ids_by_rollout_state": organization_ids_by_rollout_state,
    }


async def _clear_team_legacy_models(db: Any, *, team_id: str) -> None:
    await db.execute_raw(
        """
        UPDATE deltallm_teamtable
        SET models = ARRAY[]::text[],
            updated_at = NOW()
        WHERE team_id = $1
        """,
        team_id,
    )


async def _clear_api_key_legacy_models(db: Any, *, token: str) -> None:
    await db.execute_raw(
        """
        UPDATE deltallm_verificationtoken
        SET models = ARRAY[]::text[],
            updated_at = NOW()
        WHERE token = $1
        """,
        token,
    )


async def _clear_user_legacy_models(db: Any, *, user_id: str) -> None:
    await db.execute_raw(
        """
        UPDATE deltallm_usertable
        SET models = ARRAY[]::text[],
            updated_at = NOW()
        WHERE user_id = $1
        """,
        user_id,
    )


def _classify_scope_rollout_state(
    *,
    legacy_models: list[str],
    valid_callable_keys: list[str],
    missing_callable_keys: list[str],
    existing_binding_keys: list[str],
    scope_policy_mode: str,
) -> str:
    if not legacy_models:
        return "ready_for_enforce"
    if missing_callable_keys:
        return "missing_catalog_keys"
    if scope_policy_mode != "restrict":
        return "needs_scope_backfill"
    if not set(valid_callable_keys).issubset(set(existing_binding_keys)):
        return "needs_scope_backfill"
    return "ready_for_enforce"


def _classify_binding_only_scope_rollout_state(
    *,
    legacy_models: list[str],
    valid_callable_keys: list[str],
    missing_callable_keys: list[str],
    existing_binding_keys: list[str],
) -> str:
    if not legacy_models:
        return "ready_for_enforce"
    if missing_callable_keys:
        return "missing_catalog_keys"
    if not set(valid_callable_keys).issubset(set(existing_binding_keys)):
        return "needs_scope_backfill"
    return "ready_for_enforce"


def _classify_organization_rollout_state(
    *,
    will_bootstrap_org_bindings: bool,
    team_items: list[dict[str, Any]],
    key_items: list[dict[str, Any]],
    user_items: list[dict[str, Any]],
) -> str:
    child_states = {
        str(item.get("rollout_state") or "")
        for item in [*team_items, *key_items, *user_items]
        if str(item.get("rollout_state") or "")
    }
    if "missing_catalog_keys" in child_states:
        return "missing_catalog_keys"
    if will_bootstrap_org_bindings:
        return "needs_org_bootstrap"
    if "needs_scope_backfill" in child_states:
        return "needs_scope_backfill"
    return "ready_for_enforce"


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
            SELECT team_id, team_alias, organization_id, models
            FROM deltallm_teamtable
            WHERE organization_id = $1
            ORDER BY organization_id ASC, team_id ASC
            """,
            organization_id,
        )
    else:
        rows = await db.query_raw(
            """
            SELECT team_id, team_alias, organization_id, models
            FROM deltallm_teamtable
            ORDER BY organization_id ASC, team_id ASC
            """
        )
    return [dict(row) for row in rows]


async def _list_keys(db: Any, *, organization_id: str | None) -> list[dict[str, Any]]:
    if organization_id:
        rows = await db.query_raw(
            """
            SELECT vt.token, vt.key_name, vt.team_id, t.organization_id, vt.models
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
            SELECT vt.token, vt.key_name, vt.team_id, t.organization_id, vt.models
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
                t.organization_id,
                u.models
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
                t.organization_id,
                u.models
            FROM deltallm_usertable u
            LEFT JOIN deltallm_verificationtoken vt
                ON vt.user_id = u.user_id
            JOIN deltallm_teamtable t
                ON t.team_id = COALESCE(u.team_id, vt.team_id)
            ORDER BY u.user_id ASC, t.organization_id ASC, COALESCE(u.team_id, vt.team_id) ASC
            """
        )
    return [dict(row) for row in rows]
