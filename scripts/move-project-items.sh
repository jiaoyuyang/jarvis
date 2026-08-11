#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

if (($# < 4)); then
  echo "Usage: $0 SOURCE_PROJECT TARGET_PROJECT TARGET_NAME ITEM_ID..." >&2
  exit 2
fi

SOURCE_PROJECT="$1"
TARGET_PROJECT="$2"
TARGET_NAME="$3"
shift 3

if [[ ! "$SOURCE_PROJECT" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] ||
  [[ ! "$TARGET_PROJECT" =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]]; then
  echo "Project keys must use lowercase letters, numbers and hyphens." >&2
  exit 2
fi

AGENT_ID="${JARVIS_AGENT_ID:-default}"
WORKSPACE="/app/working/workspaces/${AGENT_ID}"
TOOL="$WORKSPACE/skills/jarvis-project/scripts/projectctl.py"

for item_id in "$@"; do
  if [[ ! "$item_id" =~ ^item-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$ ]]; then
    echo "Invalid item id: $item_id" >&2
    exit 2
  fi
  docker compose exec -T jarvis python "$TOOL" \
    --workspace "$WORKSPACE" move "$item_id" \
    --project "$SOURCE_PROJECT" \
    --to-project "$TARGET_PROJECT" \
    --to-name "$TARGET_NAME" \
    --reason "Confirmed project routing correction"
done

echo "Moved project items without deleting source history."
