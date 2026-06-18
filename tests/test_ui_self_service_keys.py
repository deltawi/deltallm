from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from src.api.admin.endpoints.common import get_auth_scope
from src.auth.roles import OrganizationRole, Permission, TeamRole
from src.models.platform_auth import PlatformAuthContext


class _FakeKeyTransaction:
    def __init__(self, db: "_FakeKeyDB") -> None:
        self._db = db

    async def __aenter__(self) -> "_FakeKeyDB":
        self._db.events.append("tx_enter")
        self._db._in_transaction = True
        return self._db

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self._db._in_transaction = False
        self._db.events.append("tx_exit")


class _FakeKeyDB:
    def __init__(
        self,
        keys: dict[str, dict[str, Any]],
        *,
        teams: dict[str, dict[str, Any]] | None = None,
        users: dict[str, dict[str, Any]] | None = None,
        account_ids: set[str] | None = None,
    ) -> None:
        self.keys = dict(keys)
        self.teams = dict(teams or {})
        self.users = dict(users or {})
        self.account_ids = set(account_ids or set())
        self.advisory_locks: list[int] = []
        self.events: list[str] = []
        self.transactional_events: list[str] = []
        self._in_transaction = False

    def tx(self) -> _FakeKeyTransaction:
        return _FakeKeyTransaction(self)

    def _record_event(self, event: str) -> None:
        self.events.append(event)
        if self._in_transaction:
            self.transactional_events.append(event)

    def _param_value(self, params: tuple[Any, ...], placeholder: str) -> Any:
        index = int(placeholder.removeprefix("$")) - 1
        return params[index]

    def _in_values(self, params: tuple[Any, ...], placeholders: str) -> set[str]:
        return {str(self._param_value(params, placeholder) or "") for placeholder in re.findall(r"\$\d+", placeholders)}

    def _list_key_rows(self, normalized_query: str, params: tuple[Any, ...]) -> list[tuple[str, dict[str, Any]]]:
        owner_scoped_orgs: list[tuple[str, set[str]]] = []
        owner_scoped_teams: list[tuple[str, set[str]]] = []

        owner_org_pattern = r"\(t\.organization_id in \(([^)]*)\) and vt\.owner_account_id = \$(\d+)\)"
        owner_team_pattern = r"\(vt\.team_id in \(([^)]*)\) and vt\.owner_account_id = \$(\d+)\)"
        for placeholders, owner_index in re.findall(owner_org_pattern, normalized_query):
            owner_scoped_orgs.append((str(params[int(owner_index) - 1] or ""), self._in_values(params, placeholders)))
        for placeholders, owner_index in re.findall(owner_team_pattern, normalized_query):
            owner_scoped_teams.append((str(params[int(owner_index) - 1] or ""), self._in_values(params, placeholders)))

        full_scope_query = re.sub(owner_org_pattern, "", normalized_query)
        full_scope_query = re.sub(owner_team_pattern, "", full_scope_query)
        full_orgs: set[str] = set()
        full_teams: set[str] = set()
        for placeholders in re.findall(r"t\.organization_id in \(([^)]*)\)", full_scope_query):
            full_orgs.update(self._in_values(params, placeholders))
        for placeholders in re.findall(r"vt\.team_id in \(([^)]*)\)", full_scope_query):
            full_teams.update(self._in_values(params, placeholders))

        global_owner_match = re.search(r"where vt\.owner_account_id = \$(\d+)", normalized_query)
        global_owner_id = str(params[int(global_owner_match.group(1)) - 1] or "") if global_owner_match else None
        exact_team_match = re.search(r"(?:where| and) vt\.team_id = \$(\d+)", normalized_query)
        exact_team_id = str(params[int(exact_team_match.group(1)) - 1] or "") if exact_team_match else None
        search_match = re.search(r"\(vt\.key_name ilike \$(\d+) or vt\.token ilike \$\d+\)", normalized_query)
        search_term = None
        if search_match:
            search_term = str(params[int(search_match.group(1)) - 1] or "").strip("%").lower()

        has_scope_predicate = bool(full_orgs or full_teams or owner_scoped_orgs or owner_scoped_teams)

        def _row_org(row: dict[str, Any]) -> str:
            team = self.teams.get(str(row.get("team_id") or ""), {})
            return str(row.get("organization_id") or team.get("organization_id") or "")

        def _scope_matches(row: dict[str, Any]) -> bool:
            if not has_scope_predicate:
                return True
            team_id = str(row.get("team_id") or "")
            organization_id = _row_org(row)
            owner_account_id = str(row.get("owner_account_id") or "")
            if organization_id in full_orgs or team_id in full_teams:
                return True
            return any(
                owner_account_id == owner_id and organization_id in org_ids
                for owner_id, org_ids in owner_scoped_orgs
            ) or any(
                owner_account_id == owner_id and team_id in team_ids
                for owner_id, team_ids in owner_scoped_teams
            )

        rows: list[tuple[str, dict[str, Any]]] = []
        for token_hash, row in self.keys.items():
            if global_owner_id is not None and str(row.get("owner_account_id") or "") != global_owner_id:
                continue
            if exact_team_id is not None and str(row.get("team_id") or "") != exact_team_id:
                continue
            if search_term and search_term not in str(row.get("key_name") or "").lower() and search_term not in token_hash.lower():
                continue
            if _scope_matches(row):
                rows.append((token_hash, row))
        return rows

    async def query_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        token_hash = str(params[0]) if params else ""

        if "from deltallm_verificationtoken vt" in normalized and "left join deltallm_platformaccount" in normalized:
            rows = self._list_key_rows(normalized, params)
            if normalized.startswith("select count(*) as total"):
                return [{"total": len(rows)}]
            offset = int(params[-1]) if " offset $" in normalized and params else 0
            limit = int(params[-2]) if " limit $" in normalized and len(params) >= 2 else len(rows)
            response_rows: list[dict[str, Any]] = []
            for token_hash, row in rows[offset : offset + limit]:
                team = self.teams.get(str(row.get("team_id") or ""), {})
                response_rows.append(
                    {
                        "token": token_hash,
                        "key_name": row.get("key_name"),
                        "user_id": row.get("user_id"),
                        "team_id": row.get("team_id"),
                        "team_alias": team.get("team_alias"),
                        "owner_account_id": row.get("owner_account_id"),
                        "owner_account_email": None,
                        "owner_service_account_id": row.get("owner_service_account_id"),
                        "owner_service_account_name": None,
                        "spend": row.get("spend", 0),
                        "max_budget": row.get("max_budget"),
                        "rpm_limit": row.get("rpm_limit"),
                        "tpm_limit": row.get("tpm_limit"),
                        "rph_limit": row.get("rph_limit"),
                        "rpd_limit": row.get("rpd_limit"),
                        "tpd_limit": row.get("tpd_limit"),
                        "expires": row.get("expires"),
                        "created_at": row.get("created_at"),
                        "updated_at": row.get("updated_at"),
                    }
                )
            return response_rows

        if "from deltallm_verificationtoken vt" in normalized and "left join deltallm_usertable" in normalized:
            row = self.keys.get(token_hash)
            if row is None:
                return []
            team = self.teams.get(str(row.get("team_id") or ""), {})
            return [
                {
                    "token": token_hash,
                    "user_id": row.get("user_id"),
                    "team_id": row.get("team_id"),
                    "organization_id": row.get("organization_id") or team.get("organization_id"),
                }
            ]

        if normalized.startswith("select owner_account_id from deltallm_verificationtoken"):
            row = self.keys.get(token_hash)
            if row is None:
                return []
            return [{"owner_account_id": row.get("owner_account_id")}]

        if (
            "select vt.token, vt.key_name, vt.team_id, t.team_alias, t.organization_id," in normalized
            and "from deltallm_verificationtoken vt" in normalized
        ):
            row = self.keys.get(token_hash)
            if row is None:
                return []
            team = self.teams.get(str(row.get("team_id") or ""), {})
            return [
                {
                    "token": token_hash,
                    "key_name": row.get("key_name"),
                    "team_id": row.get("team_id"),
                    "team_alias": team.get("team_alias"),
                    "organization_id": row.get("organization_id") or team.get("organization_id"),
                    "owner_account_id": row.get("owner_account_id"),
                    "owner_service_account_id": row.get("owner_service_account_id"),
                    "owner_service_account_name": None,
                }
            ]

        if (
            "from deltallm_teamtable" in normalized
            and "where team_id = $1" in normalized
            and "team_alias" in normalized
        ):
            team_id = token_hash
            row = self.teams.get(team_id)
            return [dict(row)] if row is not None else []

        if normalized.startswith("select self_service_keys_enabled"):
            team_id = token_hash
            row = self.teams.get(team_id)
            return [dict(row)] if row is not None else []

        if normalized.startswith("select rpm_limit, tpm_limit, rph_limit, rpd_limit, tpd_limit from deltallm_teamtable"):
            team_id = token_hash
            row = self.teams.get(team_id)
            return [dict(row)] if row is not None else []

        if normalized.startswith("select user_id from deltallm_usertable where user_id = $1 and team_id = $2"):
            self._record_event("resolve_runtime_user")
            account_id = str(params[0])
            team_id = str(params[1])
            row = self.users.get(account_id)
            if row is not None and str(row.get("team_id") or "") == team_id:
                return [{"user_id": row.get("user_id", account_id)}]
            return []

        if normalized.startswith("select user_id from deltallm_usertable where lower(user_email) = lower($1)"):
            self._record_event("resolve_runtime_user")
            email = str(params[0] or "").strip().lower()
            team_id = str(params[1])
            for fallback_id, candidate in self.users.items():
                candidate_user_id = str(candidate.get("user_id") or fallback_id)
                candidate_email = str(candidate.get("user_email") or "").strip().lower()
                if str(candidate.get("team_id") or "") == team_id and candidate_email == email:
                    return [{"user_id": candidate_user_id}]
            return []

        if normalized.startswith("select count(*) as cnt from deltallm_verificationtoken where team_id = $1 and owner_account_id = $2"):
            self._record_event("count_active_keys")
            team_id = str(params[0])
            owner_account_id = str(params[1])
            count = sum(
                1
                for row in self.keys.values()
                if str(row.get("team_id") or "") == team_id
                and str(row.get("owner_account_id") or "") == owner_account_id
                and _is_active_key(row)
            )
            return [{"cnt": count}]

        if normalized.startswith("select account_id from deltallm_platformaccount"):
            account_id = token_hash
            return [{"account_id": account_id}] if account_id in self.account_ids else []

        if normalized.startswith("select token from deltallm_verificationtoken where token = $1"):
            return [{"token": token_hash}] if token_hash in self.keys else []

        return []

    async def execute_raw(self, query: str, *params):  # noqa: ANN201
        normalized = " ".join(query.lower().split())
        if normalized.startswith("select pg_advisory_xact_lock"):
            self._record_event("advisory_lock")
            self.advisory_locks.append(int(params[0]))
            return 1
        if normalized.startswith("delete from deltallm_verificationtoken where token = $1"):
            token_hash = str(params[0])
            return 1 if self.keys.pop(token_hash, None) is not None else 0
        if normalized.startswith("insert into deltallm_verificationtoken"):
            self._record_event("insert_key")
            (
                token_hash,
                key_name,
                user_id,
                team_id,
                owner_account_id,
                owner_service_account_id,
                max_budget,
                rpm_limit,
                tpm_limit,
                rph_limit,
                rpd_limit,
                tpd_limit,
                expires,
            ) = params
            team = self.teams.get(str(team_id), {})
            self.keys[str(token_hash)] = {
                "token": token_hash,
                "key_name": key_name,
                "user_id": user_id,
                "team_id": team_id,
                "organization_id": team.get("organization_id"),
                "owner_account_id": owner_account_id,
                "owner_service_account_id": owner_service_account_id,
                "max_budget": max_budget,
                "rpm_limit": rpm_limit,
                "tpm_limit": tpm_limit,
                "rph_limit": rph_limit,
                "rpd_limit": rpd_limit,
                "tpd_limit": tpd_limit,
                "expires": expires,
            }
            return 1
        if normalized.startswith("update deltallm_verificationtoken set token = $1, updated_at = now() where token = $2"):
            new_hash = str(params[0])
            old_hash = str(params[1])
            row = self.keys.pop(old_hash, None)
            if row is None:
                return 0
            row["token"] = new_hash
            self.keys[new_hash] = row
            return 1
        return 0


