#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

AGENT_ID="${JARVIS_AGENT_ID:-default}"
TARGET="/app/working/workspaces/${AGENT_ID}"

docker compose exec -T -e JARVIS_PERSONA_TARGET="$TARGET" jarvis sh -eu -c '
  target="$JARVIS_PERSONA_TARGET"
  test -d "$target"
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  if test -f "$target/agent.json"; then
    cp "$target/agent.json" "$target/agent.json.before-jarvis-$stamp"
  fi
  for name in AGENTS.md SOUL.md PROFILE.md; do
    if test -f "$target/$name"; then
      cp "$target/$name" "$target/$name.before-jarvis-$stamp"
    fi
    cp "/opt/jarvis/persona/$name" "$target/$name"
  done
  mkdir -p "$target/knowledge" "$target/memory/inbox" "$target/skills"
  if test -d /opt/jarvis/skills/jarvis-memory; then
    if test -d "$target/skills/jarvis-memory"; then
      backup_root="$target/backups/persona/$stamp"
      mkdir -p "$backup_root"
      cp -R "$target/skills/jarvis-memory" "$backup_root/jarvis-memory"
    fi
    rm -rf "$target/skills/jarvis-memory"
    cp -R /opt/jarvis/skills/jarvis-memory "$target/skills/jarvis-memory"
  fi
'

docker compose exec -T -e JARVIS_SKILL_WORKSPACE="$TARGET" jarvis python - <<'PY'
import os
from pathlib import Path

from qwenpaw.agents.skill_system import (
    SkillService,
    read_skill_manifest,
    reconcile_workspace_manifest,
)

workspace = Path(os.environ["JARVIS_SKILL_WORKSPACE"])
reconcile_workspace_manifest(workspace)
result = SkillService(workspace).enable_skill("jarvis-memory")
if not result.get("success"):
    raise SystemExit(f"failed to enable jarvis-memory: {result}")

entry = read_skill_manifest(workspace).get("skills", {}).get(
    "jarvis-memory",
    {},
)
if not entry.get("enabled", False):
    raise SystemExit("jarvis-memory is still disabled after enable request")
print("Enabled workspace skill: jarvis-memory")
PY

docker compose exec -T -e JARVIS_MEMORY_WORKSPACE="$TARGET" jarvis sh -eu -c '
  workspace="$JARVIS_MEMORY_WORKSPACE"
  tool="$workspace/skills/jarvis-memory/scripts/memoryctl.py"
  test -f "$tool"
  python "$tool" --workspace "$workspace" verify --rebuild
'

docker compose exec -T -e JARVIS_AGENT_CONFIG="$TARGET/agent.json" jarvis python - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["JARVIS_AGENT_CONFIG"])
if not path.is_file():
    raise SystemExit(f"agent config not found: {path}")

data = json.loads(path.read_text(encoding="utf-8"))
data["name"] = "Jarvis"
data["description"] = "焦书记的长期个人智能助理、技术搭档和知识管理助手"
path.write_text(
    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY

echo "Applied Jarvis persona and enabled the continuous memory ledger for agent ${AGENT_ID}."
echo "Start a new channel session with /new so Codex inherits updated Skills."
