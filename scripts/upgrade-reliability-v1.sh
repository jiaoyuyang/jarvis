#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

echo "Creating a recoverable Jarvis backup..."
./scripts/backup.sh

echo "Building reliability patches on the existing local Jarvis image..."
docker compose build \
  --build-arg QWENPAW_IMAGE=jarvis:qwenpaw-2.1-codex \
  jarvis

docker compose up -d --force-recreate jarvis

for _ in $(seq 1 40); do
  health="$(
    docker inspect --format '{{.State.Health.Status}}' jarvis 2>/dev/null \
      || true
  )"
  if [[ "$health" == "healthy" ]]; then
    break
  fi
  sleep 3
done

if [[ "$(docker inspect --format '{{.State.Health.Status}}' jarvis)" != "healthy" ]]; then
  echo "Jarvis did not become healthy within 120 seconds." >&2
  docker compose ps >&2
  exit 1
fi

docker compose exec -T jarvis python - <<'PY'
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

path = Path("/app/working/workspaces/default/agent.json")
data = json.loads(path.read_text(encoding="utf-8"))
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
shutil.copy2(path, path.with_name(f"agent.json.before-reliability-{stamp}"))
settings = data.setdefault("backend_settings", {})
settings["turn_timeout_seconds"] = 600
path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

./scripts/codex-status.sh
echo "reliability_v1=active"
