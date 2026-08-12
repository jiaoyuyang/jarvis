#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

AGENT_ID="${JARVIS_AGENT_ID:-default}"
TARGET="/app/working/workspaces/${AGENT_ID}/agent.json"

docker compose exec -T \
  -e JARVIS_AGENT_CONFIG="$TARGET" \
  jarvis python - <<'PY'
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["JARVIS_AGENT_CONFIG"])
if not path.is_file():
    raise SystemExit(f"agent config not found: {path}")

data = json.loads(path.read_text(encoding="utf-8"))
channels = data.setdefault("channels", {})
dingtalk = channels.setdefault("dingtalk", {})
if not isinstance(dingtalk, dict):
    raise SystemExit("channels.dingtalk is not an object")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
shutil.copy2(path, path.with_name(f"agent.json.before-dingtalk-markdown-{stamp}"))

dingtalk["message_type"] = "markdown"
dingtalk["cron_message_type"] = "markdown"
dingtalk["streaming_enabled"] = False

tmp = path.with_suffix(".tmp")
tmp.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
tmp.replace(path)
print("dingtalk_card=disabled")
print("message_type=markdown")
PY

docker compose restart jarvis

for _ in $(seq 1 40); do
  health="$(docker inspect --format '{{.State.Health.Status}}' jarvis 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then
    echo "health=healthy"
    echo "DingTalk Markdown fallback is active for agent ${AGENT_ID}."
    exit 0
  fi
  sleep 3
done

echo "Jarvis did not become healthy within 120 seconds." >&2
docker compose ps >&2
exit 1
