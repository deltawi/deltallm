#!/usr/bin/env python3
"""Verify current migrations on fresh, last-release, and shared-feature databases.

The verifier creates uniquely named disposable databases, applies the current
migration chain to one, upgrades one from a Git ref after seeding compatibility
data, and upgrades one from the already-shared route-policy migration. Every
database is dropped on exit.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
CURRENT_SCHEMA = REPO_ROOT / "prisma" / "schema.prisma"
DATABASE_NAME_PATTERN = re.compile(r"\Adeltallm_migration_verify_[a-z0-9_]+\Z")
UPGRADE_ORGANIZATION_ID = "migration-upgrade-fixture-org"
UPGRADE_PROMPT_RENDER_ID = "migration-upgrade-fixture-render"
UPGRADE_EMAIL_ID = "migration-upgrade-fixture-email"
UPGRADE_SPEND_EVENT_ID = "migration-upgrade-fixture-spend"
UPGRADE_ROUTE_GROUP_ID = "migration-upgrade-route-group"
UPGRADE_MODEL_DEPLOYMENT_ID = "migration-upgrade-model-deployment"
UPGRADE_MODEL_DEPLOYMENT_SECOND_ID = "migration-upgrade-model-deployment-second"
UPGRADE_MODEL_NAME = "migration-upgrade-model"
STABLE_RELEASE_TAG_PATTERN = re.compile(r"\Av\d+\.\d+\.\d+\Z")
SHARED_ROUTE_POLICY_MIGRATION_REF = "3372602bf7bff6107ee9595217b7f2fd75da61cd"


def database_url_for(base_url: str, database_name: str) -> str:
    """Return *base_url* with only its database path replaced."""
    if not DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise ValueError(f"unsafe temporary database name: {database_name!r}")
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("admin database URL must be a PostgreSQL URL with a host")
    return urlunsplit(
        SplitResult(
            scheme=parsed.scheme,
            netloc=parsed.netloc,
            path=f"/{database_name}",
            query=parsed.query,
            fragment=parsed.fragment,
        )
    )


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> None:
    subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        input=stdin,
        text=True,
        check=True,
    )


def _database_env(database_url: str) -> dict[str, str]:
    return {**os.environ, "DATABASE_URL": database_url}


def _db_execute(
    prisma: str,
    *,
    schema: Path,
    database_url: str,
    sql: str,
) -> None:
    _run(
        [prisma, "db", "execute", "--schema", str(schema), "--stdin"],
        env=_database_env(database_url),
        stdin=sql,
    )


def _migrate(prisma: str, *, schema: Path, database_url: str) -> None:
    _run(
        [prisma, "migrate", "deploy", "--schema", str(schema)],
        env=_database_env(database_url),
    )


def _extract_prisma_at_ref(base_ref: str, destination: Path) -> Path:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", base_ref, "prisma"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        destination_resolved = destination.resolve()
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination_resolved):
                raise ValueError(f"unsafe path in Git archive: {member.name!r}")
        tar.extractall(destination)
    schema = destination / "prisma" / "schema.prisma"
    if not schema.is_file():
        raise FileNotFoundError(f"{base_ref!r} does not contain prisma/schema.prisma")
    return schema


def _resolve_prisma_command(explicit: str | None) -> str:
    if explicit:
        return explicit
    discovered = shutil.which("prisma")
    if discovered:
        return discovered
    local = REPO_ROOT / ".venv" / "bin" / "prisma"
    if local.is_file():
        return str(local)
    raise FileNotFoundError("prisma executable not found; run this script via `uv run`")


def _default_base_ref() -> str:
    configured = os.getenv("MIGRATION_TEST_BASE_REF")
    if configured and (normalized := configured.strip()):
        return normalized
    tags = subprocess.run(
        ["git", "tag", "--merged", "origin/main", "--sort=-version:refname"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    try:
        return next(tag for tag in tags if STABLE_RELEASE_TAG_PATTERN.fullmatch(tag))
    except StopIteration as exc:
        raise RuntimeError(
            "no stable release tag reachable from origin/main; "
            "set MIGRATION_TEST_BASE_REF explicitly"
        ) from exc


def _create_database(prisma: str, admin_url: str, database_name: str) -> None:
    _db_execute(
        prisma,
        schema=CURRENT_SCHEMA,
        database_url=admin_url,
        sql=f'CREATE DATABASE "{database_name}";',
    )


def _drop_database(prisma: str, admin_url: str, database_name: str) -> None:
    if not DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise ValueError(f"refusing to drop unsafe database name: {database_name!r}")
    _db_execute(
        prisma,
        schema=CURRENT_SCHEMA,
        database_url=admin_url,
        sql=(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{database_name}' AND pid <> pg_backend_pid();"
        ),
    )
    _db_execute(
        prisma,
        schema=CURRENT_SCHEMA,
        database_url=admin_url,
        sql=f'DROP DATABASE IF EXISTS "{database_name}";',
    )


def _seed_upgrade_fixture(prisma: str, database_url: str, base_schema: Path) -> None:
    _db_execute(
        prisma,
        schema=base_schema,
        database_url=database_url,
        sql=f"""