class _FakeKeyDBWithoutTransactions(_FakeKeyDB):
    tx = None


def _set_auth_context(monkeypatch: pytest.MonkeyPatch, context: PlatformAuthContext | None) -> None:
    monkeypatch.setattr("src.middleware.platform_auth.get_platform_auth_context", lambda request: context)
    monkeypatch.setattr("src.middleware.admin.get_platform_auth_context", lambda request: context)
    monkeypatch.setattr("src.api.admin.endpoints.keys.get_platform_auth_context", lambda request: context)


def _is_active_key(row: dict[str, Any]) -> bool:
    expires = row.get("expires")
    if expires is None:
        return True
    if isinstance(expires, datetime):
        exp_dt = expires if expires.tzinfo else expires.replace(tzinfo=UTC)
    elif isinstance(expires, str):
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        except ValueError:
            return True
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=UTC)
    else:
        return True
    return exp_dt.astimezone(UTC) > datetime.now(tz=UTC)


def _make_context(
    *,
    account_id: str,
    org_memberships: list[dict[str, Any]] | None = None,
    team_memberships: list[dict[str, Any]] | None = None,
) -> PlatformAuthContext:
    return PlatformAuthContext(
        account_id=account_id,
        email=f"{account_id}@example.com",
        role="platform_user",
        organization_memberships=org_memberships or [],
        team_memberships=team_memberships or [],
    )


