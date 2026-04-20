#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCHEMA_PATH="${REPO_ROOT}/prisma/schema.prisma"
LEGACY_HELPER_PATH="${REPO_ROOT}/scripts/prisma/baseline_legacy_environment.py"
FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/prisma"
LEGACY_SNAPSHOT_PATH="${FIXTURE_DIR}/legacy_v0_1_19_snapshot.sql"
LEGACY_SEED_PATH="${FIXTURE_DIR}/legacy_v0_1_19_seed.sql"
PREVIOUS_RELEASE_SNAPSHOT_PATH="${FIXTURE_DIR}/previous_release_snapshot.sql"
PREVIOUS_RELEASE_SEED_PATH="${FIXTURE_DIR}/previous_release_seed.sql"
SMOKE_TEST_PATH="${REPO_ROOT}/tests/test_prisma_upgrade_paths.py"
LATEST_REPO_MIGRATION="$(
  ls "${REPO_ROOT}/prisma/migrations" \
    | rg '^[0-9]' \
    | sort \
    | tail -n 1
)"

scenario="${1:-}"
if [[ -z "${scenario}" ]]; then
  echo "usage: $0 <fresh_install|head_db_push_no_history_baseline|legacy_v0_1_19_refusal|previous_release_v0_1_20_rc2_upgrade>" >&2
  exit 64
fi

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "${name} is required" >&2
    exit 64
  fi
}

run_cmd() {
  echo "+ $*"
  "$@"
}

postgres_container() {
  if [[ -n "${POSTGRES_SERVICE_CONTAINER_ID:-}" ]]; then
    printf '%s\n' "${POSTGRES_SERVICE_CONTAINER_ID}"
    return
  fi
  printf '%s\n' "deltallm-db-1"
}

current_database_name() {
  local database_name="${DATABASE_URL##*/}"
  database_name="${database_name%%\?*}"
  printf '%s\n' "${database_name}"
}

run_prisma_sql() {
  local sql="$1"
  printf '%s\n' "${sql}" | uv run prisma db execute --stdin --url "${DATABASE_URL}"
}

run_prisma_sql_file() {
  local path="$1"
  run_cmd uv run prisma db execute --file "${path}" --url "${DATABASE_URL}"
}

run_postgres_scalar() {
  local database_name="$1"
  local sql="$2"
  docker exec "$(postgres_container)" psql -U postgres -d "${database_name}" -Atqc "${sql}"
}