INSERT INTO deltallm_organizationtable
  (id, organization_id, organization_name, audit_content_storage_enabled)
VALUES
  ('migration-upgrade-fixture-id', '{UPGRADE_ORGANIZATION_ID}',
   'Migration upgrade fixture', TRUE);

INSERT INTO deltallm_promptrenderlog
  (prompt_render_log_id, request_id, status, variables)
VALUES
  ('{UPGRADE_PROMPT_RENDER_ID}', 'migration-upgrade-request', 'success',
   '{{"region":"test"}}'::jsonb);

INSERT INTO deltallm_emailoutbox
  (email_id, kind, provider, to_addresses, from_address, subject, text_body)
VALUES
  ('{UPGRADE_EMAIL_ID}', 'test', 'smtp', ARRAY['upgrade@example.com'],
   'noreply@example.com', 'Upgrade fixture', 'Upgrade fixture');

INSERT INTO deltallm_spendlog_events
  (id, request_id, call_type, api_key, model, spend, start_time, end_time)
VALUES
  ('{UPGRADE_SPEND_EVENT_ID}', 'migration-upgrade-request', 'migration',
   'migration-upgrade-key', 'migration-upgrade-model', 12.34, NOW(), NOW());

INSERT INTO deltallm_modeldeployment
  (deployment_id, model_name, deltallm_params)
VALUES
  ('{UPGRADE_MODEL_DEPLOYMENT_ID}', '{UPGRADE_MODEL_NAME}', '{{}}'::jsonb),
  ('{UPGRADE_MODEL_DEPLOYMENT_SECOND_ID}', '{UPGRADE_MODEL_NAME}', '{{}}'::jsonb);

INSERT INTO deltallm_routegroup
  (route_group_id, group_key, mode)
VALUES
  ('{UPGRADE_ROUTE_GROUP_ID}', 'migration-upgrade-route', 'chat');

INSERT INTO deltallm_routepolicy
  (route_policy_id, route_group_id, version, status, policy_json, published_at)
VALUES
  ('migration-upgrade-route-policy-1', '{UPGRADE_ROUTE_GROUP_ID}', 1, 'published',
   '{{"strategy":"weighted","server_revision":1}}'::jsonb, NOW()),
  ('migration-upgrade-route-policy-2', '{UPGRADE_ROUTE_GROUP_ID}', 2, 'published',
   '{{"strategy":"least-busy","server_revision":2}}'::jsonb, NOW());
""",
    )


def _seed_shared_migration_fixture(
    prisma: str,
    database_url: str,
    shared_schema: Path,
) -> None:
    _db_execute(
        prisma,
        schema=shared_schema,
        database_url=database_url,
        sql=f"""
INSERT INTO deltallm_modeldeployment
  (deployment_id, model_name, deltallm_params)
VALUES
  ('{UPGRADE_MODEL_DEPLOYMENT_ID}', '{UPGRADE_MODEL_NAME}', '{{}}'::jsonb);
""",
    )


def _verify_fresh_database(prisma: str, database_url: str) -> None:
    _db_execute(
        prisma,
        schema=CURRENT_SCHEMA,
        database_url=database_url,
        sql="""
