from pathlib import Path

from src.api.admin.endpoints.common import AuthScope
from src.auth.roles import Permission
from src.billing.spend_read import get_spend_read_source
from src.services.spend_visibility import apply_spend_visibility, resolve_spend_visibility


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_self_visibility_uses_only_the_authenticated_account() -> None:
    scope = AuthScope(
        account_id="acct-1",
        org_permissions_by_id={"org-1": {Permission.SPEND_READ_SELF}},
        effective_permissions={Permission.SPEND_READ_SELF},
    )

    visibility = resolve_spend_visibility(scope, scoped_views_enabled=True)
    clauses: list[str] = []
    params: list[object] = []
    apply_spend_visibility(
        clauses=clauses,
        params=params,
        visibility=visibility,
        source=get_spend_read_source(),
        table_alias="s",
    )

    assert visibility.is_self_only is True
    assert visibility.allowed_dimensions == ("organization", "team")
    assert visibility.allowed_groupings == (
        "day",
        "model",
        "provider",
        "organization",
        "team",
    )
    assert clauses == ["(s.owner_account_id = $1 AND (s.organization_id IN ($2)))"]
    assert params == ["acct-1", "org-1"]


def test_team_visibility_takes_precedence_over_self_visibility() -> None:
    scope = AuthScope(
        account_id="acct-1",
        team_permissions_by_id={
            "team-1": {Permission.SPEND_READ_TEAM, Permission.SPEND_READ_SELF},
        },
        effective_permissions={Permission.SPEND_READ_TEAM, Permission.SPEND_READ_SELF},
    )

    visibility = resolve_spend_visibility(scope, scoped_views_enabled=True)

    assert visibility.level == "team"
    assert visibility.team_ids == ("team-1",)
    assert visibility.owner_account_id == "acct-1"
    assert visibility.available_views == ("team", "self")
    assert visibility.allowed_dimensions == ("team", "user")
    assert visibility.can_view_request_logs is False


def test_visibility_cache_payload_isolated_by_account() -> None:
    first = resolve_spend_visibility(AuthScope(
        account_id="acct-1",
        org_permissions_by_id={"org-1": {Permission.SPEND_READ_SELF}},
        effective_permissions={Permission.SPEND_READ_SELF},
    ), scoped_views_enabled=True)
    second = resolve_spend_visibility(AuthScope(
        account_id="acct-2",
        org_permissions_by_id={"org-1": {Permission.SPEND_READ_SELF}},
        effective_permissions={Permission.SPEND_READ_SELF},
    ), scoped_views_enabled=True)

    assert first.cache_payload() != second.cache_payload()
    assert first.cache_payload()["version"] == 5


def test_elevated_visibility_cache_payload_uses_only_the_active_scope() -> None:
    first_scope = AuthScope(
        account_id="acct-1",
        org_permissions_by_id={
            "org-1": {Permission.SPEND_READ, Permission.SPEND_READ_SELF},
        },
        team_permissions_by_id={
            "team-a": {Permission.SPEND_READ_TEAM, Permission.SPEND_READ_SELF},
        },
        effective_permissions={
            Permission.SPEND_READ,
            Permission.SPEND_READ_TEAM,
            Permission.SPEND_READ_SELF,
        },
    )
    second_scope = AuthScope(
        account_id="acct-2",
        org_permissions_by_id={
            "org-1": {Permission.SPEND_READ, Permission.SPEND_READ_SELF},
        },
        team_permissions_by_id={
            "team-b": {Permission.SPEND_READ_TEAM, Permission.SPEND_READ_SELF},
        },
        effective_permissions={
            Permission.SPEND_READ,
            Permission.SPEND_READ_TEAM,
            Permission.SPEND_READ_SELF,
        },
    )

    first = resolve_spend_visibility(
        first_scope, "organization", scoped_views_enabled=True
    )
    second = resolve_spend_visibility(
        second_scope, "organization", scoped_views_enabled=True
    )

    assert first.cache_payload() == second.cache_payload() == {
        "version": 5,
        "active_view": "organization",
        "organization_ids": ["org-1"],
    }
    assert resolve_spend_visibility(
        first_scope, "team", scoped_views_enabled=True
    ).cache_payload() != (
        resolve_spend_visibility(
            second_scope, "team", scoped_views_enabled=True
        ).cache_payload()
    )


def test_mixed_role_can_select_each_non_overlapping_view() -> None:
    scope = AuthScope(
        account_id="acct-1",
        org_permissions_by_id={
            "org-1": {Permission.SPEND_READ, Permission.SPEND_READ_SELF},
        },
        team_permissions_by_id={
            "team-1": {Permission.SPEND_READ_TEAM, Permission.SPEND_READ_SELF},
        },
        effective_permissions={
            Permission.SPEND_READ,
            Permission.SPEND_READ_TEAM,
            Permission.SPEND_READ_SELF,
        },
    )

    default = resolve_spend_visibility(scope, scoped_views_enabled=True)
    team = resolve_spend_visibility(scope, "team", scoped_views_enabled=True)
    self_view = resolve_spend_visibility(scope, "self", scoped_views_enabled=True)

    assert default.view == "organization"
    assert default.available_views == ("organization", "team", "self")
    assert team.view == "team"
    assert self_view.view == "self"


