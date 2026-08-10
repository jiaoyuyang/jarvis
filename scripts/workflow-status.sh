#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

AGENT_ID="${JARVIS_AGENT_ID:-default}"
WORKSPACE="/app/working/workspaces/${AGENT_ID}"

if [[ "$(docker inspect --format '{{.State.Running}}' jarvis 2>/dev/null || true)" != "true" ]]; then
  echo "Jarvis container is not running." >&2
  exit 1
fi

docker compose exec -T -e JARVIS_WORKSPACE="$WORKSPACE" jarvis sh -eu -c '
  workspace="$JARVIS_WORKSPACE"
  for skill in jarvis-memory jarvis-intake jarvis-management-writing jarvis-project; do
    test -f "$workspace/skills/$skill/SKILL.md"
    echo "$skill=installed"
  done
  python "$workspace/skills/jarvis-memory/scripts/memoryctl.py" \
    --workspace "$workspace" verify --rebuild
  python "$workspace/skills/jarvis-intake/scripts/intakectl.py" \
    --workspace "$workspace" verify
  for meta in "$workspace"/knowledge/projects/*/meta.json; do
    test -f "$meta" || continue
    project_dir=${meta%/meta.json}
    project=${project_dir##*/}
    python "$workspace/skills/jarvis-project/scripts/projectctl.py" \
      --workspace "$workspace" verify --project "$project" --rebuild
  done
  echo "workspace_workflows=verified"
'

docker compose exec -T -e JARVIS_SKILL_WORKSPACE="$WORKSPACE" jarvis python - <<'PY'
import os
from pathlib import Path

from qwenpaw.agents.skill_system import read_skill_manifest

workspace = Path(os.environ["JARVIS_SKILL_WORKSPACE"])
required = {
    "jarvis-memory",
    "jarvis-intake",
    "jarvis-management-writing",
    "jarvis-project",
}
manifest = read_skill_manifest(workspace).get("skills", {})
disabled = sorted(
    name for name in required if not manifest.get(name, {}).get("enabled", False)
)
if disabled:
    raise SystemExit(f"disabled Jarvis skills: {disabled}")
print("enabled_skills=" + ",".join(sorted(required)))
PY
