#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

AGENT_ID="${JARVIS_AGENT_ID:-default}"
TARGET="/app/working/workspaces/${AGENT_ID}/agent.json"

docker compose exec -T -e JARVIS_AGENT_CONFIG="$TARGET" jarvis python - <<'PY'
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["JARVIS_AGENT_CONFIG"])
if not path.is_file():
    raise SystemExit(f"agent config not found: {path}")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
shutil.copy2(path, path.with_name(f"agent.json.before-jarvis-{stamp}"))

data = json.loads(path.read_text(encoding="utf-8"))
security = data.setdefault("security", {})
security["sandbox_enabled"] = True
security.setdefault("tool_guard", {})["enabled"] = True
security.setdefault("file_guard", {})["enabled"] = True
security.setdefault("skill_scanner", {})["mode"] = "block"
security["allow_no_auth_hosts"] = ["127.0.0.1", "::1"]

# Do not expose model reasoning or verbose tool traces through DingTalk.
channels = data.get("channels")
if not isinstance(channels, dict):
    channels = {}
    data["channels"] = channels
dingtalk = channels.get("dingtalk")
if not isinstance(dingtalk, dict):
    dingtalk = {}
    channels["dingtalk"] = dingtalk
dingtalk["show_thinking"] = False
dingtalk["show_tool_calls"] = False
dingtalk["show_tool_results"] = False

path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

echo "Hardened agent ${AGENT_ID} and hid DingTalk reasoning/tool traces."
