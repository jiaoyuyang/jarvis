#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

TEMPLATE_ID="${1:-${JARVIS_DINGTALK_CARD_TEMPLATE_ID:-}}"
AGENT_ID="${JARVIS_AGENT_ID:-default}"
TARGET="/app/working/workspaces/${AGENT_ID}/agent.json"

if [[ -z "$TEMPLATE_ID" ]]; then
  echo "Usage: $0 CARD_TEMPLATE_ID" >&2
  exit 2
fi

if [[ ! "$TEMPLATE_ID" =~ ^[A-Za-z0-9._-]+\.schema$ ]]; then
  echo "Invalid DingTalk card template ID: expected a value ending in .schema" >&2
  exit 2
fi

docker compose exec -T \
  -e JARVIS_AGENT_CONFIG="$TARGET" \
  -e JARVIS_DINGTALK_CARD_TEMPLATE_ID="$TEMPLATE_ID" \
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

robot_code = str(dingtalk.get("robot_code") or dingtalk.get("client_id") or "")
if not robot_code:
    raise SystemExit(
        "DingTalk robot code is missing; configure the DingTalk channel first"
    )

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
shutil.copy2(path, path.with_name(f"agent.json.before-dingtalk-card-{stamp}"))

dingtalk["message_type"] = "card"
dingtalk["cron_message_type"] = "card"
dingtalk["card_template_id"] = os.environ[
    "JARVIS_DINGTALK_CARD_TEMPLATE_ID"
]
dingtalk["card_template_key"] = "content"
dingtalk["robot_code"] = robot_code
dingtalk["card_auto_layout"] = False
dingtalk["streaming_enabled"] = False
dingtalk["show_thinking"] = False
dingtalk["show_tool_calls"] = False
dingtalk["show_tool_results"] = False

tmp = path.with_suffix(".tmp")
tmp.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
tmp.replace(path)
print("dingtalk_card=enabled")
print("card_template_key=content")
print("card_streaming=false")
PY

docker compose restart jarvis

for _ in $(seq 1 40); do
  health="$(docker inspect --format '{{.State.Health.Status}}' jarvis 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then
    echo "health=healthy"
    echo "DingTalk AI Card is active for agent ${AGENT_ID}."
    exit 0
  fi
  sleep 3
done

echo "Jarvis did not become healthy within 120 seconds." >&2
docker compose ps >&2
exit 1
