#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

AGENT_ID="${JARVIS_AGENT_ID:-QwenPaw_QA_Agent_0.2}"
TARGET="/app/working/workspaces/${AGENT_ID}"

docker compose exec -T jarvis sh -eu -c "
  test -d '${TARGET}'
  stamp=\$(date -u +%Y%m%dT%H%M%SZ)
  for name in AGENTS.md SOUL.md PROFILE.md; do
    if test -f '${TARGET}'/\$name; then
      cp '${TARGET}'/\$name '${TARGET}'/\$name.before-jarvis-\$stamp
    fi
    cp /opt/jarvis/persona/\$name '${TARGET}'/\$name
  done
"

echo "Applied Jarvis persona to agent ${AGENT_ID}."