DO $migration_verify$
BEGIN
  IF to_regclass('public.deltallm_spend_ingestion_outbox') IS NULL
     OR to_regclass('public.deltallm_audit_ingestion_outbox') IS NULL
     OR to_regclass('public.deltallm_telemetry_ingestion_capacity') IS NULL THEN
    RAISE EXCEPTION 'telemetry ingestion tables are missing';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'deltallm_organizationtable'
      AND column_name = 'audit_content_policy_version'
  ) THEN
    RAISE EXCEPTION 'audit content policy version column is missing';
  END IF;
  IF (
    SELECT count(*)
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN (
        'deltallm_spend_ingestion_outbox',
        'deltallm_audit_ingestion_outbox'
      )
      AND column_name IN (
        'claim_token', 'blocked_at', 'replay_count',
        'last_replayed_at', 'last_replayed_by'
      )
  ) <> 10 THEN
    RAISE EXCEPTION 'telemetry fencing/replay columns are missing';
  END IF;
  IF (SELECT count(*) FROM deltallm_telemetry_ingestion_capacity
      WHERE queue_name IN ('spend', 'audit') AND pending_count = 0) <> 2 THEN
    RAISE EXCEPTION 'telemetry capacity seed rows are invalid';
  END IF;
  IF (
    SELECT count(*)
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND data_type = 'numeric'
      AND numeric_precision = 38
      AND numeric_scale = 18
      AND (
        (table_name = 'deltallm_spendlog_events'
         AND column_name IN ('spend_exact', 'provider_cost_exact'))
        OR (table_name IN (
              'deltallm_teammodelspend', 'deltallm_verificationtoken',
              'deltallm_usertable', 'deltallm_teamtable',
              'deltallm_organizationtable'
            ) AND column_name = 'spend_exact')
      )
  ) <> 7 THEN
    RAISE EXCEPTION 'exact spend expansion columns are missing';
  END IF;
  IF (
    SELECT count(*)
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'deltallm_emailoutbox'
      AND column_name IN (
        'delivery_audit_status', 'delivery_audit_event_id',
        'delivery_audit_attempt_count', 'delivery_audit_max_attempts',
        'delivery_audit_next_attempt_at', 'delivery_audit_last_error',
        'delivery_audit_locked_by', 'delivery_audit_lease_expires_at',
        'delivery_audited_at'
      )
  ) <> 9 THEN
    RAISE EXCEPTION 'email delivery audit reconciliation columns are missing';
  END IF;
  IF (
    SELECT count(*)
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'deltallm_emailoutbox'
      AND column_name IN (
        'delivery_locked_by', 'delivery_claim_token',
        'delivery_lease_expires_at', 'delivery_started_at',
        'delivery_blocked_at', 'delivery_audit_claim_token',
        'delivery_audit_blocked_at', 'delivery_audit_replay_count',
        'delivery_audit_last_replayed_at', 'delivery_audit_last_replayed_by'
      )
  ) <> 10 THEN
    RAISE EXCEPTION 'email delivery fencing/replay columns are missing';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'deltallm_emailoutbox_delivery_audit_status_check'
      AND pg_get_constraintdef(oid) LIKE '%waiting%'
      AND pg_get_constraintdef(oid) LIKE '%blocked%'
  ) THEN
    RAISE EXCEPTION 'email delivery audit status constraint is stale';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname = 'deltallm_routepolicy_one_published_per_group'
  ) THEN
    RAISE EXCEPTION 'route policy publication invariant index is missing';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'deltallm_routepolicy'
      AND column_name = 'semantics_version'
  ) THEN
    RAISE EXCEPTION 'route policy semantics version column is missing';
  END IF;
  IF (SELECT count(*) FROM deltallm_routeruntimestate
      WHERE state_key = 'routing_runtime'
        AND revision = 0
        AND route_groups_initialized = FALSE) <> 1 THEN
    RAISE EXCEPTION 'route runtime revision seed row is invalid';
  END IF;
  IF EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname = 'deltallm_modeldeployment_model_name_key'
  ) THEN
    RAISE EXCEPTION 'model deployment name uniqueness was not removed';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname = 'deltallm_modeldeployment_model_name_idx'
      AND indexdef NOT LIKE 'CREATE UNIQUE INDEX%'
  ) THEN
    RAISE EXCEPTION 'non-unique model deployment name index is missing';
  END IF;
  IF to_regclass('public._deltallm_model_name_restore_20260823') IS NOT NULL THEN
    RAISE EXCEPTION 'temporary model-name restore table was not removed';
  END IF;
END
$migration_verify$;
""",
    )


def _verify_upgrade_database(prisma: str, database_url: str) -> None:
    _db_execute(
        prisma,
        schema=CURRENT_SCHEMA,
        database_url=database_url,
        sql=f"""
DO $migration_verify$
DECLARE
  fixture_count INTEGER;
