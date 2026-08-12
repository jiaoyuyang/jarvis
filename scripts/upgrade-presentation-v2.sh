#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

echo "Creating a recoverable Jarvis backup..."
./scripts/backup.sh

echo "Installing presentation rules into the active workspace..."
./scripts/apply-persona.sh
./scripts/harden-security.sh
docker compose restart jarvis

for _ in $(seq 1 40); do
  health="$(docker inspect --format '{{.State.Health.Status}}' jarvis 2>/dev/null || true)"
  if [[ "$health" == "healthy" ]]; then
    ./scripts/workflow-status.sh
    ./scripts/codex-status.sh
    echo "presentation_v2=active"
    echo "Start a new DingTalk session with /new before acceptance testing."
    exit 0
  fi
  sleep 3
done

echo "Jarvis did not become healthy within 120 seconds." >&2
docker compose ps >&2
exit 1