def _fake_request() -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                app_config=None,
                settings=SimpleNamespace(master_key=None),
            )
        )
    )


def _install_key_db(
    test_app: Any,
    keys: dict[str, dict[str, Any]],
    *,
    teams: dict[str, dict[str, Any]] | None = None,
    users: dict[str, dict[str, Any]] | None = None,
    account_ids: set[str] | None = None,
    db_cls: type[_FakeKeyDB] = _FakeKeyDB,
) -> _FakeKeyDB:
    fake_db = db_cls(keys, teams=teams, users=users, account_ids=account_ids)
    test_app.state.prisma_manager = type("Prisma", (), {"client": fake_db})()
    return fake_db


def _self_service_team(**overrides: Any) -> dict[str, Any]:
    team = {
        "team_id": "team-sandbox",
        "team_alias": "Sandbox",
        "organization_id": "org-sandbox",
        "self_service_keys_enabled": True,
        "self_service_max_keys_per_user": 2,
        "self_service_budget_ceiling": 5.0,
        "self_service_require_expiry": True,
        "self_service_max_expiry_days": 14,
        "rpm_limit": 10,
        "tpm_limit": 1000,
        "rph_limit": 100,
        "rpd_limit": 500,
        "tpd_limit": 10000,
    }
    team.update(overrides)
    return team


