#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCHEMA_PATH="${REPO_ROOT}/prisma/schema.prisma"
FIXTURE_DIR="${REPO_ROOT}/tests/fixtures/prisma"
PREVIOUS_RELEASE_SNAPSHOT_PATH="${FIXTURE_DIR}/previous_release_snapshot.sql"
PREVIOUS_RELEASE_SEED_PATH="${FIXTURE_DIR}/previous_release_seed.sql"
SMOKE_TEST_PATH="${REPO_ROOT}/tests/test_prisma_upgrade_paths.py"

scenario="${1:-}"
if [[ -z "${scenario}" ]]; then
  echo "usage: $0 <fresh_install|previous_release_upgrade>" >&2
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

run_prisma_sql() {
  local sql="$1"
  printf '%s\n' "${sql}" | uv run prisma db execute --stdin --url "${DATABASE_URL}"
}

run_prisma_sql_file() {
  local path="$1"
  run_cmd uv run prisma db execute --file "${path}" --url "${DATABASE_URL}"
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

run_fresh_install_scenario() {
  reset_schemas
  run_cmd uv run prisma migrate deploy --schema="${SCHEMA_PATH}"
  run_smoke_suite
}

run_previous_release_upgrade_scenario() {
  restore_fixture "${PREVIOUS_RELEASE_SNAPSHOT_PATH}" "${PREVIOUS_RELEASE_SEED_PATH}"
  run_cmd uv run prisma migrate deploy --schema="${SCHEMA_PATH}"
  run_smoke_suite
}

require_env "DATABASE_URL"

case "${scenario}" in
  fresh_install)
    run_fresh_install_scenario
    ;;
  previous_release_upgrade)
    run_previous_release_upgrade_scenario
    ;;
  *)
    echo "unsupported scenario: ${scenario}" >&2
    exit 64
    ;;
esac
