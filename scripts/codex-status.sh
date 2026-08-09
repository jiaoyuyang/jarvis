#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

# shellcheck source=_codex-runtime.sh
source "$SCRIPT_DIR/_codex-runtime.sh"
CODEX_BIN="$(resolve_codex_in_container)"

docker compose exec -T jarvis "$CODEX_BIN" --version
docker compose exec -T jarvis "$CODEX_BIN" login status

docker compose exec -T jarvis python - <<'PY'
import json
from pathlib import Path

path = Path("/app/working/workspaces/default/agent.json")
if not path.is_file():
    raise SystemExit(f"Agent config not found: {path}")
data = json.loads(path.read_text(encoding="utf-8"))
settings = data.get("backend_settings") or {}
print(f"agent={data.get('name', '')}")
print(f"backend={data.get('backend', 'qwenpaw')}")
print(f"sandbox={settings.get('sandbox', '')}")
print(f"approval_policy={settings.get('approval_policy', '')}")
print(f"model={settings.get('model') or 'account default'}")
print(f"reasoning_effort={settings.get('reasoning_effort') or 'account default'}")
PY