validate_database_name() {
  local database_name="$1"
  if [[ ! "${database_name}" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "Unsupported database name: ${database_name}" >&2
    exit 64
  fi
}

reset_shadow_database() {
  require_env "PRISMA_SHADOW_DATABASE_NAME"
  validate_database_name "${PRISMA_SHADOW_DATABASE_NAME}"
  run_cmd docker exec "$(postgres_container)" sh -lc \
    "psql -U postgres -d postgres -c 'DROP DATABASE IF EXISTS ${PRISMA_SHADOW_DATABASE_NAME} WITH (FORCE);' -c 'CREATE DATABASE ${PRISMA_SHADOW_DATABASE_NAME};'"
}

reset_schemas() {
  run_prisma_sql '
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
DROP SCHEMA IF EXISTS prisma_shadow CASCADE;
CREATE SCHEMA prisma_shadow;
'
}

restore_fixture() {
  local snapshot_path="$1"
  local seed_path="$2"
  reset_schemas
  run_prisma_sql_file "${snapshot_path}"
  run_prisma_sql_file "${seed_path}"
}

run_smoke_suite() {
  echo "Running upgrade-path smoke suite for scenario: ${scenario}"
  PRISMA_UPGRADE_PATH_SCENARIO="${scenario}" \
    run_cmd uv run pytest -q "${SMOKE_TEST_PATH}"
}

assert_legacy_failure_before_baseline() {
  local output_file
  output_file="$(mktemp)"

  set +e
  uv run prisma migrate deploy --schema="${SCHEMA_PATH}" >"${output_file}" 2>&1
  local status=$?
  set -e

  if [[ ${status} -eq 0 ]]; then
    cat "${output_file}" >&2
    rm -f "${output_file}"
    echo "Expected prisma migrate deploy to fail on a legacy unbaselined database" >&2
    exit 1
  fi

  if ! rg -q 'P3005' "${output_file}"; then
    cat "${output_file}" >&2
    rm -f "${output_file}"
    echo "Expected prisma migrate deploy failure to include Prisma error P3005" >&2
    exit 1
  fi

  rm -f "${output_file}"
}

assert_no_prisma_history_table() {
  local result
  result="$(run_postgres_scalar "$(current_database_name)" "SELECT to_regclass('public._prisma_migrations')::text;")"
  if [[ -n "${result}" ]]; then
    echo "Legacy fixture unexpectedly contains _prisma_migrations: ${result}" >&2
    exit 1
  fi
}

assert_current_latest_migration_absent() {
  local result
  result="$(run_postgres_scalar "$(current_database_name)" "SELECT COUNT(*) FROM public._prisma_migrations WHERE migration_name = '${LATEST_REPO_MIGRATION}';")"
  if [[ "${result}" != "0" ]]; then
    echo "Previous-release fixture already contains the current repo latest migration (${LATEST_REPO_MIGRATION})" >&2
    exit 1
  fi
}

assert_legacy_release_refusal() {
  local output_file
  output_file="$(mktemp)"

  set +e
  uv run python "${LEGACY_HELPER_PATH}" plan \
    --schema "${SCHEMA_PATH}" \
    --database-url "${DATABASE_URL}" \
    --shadow-database-url "${PRISMA_SHADOW_DATABASE_URL}" >"${output_file}" 2>&1
  local status=$?
  set -e

  if [[ ${status} -eq 0 ]]; then
    cat "${output_file}" >&2
    rm -f "${output_file}"
    echo "Expected the baseline helper to refuse the released legacy fixture" >&2
    exit 1
  fi

  if ! rg -q 'Refusing to baseline because the live database differs from the checked-in migration chain' "${output_file}"; then
    cat "${output_file}" >&2
    rm -f "${output_file}"
    echo "Expected the released legacy fixture refusal to explain the migration-chain mismatch" >&2
    exit 1
  fi

  rm -f "${output_file}"
}

run_fresh_install_scenario() {
  reset_schemas
  run_cmd uv run prisma migrate deploy --schema="${SCHEMA_PATH}"
  run_smoke_suite
}

run_head_db_push_no_history_baseline_scenario() {
  require_env "PRISMA_SHADOW_DATABASE_URL"

  reset_schemas
  reset_shadow_database
  run_cmd uv run prisma db push --schema="${SCHEMA_PATH}"
  assert_legacy_failure_before_baseline
  run_cmd uv run python "${LEGACY_HELPER_PATH}" plan \
    --schema "${SCHEMA_PATH}" \
    --database-url "${DATABASE_URL}" \
    --shadow-database-url "${PRISMA_SHADOW_DATABASE_URL}"
  run_cmd uv run python "${LEGACY_HELPER_PATH}" apply --yes \
    --schema "${SCHEMA_PATH}" \
    --database-url "${DATABASE_URL}" \
    --shadow-database-url "${PRISMA_SHADOW_DATABASE_URL}"
  run_cmd uv run prisma migrate deploy --schema="${SCHEMA_PATH}"
  run_smoke_suite
}

run_legacy_v0_1_19_refusal_scenario() {
  require_env "PRISMA_SHADOW_DATABASE_URL"

  restore_fixture "${LEGACY_SNAPSHOT_PATH}" "${LEGACY_SEED_PATH}"
  assert_no_prisma_history_table
  reset_shadow_database
  assert_legacy_failure_before_baseline
  assert_legacy_release_refusal
}

run_previous_release_v0_1_20_rc2_upgrade_scenario() {
  restore_fixture "${PREVIOUS_RELEASE_SNAPSHOT_PATH}" "${PREVIOUS_RELEASE_SEED_PATH}"
  assert_current_latest_migration_absent
  run_cmd uv run prisma migrate deploy --schema="${SCHEMA_PATH}"
  run_smoke_suite
}

require_env "DATABASE_URL"

case "${scenario}" in
  fresh_install)
    run_fresh_install_scenario
    ;;
  head_db_push_no_history_baseline)
    run_head_db_push_no_history_baseline_scenario
    ;;
  legacy_v0_1_19_refusal)
    run_legacy_v0_1_19_refusal_scenario
    ;;
  previous_release_v0_1_20_rc2_upgrade)
    run_previous_release_v0_1_20_rc2_upgrade_scenario
    ;;
  *)
    echo "unsupported scenario: ${scenario}" >&2
    exit 64
    ;;
esac
