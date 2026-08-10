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
  mkdir -p "$target/knowledge/projects" "$target/knowledge/intake" \
    "$target/memory/inbox" "$target/skills"
  for source in /opt/jarvis/skills/jarvis-*; do
    test -d "$source" || continue
    skill_name=${source##*/}
    if test -d "$target/skills/$skill_name"; then
      backup_root="$target/backups/persona/$stamp"
      mkdir -p "$backup_root"
      cp -R "$target/skills/$skill_name" "$backup_root/$skill_name"
    fi
    rm -rf "$target/skills/$skill_name"
    cp -R "$source" "$target/skills/$skill_name"
  done
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
skill_names = sorted(
    path.name
    for path in (workspace / "skills").glob("jarvis-*")
    if path.is_dir()
)
if not skill_names:
    raise SystemExit("no Jarvis skills were installed")

service = SkillService(workspace)
for skill_name in skill_names:
    result = service.enable_skill(skill_name)
    if not result.get("success"):
        raise SystemExit(f"failed to enable {skill_name}: {result}")

manifest = read_skill_manifest(workspace).get("skills", {})
disabled = [name for name in skill_names if not manifest.get(name, {}).get("enabled", False)]
if disabled:
    raise SystemExit(f"Jarvis skills are still disabled: {disabled}")
print("Enabled workspace skills: " + ", ".join(skill_names))
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

echo "Applied Jarvis persona, continuous memory, intake, writing and project skills for agent ${AGENT_ID}."
echo "Start a new channel session with /new so Codex inherits updated Skills."
