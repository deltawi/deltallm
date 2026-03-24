#!/usr/bin/env bash
set -uo pipefail

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
TOTAL_PASS=0
TOTAL_FAIL=0

echo "============================================================="
echo "  DeltaLLM E2E Rate Limit Test Suite"
echo "  $(date)"
echo "============================================================="
echo ""

run_script() {
  local script="$1"
  local name="$(basename "$script" .sh)"
  echo ""
  echo ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>"
  echo "  Running: $name"
  echo ">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>"
  echo ""
  if bash "$script"; then
    echo ""
    echo "  >>> $name: ALL PASSED <<<"
  else
    echo ""
    echo "  >>> $name: SOME FAILURES <<<"
    TOTAL_FAIL=$((TOTAL_FAIL+1))
  fi
  echo ""
}

run_script "$SCRIPTS_DIR/01_setup_test_environment.sh"
run_script "$SCRIPTS_DIR/02_test_rpm_rate_limits.sh"
run_script "$SCRIPTS_DIR/03_test_rate_limit_headers.sh"
run_script "$SCRIPTS_DIR/04_test_multi_window_limits.sh"
run_script "$SCRIPTS_DIR/05_test_admin_crud_limits.sh"
run_script "$SCRIPTS_DIR/06_test_cache_invalidation.sh"
run_script "$SCRIPTS_DIR/07_test_team_org_limits.sh"

echo ""
echo "============================================================="
if [ "$TOTAL_FAIL" -eq 0 ]; then
  echo "  ALL TEST SCENARIOS PASSED!"
else
  echo "  $TOTAL_FAIL SCENARIO(S) HAD FAILURES"
fi
echo "============================================================="
