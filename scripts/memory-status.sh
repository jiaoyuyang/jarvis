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

docker compose exec -T -e JARVIS_MEMORY_WORKSPACE="$WORKSPACE" jarvis sh -eu -c '
  workspace="$JARVIS_MEMORY_WORKSPACE"
  tool="$workspace/skills/jarvis-memory/scripts/memoryctl.py"
  test -f "$tool"
  python "$tool" --workspace "$workspace" verify --rebuild
  python "$tool" --workspace "$workspace" status
  echo "pending_candidates:"
  python "$tool" --workspace "$workspace" list --status pending --limit 20
'