def test_self_visibility_without_an_active_membership_fails_closed() -> None:
    visibility = resolve_spend_visibility(AuthScope(
        account_id="acct-1",
        effective_permissions={Permission.SPEND_READ_SELF},
    ))
    clauses: list[str] = []
    params: list[object] = []

    apply_spend_visibility(
        clauses=clauses,
        params=params,
        visibility=visibility,
        source=get_spend_read_source(),
    )

    assert clauses == ["1 = 0"]
    assert params == []


def test_multi_org_self_visibility_keeps_all_memberships_behind_owner_filter() -> None:
    visibility = resolve_spend_visibility(AuthScope(
        account_id="acct-1",
        org_permissions_by_id={
            "org-a": {Permission.SPEND_READ_SELF},
            "org-b": {Permission.SPEND_READ_SELF},
        },
        team_permissions_by_id={
            "team-a": {Permission.SPEND_READ_SELF},
            "team-b": {Permission.SPEND_READ_SELF},
        },
        effective_permissions={Permission.SPEND_READ_SELF},
    ), scoped_views_enabled=True)
    clauses: list[str] = []
    params: list[object] = []

    apply_spend_visibility(
        clauses=clauses,
        params=params,
        visibility=visibility,
        source=get_spend_read_source(),
        table_alias="s",
    )

    assert clauses == [
        "(s.owner_account_id = $1 AND "
        "(s.organization_id IN ($2, $3) OR s.team_id IN ($4, $5)))"
    ]
    assert params == ["acct-1", "org-a", "org-b", "team-a", "team-b"]


def test_scoped_views_stay_hidden_until_the_cluster_gate_is_enabled() -> None:
    scope = AuthScope(
        account_id="acct-1",
        org_permissions_by_id={"org-1": {Permission.SPEND_READ_SELF}},
        team_permissions_by_id={
            "team-1": {Permission.SPEND_READ_TEAM, Permission.SPEND_READ_SELF},
        },
        effective_permissions={Permission.SPEND_READ_TEAM, Permission.SPEND_READ_SELF},
    )

    disabled = resolve_spend_visibility(scope, scoped_views_enabled=False)
    enabled = resolve_spend_visibility(scope, scoped_views_enabled=True)

    assert disabled.available_views == ()
    assert enabled.available_views == ("team", "self")


def test_spend_scope_migrations_are_online_and_do_not_guess_historical_owners() -> None:
    cursor_sql = (
        _REPOSITORY_ROOT
        / "prisma/migrations/20260810120000_spend_log_cursor_indexes/migration.sql"
    ).read_text()
    owner_sql = (
        _REPOSITORY_ROOT
        / "prisma/migrations/20260810140000_spend_owner_scope/migration.sql"
    ).read_text()
    owner_index_sql = (
        _REPOSITORY_ROOT
        / "prisma/migrations/20260810150000_spend_owner_scope_index/migration.sql"
    ).read_text()

    assert "CONCURRENTLY" not in cursor_sql
    assert "CONCURRENTLY" not in owner_index_sql
    assert cursor_sql.count("CREATE INDEX IF NOT EXISTS") == 2
    assert "CREATE INDEX IF NOT EXISTS" in owner_index_sql
    assert "NOT index_meta.indisvalid OR NOT index_meta.indisready" in cursor_sql
    assert "NOT index_meta.indisvalid OR NOT index_meta.indisready" in owner_index_sql
    assert "SET LOCAL lock_timeout = '5s'" in cursor_sql
    assert "SET LOCAL lock_timeout = '5s'" in owner_sql
    assert "SET LOCAL lock_timeout = '5s'" in owner_index_sql
    assert "UPDATE deltallm_spendlog_events" not in owner_sql
    assert "deltallm_snapshot_spend_owner_account_id" in owner_sql
    assert "deltallm_snapshot_batch_session_owner_account_id" in owner_sql
    assert "deltallm_snapshot_batch_job_owner_account_id" in owner_sql
    assert "deltallm_verificationtoken" in owner_sql
    assert 'IF NEW."owner_account_id" IS NOT NULL THEN' in owner_sql
    assert 'IF NEW."created_by_owner_account_id" IS NULL' not in owner_sql
    assert 'IF NOT NEW."created_by_owner_snapshot_complete" THEN' in owner_sql
    assert 'NEW."created_by_owner_snapshot_complete" := TRUE' in owner_sql
    assert "IF FOUND THEN" in owner_sql
    assert "_deltallm_reporting_writer_version" in owner_sql