BEGIN
  SELECT count(*) INTO fixture_count
  FROM deltallm_organizationtable
  WHERE organization_id = '{UPGRADE_ORGANIZATION_ID}'
    AND organization_name = 'Migration upgrade fixture'
    AND audit_content_storage_enabled = TRUE
    AND audit_content_policy_version = 0;
  IF fixture_count <> 1 THEN
    RAISE EXCEPTION 'organization upgrade fixture was not preserved';
  END IF;

  SELECT count(*) INTO fixture_count
  FROM deltallm_promptrenderlog
  WHERE prompt_render_log_id = '{UPGRADE_PROMPT_RENDER_ID}'
    AND variables = '{{"region":"test"}}'::jsonb
    AND variables_redacted = FALSE;
  IF fixture_count <> 1 THEN
    RAISE EXCEPTION 'prompt render upgrade fixture was not preserved';
  END IF;

  IF (
    SELECT count(*)
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN (
        'deltallm_spend_ingestion_outbox',
        'deltallm_audit_ingestion_outbox'
      )
      AND column_name IN ('claim_token', 'blocked_at', 'replay_count')
  ) <> 6 THEN
    RAISE EXCEPTION 'telemetry fencing/replay upgrade columns are missing';
  END IF;

  SELECT count(*) INTO fixture_count
  FROM deltallm_emailoutbox
  WHERE email_id = '{UPGRADE_EMAIL_ID}'
    AND status = 'queued'
    AND delivery_audit_status = 'not_required'
    AND delivery_audit_attempt_count = 0
    AND delivery_audit_max_attempts = 10
    AND delivery_audit_event_id IS NULL
    AND delivery_claim_token IS NULL
    AND delivery_lease_expires_at IS NULL
    AND delivery_audit_claim_token IS NULL
    AND delivery_audit_blocked_at IS NULL
    AND delivery_audit_replay_count = 0;
  IF fixture_count <> 1 THEN
    RAISE EXCEPTION 'email delivery audit upgrade defaults are invalid';
  END IF;

  SELECT count(*) INTO fixture_count
  FROM deltallm_spendlog_events
  WHERE id = '{UPGRADE_SPEND_EVENT_ID}'
    AND spend = 12.34
    AND spend_exact IS NULL
    AND provider_cost_exact IS NULL;
  IF fixture_count <> 1 THEN
    RAISE EXCEPTION 'legacy spend event was not preserved by exact-money expansion';
  END IF;

  SELECT count(*) INTO fixture_count
  FROM deltallm_organizationtable
  WHERE organization_id = '{UPGRADE_ORGANIZATION_ID}'
    AND spend_exact IS NULL;
  IF fixture_count <> 1 THEN
    RAISE EXCEPTION 'legacy organization spend was not preserved by exact-money expansion';
  END IF;

  SELECT count(*) INTO fixture_count
  FROM deltallm_routepolicy
  WHERE route_group_id = '{UPGRADE_ROUTE_GROUP_ID}'
    AND status = 'published'
    AND version = 2
    AND semantics_version = 1
    AND policy_json->>'server_revision' = '2';
  IF fixture_count <> 1 THEN
    RAISE EXCEPTION 'route policy publication reconciliation did not retain the latest version';
  END IF;

  SELECT count(*) INTO fixture_count
  FROM deltallm_routepolicy
  WHERE route_group_id = '{UPGRADE_ROUTE_GROUP_ID}'
    AND status = 'archived'
    AND version = 1
    AND semantics_version = 1
    AND policy_json->>'server_revision' = '1';
  IF fixture_count <> 1 THEN
    RAISE EXCEPTION 'route policy publication reconciliation did not preserve history';
  END IF;

  SELECT count(*) INTO fixture_count
  FROM deltallm_routeruntimestate
  WHERE state_key = 'routing_runtime'
    AND revision = 0
    AND route_groups_initialized = TRUE;
  IF fixture_count <> 1 THEN
    RAISE EXCEPTION 'route runtime state did not preserve initialized route groups';
  END IF;

  SELECT count(*) INTO fixture_count
  FROM deltallm_modeldeployment
  WHERE deployment_id IN (
      '{UPGRADE_MODEL_DEPLOYMENT_ID}',
      '{UPGRADE_MODEL_DEPLOYMENT_SECOND_ID}'
    )
    AND model_name = '{UPGRADE_MODEL_NAME}';
  IF fixture_count <> 2 THEN
    RAISE EXCEPTION 'implicit model-group upgrade fixtures were not preserved';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname = 'deltallm_modeldeployment_model_name_key'
  ) THEN
    RAISE EXCEPTION 'model deployment name uniqueness was not removed';
  END IF;
  IF NOT EXISTS (
    SELECT 1
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname = 'deltallm_modeldeployment_model_name_idx'
      AND indexdef NOT LIKE 'CREATE UNIQUE INDEX%'
  ) THEN
    RAISE EXCEPTION 'non-unique model deployment name index was not installed';
  END IF;
  IF to_regclass('public._deltallm_model_name_restore_20260823') IS NOT NULL THEN
    RAISE EXCEPTION 'temporary model-name restore table was not removed';
  END IF;
