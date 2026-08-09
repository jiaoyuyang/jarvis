#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

# shellcheck source=_codex-runtime.sh
source "$SCRIPT_DIR/_codex-runtime.sh"
CODEX_BIN="$(resolve_codex_in_container)"

if ! docker compose exec -T jarvis "$CODEX_BIN" login status >/dev/null; then
  echo "Codex is not authenticated. Run ./scripts/codex-login.sh first." >&2
  exit 1
fi

MODEL="${JARVIS_CODEX_MODEL:-}"
REASONING="${JARVIS_CODEX_REASONING:-}"
if [[ -f .env ]]; then
  ENV_MODEL="$(sed -n 's/^JARVIS_CODEX_MODEL=//p' .env | tail -1)"
  ENV_REASONING="$(sed -n 's/^JARVIS_CODEX_REASONING=//p' .env | tail -1)"
  MODEL="${MODEL:-$ENV_MODEL}"
  REASONING="${REASONING:-$ENV_REASONING}"
fi

TARGET="/app/working/workspaces/default/agent.json"
docker compose exec -T \
  -e JARVIS_AGENT_CONFIG="$TARGET" \
  -e JARVIS_CODEX_MODEL="$MODEL" \
  -e JARVIS_CODEX_REASONING="$REASONING" \
  jarvis python - <<'PY'
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["JARVIS_AGENT_CONFIG"])
if not path.is_file():
    raise SystemExit(f"agent config not found: {path}")

stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
shutil.copy2(path, path.with_name(f"agent.json.before-codex-{stamp}"))

data = json.loads(path.read_text(encoding="utf-8"))
data["backend"] = "codex"
settings = data.setdefault("backend_settings", {})
settings["sandbox"] = "workspace-write"
settings["approval_policy"] = "on-request"
settings["reasoning_summary"] = "auto"

model = os.environ.get("JARVIS_CODEX_MODEL", "").strip()
reasoning = os.environ.get("JARVIS_CODEX_REASONING", "").strip()
if model:
    settings["model"] = model
else:
    settings.pop("model", None)
if reasoning:
    settings["reasoning_effort"] = reasoning
else:
    settings.pop("reasoning_effort", None)

path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

docker compose restart jarvis >/dev/null
echo "Jarvis now uses the Codex backend with ChatGPT subscription authentication."
echo "Run ./scripts/codex-status.sh to verify the active backend."