def _list_tokens(response_body: dict[str, Any]) -> set[str]:
    return {str(row.get("token") or "") for row in response_body.get("data", [])}


def _install_key_asset_read_stubs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def fake_visibility(request: Any, **kwargs: Any) -> dict[str, Any]:
        del request
        call = {"kind": "visibility", **kwargs}
        calls.append(call)
        return call

    async def fake_access(request: Any, **kwargs: Any) -> dict[str, Any]:
        del request
        call = {"kind": "access", **kwargs}
        calls.append(call)
        return call

    monkeypatch.setattr("src.api.admin.endpoints.keys.build_asset_visibility_preview", fake_visibility)
    monkeypatch.setattr("src.api.admin.endpoints.keys.build_scope_asset_access", fake_access)
    return calls


def test_get_auth_scope_tracks_effective_permissions_beyond_endpoint_permissions(monkeypatch: pytest.MonkeyPatch):
    context = _make_context(
        account_id="acct-admin",
        org_memberships=[{"organization_id": "org-1", "role": OrganizationRole.ADMIN}],
    )
    _set_auth_context(monkeypatch, context)

    scope = get_auth_scope(
        _fake_request(),
        any_permission=[Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF],
    )

    assert scope.granted_permissions == {Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF}
    assert Permission.KEY_UPDATE not in scope.granted_permissions
    assert Permission.KEY_UPDATE in scope.effective_permissions
    assert Permission.KEY_REVOKE in scope.org_permissions_by_id["org-1"]
    assert Permission.KEY_CREATE_SELF in scope.org_permissions_by_id["org-1"]


