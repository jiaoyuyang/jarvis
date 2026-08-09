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
      cp -R "$target/skills/jarvis-memory" \
        "$target/skills/jarvis-memory.before-jarvis-$stamp"
    fi
    rm -rf "$target/skills/jarvis-memory"
    cp -R /opt/jarvis/skills/jarvis-memory "$target/skills/jarvis-memory"
  fi
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

echo "Applied Jarvis persona to agent ${AGENT_ID}."
