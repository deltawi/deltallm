#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd "$(dirname "$0")/.." && pwd)"
target_migration="20260816121100_organization_deletion_write_fences"
migration_database_url="${ORGANIZATION_DELETION_MIGRATION_TEST_DATABASE_URL:-}"

if [[ -z "$migration_database_url" ]]; then
  echo "ORGANIZATION_DELETION_MIGRATION_TEST_DATABASE_URL is required" >&2
  exit 1
fi
if [[ "$migration_database_url" != *"organization_deletion_migration_test"* ]]; then
  echo "Refusing to reset a database URL without the organization_deletion_migration_test marker" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "Missing required binary: uv" >&2
  exit 1
fi

migration_tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/deltallm-org-delete-migration.XXXXXX")"
trap 'rm -rf "${migration_tmp_dir:?}"' EXIT
mkdir -p "$migration_tmp_dir/prisma/migrations"
cp "$repository_root/prisma/schema.prisma" "$migration_tmp_dir/prisma/schema.prisma"

target_found=false
for migration_dir in "$repository_root"/prisma/migrations/*; do
  migration_name="$(basename "$migration_dir")"
  if [[ "$migration_name" == "$target_migration" ]]; then
    target_found=true
    break
  fi
  cp -R "$migration_dir" "$migration_tmp_dir/prisma/migrations/"
done
if [[ "$target_found" != true ]]; then
  echo "Target migration not found: $target_migration" >&2
  exit 1
fi

printf '%s\n' \
  'DROP SCHEMA IF EXISTS public CASCADE;' \
  'CREATE SCHEMA public;' \
  | env DATABASE_URL="$migration_database_url" uv run prisma db execute \
      --stdin --schema="$migration_tmp_dir/prisma/schema.prisma"

env DATABASE_URL="$migration_database_url" uv run prisma migrate deploy \
  --schema="$migration_tmp_dir/prisma/schema.prisma"

printf '%s\n' \
  "INSERT INTO deltallm_organizationtable (organization_id, organization_name, created_at, updated_at) VALUES ('org-upgrade-fixture', 'Upgrade fixture', NOW(), NOW());" \
  "INSERT INTO deltallm_teamtable (team_id, team_alias, organization_id, models, created_at, updated_at) VALUES ('team-upgrade-fixture', 'Upgrade team', 'org-upgrade-fixture', ARRAY[]::text[], NOW(), NOW());" \
  "INSERT INTO deltallm_verificationtoken (token, key_name, team_id, models, created_at, updated_at) VALUES ('key-upgrade-fixture', 'Upgrade key', 'team-upgrade-fixture', ARRAY[]::text[], NOW(), NOW());" \
  "INSERT INTO deltallm_batch_file (file_id, purpose, filename, bytes, storage_backend, storage_key, created_at) VALUES ('file-upgrade-fixture', 'batch', 'input.jsonl', 2, 'local', 'upgrade/input.jsonl', NOW());" \
  "INSERT INTO deltallm_batch_job (batch_id, endpoint, status, input_file_id, created_by_api_key, created_at) VALUES ('batch-upgrade-fixture', '/v1/chat/completions', 'completed', 'file-upgrade-fixture', 'key-upgrade-fixture', NOW());" \
  "INSERT INTO deltallm_teamtombstone (team_id, organization_id, deletion_job_id, deleted_at) VALUES ('team-upgrade-tombstone', 'org-upgrade-fixture', 'job-upgrade-tombstone', NOW());" \
  | env DATABASE_URL="$migration_database_url" uv run prisma db execute \
      --stdin --schema="$migration_tmp_dir/prisma/schema.prisma"

env DATABASE_URL="$migration_database_url" uv run python \
  -m src.organization_deletion_migrations deploy \
  --schema "$repository_root/prisma/schema.prisma"

printf '%s\n' \
  'DO $$' \
  'BEGIN' \
  "  IF (SELECT lifecycle_state FROM deltallm_organizationtable WHERE organization_id = 'org-upgrade-fixture') IS DISTINCT FROM 'active' THEN" \
  "    RAISE EXCEPTION 'existing organization lifecycle state was not backfilled';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM deltallm_teamtable WHERE team_id = 'team-upgrade-fixture') THEN" \
  "    RAISE EXCEPTION 'existing team was not preserved';" \
  '  END IF;' \
  "  IF (SELECT created_by_organization_id FROM deltallm_batch_job WHERE batch_id = 'batch-upgrade-fixture') IS DISTINCT FROM 'org-upgrade-fixture' THEN" \
  "    RAISE EXCEPTION 'legacy batch ownership was not backfilled';" \
  '  END IF;' \
  "  IF to_regclass('public.deltallm_organizationdeletionjob') IS NULL THEN" \
  "    RAISE EXCEPTION 'organization deletion job table is missing';" \
  '  END IF;' \
  "  IF to_regclass('public.deltallm_organizationtombstone') IS NULL THEN" \
  "    RAISE EXCEPTION 'organization tombstone table is missing';" \
  '  END IF;' \
  "  IF to_regclass('public.deltallm_teamtombstone') IS NULL THEN" \
  "    RAISE EXCEPTION 'team tombstone table is missing';" \
  '  END IF;' \
  "  IF to_regclass('public.deltallm_organizationprincipaltombstone') IS NULL THEN" \
  "    RAISE EXCEPTION 'organization principal tombstone table is missing';" \
  '  END IF;' \
  '  BEGIN' \
  "    INSERT INTO deltallm_callabletargetbinding (callable_target_binding_id, callable_key, scope_type, scope_id) VALUES ('upgrade-missing-scope', 'upgrade-missing-scope', 'team', 'missing-team-upgrade-fixture');" \
  "    RAISE EXCEPTION 'missing team scope write was accepted';" \
  '  EXCEPTION WHEN check_violation THEN' \
  '    NULL;' \
  '  END;' \
  '  BEGIN' \
  "    INSERT INTO deltallm_callabletargetbinding (callable_target_binding_id, callable_key, scope_type, scope_id) VALUES ('upgrade-tombstoned-scope', 'upgrade-tombstoned-scope', 'team', 'team-upgrade-tombstone');" \
  "    RAISE EXCEPTION 'tombstoned team scope write was accepted';" \
  '  EXCEPTION WHEN check_violation THEN' \
  '    NULL;' \
  '  END;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_batch_file_org_created_idx'::regclass AND indisvalid) THEN" \
  "    RAISE EXCEPTION 'batch file organization index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_promptrenderlog_org_created_idx'::regclass AND indisvalid) THEN" \
  "    RAISE EXCEPTION 'prompt log organization index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_promptrenderlog_team_created_idx'::regclass AND indisvalid) THEN" \
  "    RAISE EXCEPTION 'prompt log team index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_promptrenderlog_api_key_created_idx'::regclass AND indisvalid) THEN" \
  "    RAISE EXCEPTION 'prompt log API key index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_promptrenderlog_user_created_idx'::regclass AND indisvalid) THEN" \
  "    RAISE EXCEPTION 'prompt log user index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_mcpapproval_scope_status_created_idx'::regclass AND indisvalid) THEN" \
  "    RAISE EXCEPTION 'MCP approval scope index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_organizationtable_lifecycle_state_idx'::regclass AND indisvalid AND indisready) THEN" \
  "    RAISE EXCEPTION 'organization lifecycle index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_mcpapproval_org_status_created_idx'::regclass AND indisvalid AND indisready) THEN" \
  "    RAISE EXCEPTION 'MCP approval organization index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_mcpapproval_api_key_created_idx'::regclass AND indisvalid AND indisready) THEN" \
  "    RAISE EXCEPTION 'MCP approval requester API key index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_mcpapproval_user_created_idx'::regclass AND indisvalid AND indisready) THEN" \
  "    RAISE EXCEPTION 'MCP approval requester user index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_batch_webhook_outbox_org_status_created_idx'::regclass AND indisvalid AND indisready) THEN" \
  "    RAISE EXCEPTION 'batch webhook organization index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_batch_job_user_created_idx'::regclass AND indisvalid AND indisready) THEN" \
  "    RAISE EXCEPTION 'batch job user index is missing or invalid';" \
  '  END IF;' \
  "  IF NOT EXISTS (SELECT 1 FROM pg_index WHERE indexrelid = 'deltallm_batch_create_session_user_status_created_idx'::regclass AND indisvalid AND indisready) THEN" \
  "    RAISE EXCEPTION 'batch create session user index is missing or invalid';" \
  '  END IF;' \
  "  IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deltallm_organizationtable_lifecycle_state_check' AND NOT convalidated) THEN" \
  "    RAISE EXCEPTION 'organization lifecycle state constraint is not validated';" \
  '  END IF;' \
  'END' \
  '$$;' \
  | env DATABASE_URL="$migration_database_url" uv run prisma db execute \
      --stdin --schema="$repository_root/prisma/schema.prisma"

printf '%s\n' \
  'DROP SCHEMA IF EXISTS public CASCADE;' \
  'CREATE SCHEMA public;' \
  | env DATABASE_URL="$migration_database_url" uv run prisma db execute \
      --stdin --schema="$repository_root/prisma/schema.prisma"

env DATABASE_URL="$migration_database_url" uv run python \
  -m src.organization_deletion_migrations deploy \
  --schema "$repository_root/prisma/schema.prisma"

printf '%s\n' \
  'DO $$' \
  'BEGIN' \
  "  IF to_regclass('public.deltallm_organizationdeletionjob') IS NULL OR to_regclass('public.deltallm_teamtombstone') IS NULL OR to_regclass('public.deltallm_organizationprincipaltombstone') IS NULL THEN" \
  "    RAISE EXCEPTION 'fresh organization deletion schema is incomplete';" \
  '  END IF;' \
  'END' \
  '$$;' \
  | env DATABASE_URL="$migration_database_url" uv run prisma db execute \
      --stdin --schema="$repository_root/prisma/schema.prisma"

echo "Organization deletion migration upgrade and fresh-install verification passed"