def test_get_auth_scope_keeps_permissions_separated_by_team(monkeypatch: pytest.MonkeyPatch):
    context = _make_context(
        account_id="acct-mixed",
        team_memberships=[
            {"team_id": "team-admin", "role": TeamRole.ADMIN},
            {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
        ],
    )
    _set_auth_context(monkeypatch, context)

    scope = get_auth_scope(
        _fake_request(),
        any_permission=[Permission.KEY_REVOKE, Permission.KEY_CREATE_SELF],
    )

    assert Permission.KEY_REVOKE in scope.team_permissions_by_id["team-admin"]
    assert Permission.KEY_CREATE_SELF in scope.team_permissions_by_id["team-admin"]
    assert Permission.KEY_REVOKE not in scope.team_permissions_by_id["team-dev"]
    assert Permission.KEY_CREATE_SELF in scope.team_permissions_by_id["team-dev"]


@pytest.mark.asyncio
async def test_org_admin_can_revoke_non_owned_key_within_org_scope(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-org-1": {
                "owner_account_id": "acct-other",
                "team_id": "team-1",
                "organization_id": "org-1",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-admin",
            org_memberships=[{"organization_id": "org-1", "role": OrganizationRole.ADMIN}],
        ),
    )

    response = await client.post("/ui/api/keys/key-org-1/revoke")

    assert response.status_code == 200
    assert response.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_self_service_user_cannot_revoke_owned_key_outside_team_scope(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-cross-scope": {
                "owner_account_id": "acct-dev",
                "team_id": "team-2",
                "organization_id": "org-2",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-1", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post("/ui/api/keys/key-cross-scope/revoke")

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"


@pytest.mark.asyncio
async def test_self_service_user_can_revoke_owned_key_within_team_scope(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-team-1": {
                "owner_account_id": "acct-dev",
                "team_id": "team-1",
                "organization_id": "org-1",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-1", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post("/ui/api/keys/key-team-1/revoke")

    assert response.status_code == 200
    assert response.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_mixed_role_user_cannot_revoke_non_owned_key_in_self_service_team(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-team-dev-other": {
                "owner_account_id": "acct-other",
                "team_id": "team-dev",
                "organization_id": "org-dev",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    response = await client.post("/ui/api/keys/key-team-dev-other/revoke")

    assert response.status_code == 403
    assert response.json()["detail"] == "You can only manage your own keys"


@pytest.mark.asyncio
async def test_mixed_role_user_can_revoke_non_owned_key_in_admin_team(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-team-admin-other": {
                "owner_account_id": "acct-other",
                "team_id": "team-admin",
                "organization_id": "org-admin",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    response = await client.post("/ui/api/keys/key-team-admin-other/revoke")

    assert response.status_code == 200
    assert response.json() == {"revoked": True}


@pytest.mark.asyncio
async def test_mixed_role_user_lists_all_admin_team_keys_and_only_owned_developer_team_keys(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_key_db(
        test_app,
        {
            "admin-owned": {
                "key_name": "admin-owned",
                "owner_account_id": "acct-mixed",
                "team_id": "team-admin",
            },
            "admin-other": {
                "key_name": "admin-other",
                "owner_account_id": "acct-other",
                "team_id": "team-admin",
            },
            "dev-owned": {
                "key_name": "dev-owned",
                "owner_account_id": "acct-mixed",
                "team_id": "team-dev",
            },
            "dev-other": {
                "key_name": "dev-other",
                "owner_account_id": "acct-other",
                "team_id": "team-dev",
            },
            "outside-owned": {
                "key_name": "outside-owned",
                "owner_account_id": "acct-mixed",
                "team_id": "team-outside",
            },
        },
        teams={
            "team-admin": {"team_id": "team-admin", "team_alias": "Admin Team", "organization_id": "org-admin"},
            "team-dev": {"team_id": "team-dev", "team_alias": "Developer Team", "organization_id": "org-dev"},
            "team-outside": {"team_id": "team-outside", "team_alias": "Outside Team", "organization_id": "org-outside"},
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    response = await client.get("/ui/api/keys")

    assert response.status_code == 200
    assert _list_tokens(response.json()) == {"admin-owned", "admin-other", "dev-owned"}


@pytest.mark.asyncio
async def test_mixed_role_my_keys_lists_only_owned_keys_in_authorized_scopes(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "admin-owned": {
                "key_name": "admin-owned",
                "owner_account_id": "acct-mixed",
                "team_id": "team-admin",
            },
            "admin-other": {
                "key_name": "admin-other",
                "owner_account_id": "acct-other",
                "team_id": "team-admin",
            },
            "dev-owned": {
                "key_name": "dev-owned",
                "owner_account_id": "acct-mixed",
                "team_id": "team-dev",
            },
            "outside-owned": {
                "key_name": "outside-owned",
                "owner_account_id": "acct-mixed",
                "team_id": "team-outside",
            },
        },
        teams={
            "team-admin": {"team_id": "team-admin", "team_alias": "Admin Team", "organization_id": "org-admin"},
            "team-dev": {"team_id": "team-dev", "team_alias": "Developer Team", "organization_id": "org-dev"},
            "team-outside": {"team_id": "team-outside", "team_alias": "Outside Team", "organization_id": "org-outside"},
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    response = await client.get("/ui/api/keys?my_keys=true")

    assert response.status_code == 200
    assert _list_tokens(response.json()) == {"admin-owned", "dev-owned"}


@pytest.mark.asyncio
async def test_self_service_developer_lists_only_owned_team_keys(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "dev-owned": {
                "key_name": "dev-owned",
                "owner_account_id": "acct-dev",
                "team_id": "team-dev",
            },
            "dev-other": {
                "key_name": "dev-other",
                "owner_account_id": "acct-other",
                "team_id": "team-dev",
            },
            "outside-owned": {
                "key_name": "outside-owned",
                "owner_account_id": "acct-dev",
                "team_id": "team-outside",
            },
        },
        teams={
            "team-dev": {"team_id": "team-dev", "team_alias": "Developer Team", "organization_id": "org-dev"},
            "team-outside": {"team_id": "team-outside", "team_alias": "Outside Team", "organization_id": "org-outside"},
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-dev", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.get("/ui/api/keys")

    assert response.status_code == 200
    assert _list_tokens(response.json()) == {"dev-owned"}


@pytest.mark.asyncio
@pytest.mark.parametrize("org_role", [OrganizationRole.BILLING, OrganizationRole.AUDITOR])
async def test_read_only_org_role_lists_scoped_keys(client, test_app, monkeypatch: pytest.MonkeyPatch, org_role: str):
    _install_key_db(
        test_app,
        {
            "org-key-owned": {
                "key_name": "org-key-owned",
                "owner_account_id": "acct-billing",
                "team_id": "team-1",
            },
            "org-key-other": {
                "key_name": "org-key-other",
                "owner_account_id": "acct-other",
                "team_id": "team-2",
            },
            "outside-key": {
                "key_name": "outside-key",
                "owner_account_id": "acct-other",
                "team_id": "team-outside",
            },
        },
        teams={
            "team-1": {"team_id": "team-1", "team_alias": "Team One", "organization_id": "org-1"},
            "team-2": {"team_id": "team-2", "team_alias": "Team Two", "organization_id": "org-1"},
            "team-outside": {"team_id": "team-outside", "team_alias": "Outside Team", "organization_id": "org-2"},
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-billing",
            org_memberships=[{"organization_id": "org-1", "role": org_role}],
        ),
    )

    response = await client.get("/ui/api/keys")

    assert response.status_code == 200
    assert _list_tokens(response.json()) == {"org-key-owned", "org-key-other"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/ui/api/keys/dev-other/asset-visibility",
        "/ui/api/keys/dev-other/asset-access",
    ],
)
async def test_self_service_developer_cannot_read_non_owned_key_asset_metadata(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
):
    calls = _install_key_asset_read_stubs(monkeypatch)
    _install_key_db(
        test_app,
        {
            "dev-other": {
                "owner_account_id": "acct-other",
                "team_id": "team-dev",
                "organization_id": "org-dev",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-dev", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.get(path)

    assert response.status_code == 403
    assert response.json()["detail"] == "You can only manage your own keys"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("/ui/api/keys/dev-owned/asset-visibility", "visibility"),
        ("/ui/api/keys/dev-owned/asset-access", "access"),
    ],
)
async def test_self_service_developer_can_read_owned_key_asset_metadata(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    kind: str,
):
    _install_key_asset_read_stubs(monkeypatch)
    _install_key_db(
        test_app,
        {
            "dev-owned": {
                "owner_account_id": "acct-dev",
                "user_id": "acct-dev",
                "team_id": "team-dev",
                "organization_id": "org-dev",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-dev", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == kind
    assert body["organization_id"] == "org-dev"
    assert body["team_id"] == "team-dev"
    assert body["api_key_id"] == "dev-owned"


@pytest.mark.asyncio
@pytest.mark.parametrize("org_role", [OrganizationRole.BILLING, OrganizationRole.AUDITOR])
@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("/ui/api/keys/org-key-other/asset-visibility", "visibility"),
        ("/ui/api/keys/org-key-other/asset-access", "access"),
    ],
)
async def test_read_only_org_role_can_read_scoped_key_asset_metadata(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
    org_role: str,
    path: str,
    kind: str,
):
    _install_key_asset_read_stubs(monkeypatch)
    _install_key_db(
        test_app,
        {
            "org-key-other": {
                "owner_account_id": "acct-other",
                "team_id": "team-1",
                "organization_id": "org-1",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-billing",
            org_memberships=[{"organization_id": "org-1", "role": org_role}],
        ),
    )

    response = await client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == kind
    assert body["organization_id"] == "org-1"
    assert body["team_id"] == "team-1"
    assert body["api_key_id"] == "org-key-other"


@pytest.mark.asyncio
async def test_mixed_role_user_key_asset_read_is_full_in_admin_team_and_owner_only_in_developer_team(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
):
    _install_key_asset_read_stubs(monkeypatch)
    _install_key_db(
        test_app,
        {
            "admin-other": {
                "owner_account_id": "acct-other",
                "team_id": "team-admin",
                "organization_id": "org-admin",
            },
            "dev-other": {
                "owner_account_id": "acct-other",
                "team_id": "team-dev",
                "organization_id": "org-dev",
            },
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    admin_response = await client.get("/ui/api/keys/admin-other/asset-visibility")
    developer_response = await client.get("/ui/api/keys/dev-other/asset-visibility")

    assert admin_response.status_code == 200
    assert admin_response.json()["api_key_id"] == "admin-other"
    assert developer_response.status_code == 403
    assert developer_response.json()["detail"] == "You can only manage your own keys"


@pytest.mark.asyncio
async def test_mixed_role_user_can_create_self_service_key_in_developer_team(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {},
        teams={
            "team-dev": {
                "team_id": "team-dev",
                "team_alias": "Developer Team",
                "organization_id": "org-dev",
                "self_service_keys_enabled": True,
                "self_service_max_keys_per_user": None,
                "self_service_budget_ceiling": None,
                "self_service_require_expiry": False,
                "self_service_max_expiry_days": None,
                "rpm_limit": None,
                "tpm_limit": None,
                "rph_limit": None,
                "rpd_limit": None,
                "tpd_limit": None,
            },
        },
        users={
            "acct-mixed": {
                "user_id": "acct-mixed",
                "user_email": "acct-mixed@example.com",
                "team_id": "team-dev",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-mixed",
            team_memberships=[
                {"team_id": "team-admin", "role": TeamRole.ADMIN},
                {"team_id": "team-dev", "role": TeamRole.DEVELOPER},
            ],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "dev-self-service",
            "team_id": "team-dev",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["team_id"] == "team-dev"
    assert body["owner_account_id"] == "acct-mixed"
    assert body["owner_service_account_id"] is None
    assert body["self_service"] is True
    assert body["user_id"] == "acct-mixed"


@pytest.mark.asyncio
async def test_self_registered_user_self_service_key_binds_runtime_user(client, test_app, monkeypatch: pytest.MonkeyPatch):
    expires = (datetime.now(tz=UTC) + timedelta(days=7)).isoformat()
    db = _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        users={"acct-dev": {"user_id": "acct-dev", "team_id": "team-sandbox"}},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "sandbox-key",
            "team_id": "team-sandbox",
            "user_id": "attacker-user",
            "owner_service_account_id": "svc-1",
            "max_budget": "5",
            "rpm_limit": "3",
            "tpm_limit": 500,
            "rph_limit": 30.0,
            "rpd_limit": 100,
            "tpd_limit": 1000,
            "expires": expires,
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["user_id"] == "acct-dev"
    assert body["owner_account_id"] == "acct-dev"
    assert body["owner_service_account_id"] is None
    assert body["max_budget"] == 5.0
    assert body["rpm_limit"] == 3
    assert db.keys[body["token"]]["user_id"] == "acct-dev"
    assert db.events.index("advisory_lock") < db.events.index("count_active_keys")
    assert db.events.index("count_active_keys") < db.events.index("insert_key")
    assert {"advisory_lock", "count_active_keys", "resolve_runtime_user", "insert_key"}.issubset(
        db.transactional_events
    )
    assert len(db.advisory_locks) == 1


@pytest.mark.asyncio
async def test_self_service_key_creation_resolves_runtime_user_by_email(client, test_app, monkeypatch: pytest.MonkeyPatch):
    expires = (datetime.now(tz=UTC) + timedelta(days=7)).isoformat()
    db = _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        users={
            "runtime-user-1": {
                "user_id": "runtime-user-1",
                "user_email": "ACCT-DEV@example.com",
                "team_id": "team-sandbox",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "sandbox-key",
            "team_id": "team-sandbox",
            "max_budget": 1,
            "expires": expires,
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["user_id"] == "runtime-user-1"
    assert db.keys[body["token"]]["user_id"] == "runtime-user-1"


@pytest.mark.asyncio
async def test_self_service_key_creation_stores_normalized_utc_expiry(client, test_app, monkeypatch: pytest.MonkeyPatch):
    utc_expiry = datetime.now(tz=UTC).replace(microsecond=0) + timedelta(days=7)
    offset_expiry = utc_expiry.astimezone(timezone(timedelta(hours=3)))
    expected_expiry = offset_expiry.astimezone(UTC).isoformat()
    db = _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        users={"acct-dev": {"user_id": "acct-dev", "user_email": "acct-dev@example.com", "team_id": "team-sandbox"}},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "normalized-expiry-key",
            "team_id": "team-sandbox",
            "max_budget": 1,
            "expires": offset_expiry.isoformat(),
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["expires"] == expected_expiry
    assert db.keys[body["token"]]["expires"] == expected_expiry


@pytest.mark.asyncio
async def test_self_service_key_creation_requires_runtime_user(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "sandbox-key",
            "team_id": "team-sandbox",
            "max_budget": 1,
            "expires": (datetime.now(tz=UTC) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Self-service key creation requires a runtime user in this team"


@pytest.mark.asyncio
async def test_self_service_key_creation_requires_transaction_support(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        users={"acct-dev": {"user_id": "acct-dev", "user_email": "acct-dev@example.com", "team_id": "team-sandbox"}},
        db_cls=_FakeKeyDBWithoutTransactions,
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "sandbox-key",
            "team_id": "team-sandbox",
            "max_budget": 1,
            "expires": (datetime.now(tz=UTC) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Database transactions are required for self-service key creation"


@pytest.mark.asyncio
async def test_self_service_key_creation_enforces_max_active_keys(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "existing-key": {
                "owner_account_id": "acct-dev",
                "team_id": "team-sandbox",
                "organization_id": "org-sandbox",
                "expires": datetime.now(tz=UTC) + timedelta(days=1),
            }
        },
        teams={"team-sandbox": _self_service_team(self_service_max_keys_per_user=1)},
        users={"acct-dev": {"user_id": "acct-dev", "team_id": "team-sandbox"}},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "second-key",
            "team_id": "team-sandbox",
            "max_budget": 1,
            "expires": (datetime.now(tz=UTC) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 403
    assert "maximum of 1 self-service keys" in response.json()["detail"]


@pytest.mark.asyncio
async def test_self_service_key_creation_enforces_budget_ceiling(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team(self_service_budget_ceiling=5.0)},
        users={"acct-dev": {"user_id": "acct-dev", "team_id": "team-sandbox"}},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "expensive-key",
            "team_id": "team-sandbox",
            "max_budget": 6,
            "expires": (datetime.now(tz=UTC) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Budget must be between $0 and $5.0"


@pytest.mark.asyncio
async def test_self_service_key_creation_requires_budget_when_ceiling_configured(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team(self_service_budget_ceiling=5.0)},
        users={"acct-dev": {"user_id": "acct-dev", "team_id": "team-sandbox"}},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "missing-budget",
            "team_id": "team-sandbox",
            "expires": (datetime.now(tz=UTC) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 400
    assert "A budget (max_budget) is required" in response.json()["detail"]


@pytest.mark.asyncio
async def test_self_service_key_creation_rejects_negative_budget_without_ceiling(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team(self_service_budget_ceiling=None)},
        users={"acct-dev": {"user_id": "acct-dev", "team_id": "team-sandbox"}},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "negative-budget",
            "team_id": "team-sandbox",
            "max_budget": -1,
            "expires": (datetime.now(tz=UTC) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "max_budget must be greater than or equal to 0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("expires", "expected_detail"),
    [
        (None, "An expiry date is required for self-service keys on this team"),
        ("not-a-date", "expires must be a valid ISO 8601 datetime string"),
        (
            lambda: (datetime.now(tz=UTC) - timedelta(minutes=1)).isoformat(),
            "Expiry date must be in the future",
        ),
        (
            lambda: (datetime.now(tz=UTC) + timedelta(days=15)).isoformat(),
            "Expiry date cannot be more than 14 days from now",
        ),
    ],
)
async def test_self_service_key_creation_enforces_expiry_policy(
    client,
    test_app,
    monkeypatch: pytest.MonkeyPatch,
    expires: str | None,
    expected_detail: str,
):
    _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team()},
        users={"acct-dev": {"user_id": "acct-dev", "team_id": "team-sandbox"}},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )
    expires_value = expires() if callable(expires) else expires
    payload = {
        "key_name": "expiry-policy-key",
        "team_id": "team-sandbox",
        "max_budget": 1,
    }
    if expires_value is not None:
        payload["expires"] = expires_value

    response = await client.post("/ui/api/keys", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail


@pytest.mark.asyncio
async def test_self_service_key_creation_rejects_limits_above_team_limits(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {},
        teams={"team-sandbox": _self_service_team(rpm_limit=10)},
        users={"acct-dev": {"user_id": "acct-dev", "team_id": "team-sandbox"}},
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.post(
        "/ui/api/keys",
        json={
            "key_name": "limit-policy-key",
            "team_id": "team-sandbox",
            "max_budget": 1,
            "rpm_limit": 11,
            "expires": (datetime.now(tz=UTC) + timedelta(days=1)).isoformat(),
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "rpm_limit (11) cannot exceed team limit (10)"


@pytest.mark.asyncio
async def test_self_service_user_cannot_update_key_asset_access(client, test_app, monkeypatch: pytest.MonkeyPatch):
    _install_key_db(
        test_app,
        {
            "key-team-1": {
                "owner_account_id": "acct-dev",
                "user_id": "acct-dev",
                "team_id": "team-sandbox",
                "organization_id": "org-sandbox",
            }
        },
    )
    _set_auth_context(
        monkeypatch,
        _make_context(
            account_id="acct-dev",
            team_memberships=[{"team_id": "team-sandbox", "role": TeamRole.DEVELOPER}],
        ),
    )

    response = await client.put(
        "/ui/api/keys/key-team-1/asset-access",
        json={"mode": "restrict", "selected_callable_keys": ["prod-model"]},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permissions"
