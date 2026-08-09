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
  for name in AGENTS.md SOUL.md PROFILE.md; do
    if test -f "$target/$name"; then
      cp "$target/$name" "$target/$name.before-jarvis-$stamp"
    fi
    cp "/opt/jarvis/persona/$name" "$target/$name"
  done
'

echo "Applied Jarvis persona to agent ${AGENT_ID}."
