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

from qwenpaw.harnesses.codex import adapter as codex_adapter

path = Path("/app/working/workspaces/default/agent.json")
if not path.is_file():
    raise SystemExit(f"Agent config not found: {path}")
data = json.loads(path.read_text(encoding="utf-8"))
settings = data.get("backend_settings") or {}
print(f"agent={data.get('name', '')}")
print(f"backend={data.get('backend', 'qwenpaw')}")
print(f"sandbox={settings.get('sandbox', '')}")
print(f"approval_policy={settings.get('approval_policy', '')}")\nprint(f"final_only={str(bool(settings.get('final_only', False))).lower()}")
adapter_source = Path(codex_adapter.__file__).read_text(encoding="utf-8")
patch_installed = "JARVIS_CODEX_FINAL_ONLY_PATCH_V1" in adapter_source
print(f"final_only_patch={'installed' if patch_installed else 'missing'}")
if settings.get("final_only") and not patch_installed:
    raise SystemExit("final_only is enabled but the Codex adapter patch is missing")
print(f"model={settings.get('model') or 'account default'}")
print(f"reasoning_effort={settings.get('reasoning_effort') or 'account default'}")
PY
