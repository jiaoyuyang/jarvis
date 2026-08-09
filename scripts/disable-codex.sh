#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

TARGET="/app/working/workspaces/default/agent.json"
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
shutil.copy2(path, path.with_name(f"agent.json.before-qwenpaw-{stamp}"))
data = json.loads(path.read_text(encoding="utf-8"))
data["backend"] = "qwenpaw"
path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

docker compose restart jarvis >/dev/null
echo "Jarvis has been switched back to the native QwenPaw backend."