END
$migration_verify$;
""",
    )


def _verify_shared_migration_database(prisma: str, database_url: str) -> None:
    _db_execute(
        prisma,
        schema=CURRENT_SCHEMA,
        database_url=database_url,
        sql=f"""
INSERT INTO deltallm_modeldeployment
  (deployment_id, model_name, deltallm_params)
VALUES
  ('{UPGRADE_MODEL_DEPLOYMENT_SECOND_ID}', '{UPGRADE_MODEL_NAME}', '{{}}'::jsonb);

DO $migration_verify$
BEGIN
  IF (
    SELECT count(*)
    FROM deltallm_modeldeployment
    WHERE model_name = '{UPGRADE_MODEL_NAME}'
      AND deployment_id IN (
        '{UPGRADE_MODEL_DEPLOYMENT_ID}',
        '{UPGRADE_MODEL_DEPLOYMENT_SECOND_ID}'
      )
  ) <> 2 THEN
    RAISE EXCEPTION 'shared-migration database did not accept an implicit model group';
  END IF;
  IF to_regclass('public.deltallm_modeldeployment_model_name_key') IS NOT NULL THEN
    RAISE EXCEPTION 'shared-migration model-name uniqueness was not removed';
  END IF;
  IF to_regclass('public.deltallm_modeldeployment_model_name_idx') IS NULL THEN
    RAISE EXCEPTION 'shared-migration non-unique model-name index is missing';
  END IF;
  IF to_regclass('public._deltallm_model_name_restore_20260823') IS NOT NULL THEN
    RAISE EXCEPTION 'shared-migration temporary model-name restore table was not removed';
  END IF;
END
$migration_verify$;
""",
    )


def verify_migration_paths(*, admin_url: str, base_ref: str, prisma: str) -> None:
    suffix = uuid.uuid4().hex[:12]
    fresh_name = f"deltallm_migration_verify_{suffix}_fresh"
    upgrade_name = f"deltallm_migration_verify_{suffix}_upgrade"
    shared_name = f"deltallm_migration_verify_{suffix}_shared"
    created: list[str] = []

    print(f"Verifying fresh install and upgrade from {base_ref}...")
    try:
        for name in (fresh_name, upgrade_name, shared_name):
            _create_database(prisma, admin_url, name)
            created.append(name)

        fresh_url = database_url_for(admin_url, fresh_name)
        upgrade_url = database_url_for(admin_url, upgrade_name)
        shared_url = database_url_for(admin_url, shared_name)
        _migrate(prisma, schema=CURRENT_SCHEMA, database_url=fresh_url)
        _verify_fresh_database(prisma, fresh_url)

        with tempfile.TemporaryDirectory(prefix="deltallm-migration-base-") as temp:
            temp_root = Path(temp)
            base_schema = _extract_prisma_at_ref(base_ref, temp_root / "base")
            _migrate(prisma, schema=base_schema, database_url=upgrade_url)
            _seed_upgrade_fixture(prisma, upgrade_url, base_schema)
            _migrate(prisma, schema=CURRENT_SCHEMA, database_url=upgrade_url)
            shared_schema = _extract_prisma_at_ref(
                SHARED_ROUTE_POLICY_MIGRATION_REF,
                temp_root / "shared",
            )
            _migrate(prisma, schema=shared_schema, database_url=shared_url)
            _seed_shared_migration_fixture(prisma, shared_url, shared_schema)
            _migrate(prisma, schema=CURRENT_SCHEMA, database_url=shared_url)
        _verify_upgrade_database(prisma, upgrade_url)
        _verify_shared_migration_database(prisma, shared_url)
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_errors: list[subprocess.CalledProcessError] = []
        for name in reversed(created):
            try:
                _drop_database(prisma, admin_url, name)
            except subprocess.CalledProcessError as exc:
                cleanup_errors.append(exc)
        if cleanup_errors and primary_error is None:
            raise cleanup_errors[0]

    print("Fresh-install, last-release, and shared-feature migration checks passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admin-database-url",
        default=os.getenv("MIGRATION_TEST_ADMIN_DATABASE_URL"),
        help="PostgreSQL admin URL used only to create disposable databases",
    )
    parser.add_argument("--base-ref", default=None, help="last supported release Git ref")
    parser.add_argument("--prisma", default=None, help="path to the prisma executable")
    args = parser.parse_args()
    if not args.admin_database_url:
        parser.error("--admin-database-url or MIGRATION_TEST_ADMIN_DATABASE_URL is required")

    verify_migration_paths(
        admin_url=args.admin_database_url,
        base_ref=args.base_ref or _default_base_ref(),
        prisma=_resolve_prisma_command(args.prisma),
    )


if __name__ == "__main__":
    main()
