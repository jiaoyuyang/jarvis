#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REPORT_DIR="$PROJECT_DIR/runtime/acceptance"
REPORT="$REPORT_DIR/jarvis-regression-$STAMP.txt"
TEMP_REPORT="$(mktemp)"
trap 'rm -f "$TEMP_REPORT"' EXIT

mkdir -p "$REPORT_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Tracked repository files have uncommitted changes." >&2
  exit 1
fi

{
  echo "jarvis_regression=$STAMP"
  echo "release=$(git rev-parse HEAD)"
  echo "unit_tests=running"
  python3 -m unittest discover -s tests
  echo "unit_tests=PASS"

  echo "workflow_check=running"
  ./scripts/workflow-status.sh
  echo "workflow_check=PASS"

  echo "codex_check=running"
  ./scripts/codex-status.sh
  echo "codex_check=PASS"

  health="$(docker inspect --format '{{.State.Health.Status}}' jarvis)"
  echo "health=$health"
  test "$health" = "healthy"

  echo "technical_regression=PASS"
  echo "dingtalk_acceptance=PENDING"
} >"$TEMP_REPORT" 2>&1

grep -q '^jarvis-memory=installed$' "$TEMP_REPORT"
grep -q '^jarvis-presentation=installed$' "$TEMP_REPORT"
grep -q '^workspace_workflows=verified$' "$TEMP_REPORT"
grep -q '^backend=codex$' "$TEMP_REPORT"
grep -q '^sandbox=danger-full-access$' "$TEMP_REPORT"
grep -q '^final_only=true$' "$TEMP_REPORT"
grep -q '^final_only_patch=installed$' "$TEMP_REPORT"
grep -q '^turn_recovery_patch=installed$' "$TEMP_REPORT"
grep -q '^health=healthy$' "$TEMP_REPORT"

install -m 600 "$TEMP_REPORT" "$REPORT"
cat "$REPORT"
echo "report=$REPORT"
