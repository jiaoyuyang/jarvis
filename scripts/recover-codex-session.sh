#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || -z "$1" ]]; then
  echo "Usage: $0 SESSION_ID" >&2
  exit 2
fi

SESSION_ID="$1"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

echo "Creating a recoverable Jarvis backup..."
./scripts/backup.sh

echo "Stopping Jarvis before resetting one Codex thread mapping..."
docker compose stop jarvis
JARVIS_RECOVERY_NEEDS_START=true
trap '
  if [[ "$JARVIS_RECOVERY_NEEDS_START" == "true" ]]; then
    docker compose up -d jarvis >/dev/null 2>&1 || true
  fi
' EXIT

docker compose run --rm --no-deps \
  -e JARVIS_RECOVERY_SESSION_ID="$SESSION_ID" \
  --entrypoint /app/venv/bin/python jarvis - <<'PY'
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

session_id = os.environ["JARVIS_RECOVERY_SESSION_ID"]
root = Path("/app/working/workspaces/default")
paths = list(root.rglob("codex_sessions.json"))
if len(paths) != 1:
    raise SystemExit(
        f"expected exactly one codex_sessions.json, found {len(paths)}: "
        + ", ".join(str(path) for path in paths)
    )

path = paths[0]
data = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    raise SystemExit(f"invalid Codex session map: {path}")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = path.with_name(f"{path.name}.before-recovery-{stamp}")
shutil.copy2(path, backup)
removed = data.pop(session_id, None)

tmp = path.with_suffix(".tmp")
tmp.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
tmp.replace(path)

print(f"session_map={path}")
print(f"session_id={session_id}")
print("mapping_removed=" + ("yes" if removed else "already_absent"))
print(f"backup={backup}")
PY

docker compose up -d jarvis
JARVIS_RECOVERY_NEEDS_START=false
trap - EXIT

for _ in $(seq 1 40); do
  health="$(
    docker inspect --format '{{.State.Health.Status}}' jarvis 2>/dev/null \
      || true
  )"
  if [[ "$health" == "healthy" ]]; then
    echo "health=healthy"
    echo "codex_session_recovery=complete"
    exit 0
  fi
  sleep 3
done

echo "Jarvis did not become healthy within 120 seconds." >&2
docker compose ps >&2
exit 1
