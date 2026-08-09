#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

mkdir -p runtime/backups/manual
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
ARCHIVE="runtime/backups/manual/jarvis-${STAMP}.tar.gz"

WAS_RUNNING="$(docker inspect --format '{{.State.Running}}' jarvis 2>/dev/null || true)"
restart_if_needed() {
  if [[ "$WAS_RUNNING" == "true" ]]; then
    docker compose start jarvis >/dev/null
  fi
}
trap restart_if_needed EXIT

if [[ "$WAS_RUNNING" == "true" ]]; then
  docker compose stop jarvis >/dev/null
fi

tar --exclude='backups/manual' -czf "$ARCHIVE" -C runtime data secrets backups
sha256sum "$ARCHIVE" > "${ARCHIVE}.sha256"
chmod 600 "$ARCHIVE" "${ARCHIVE}.sha256"

restart_if_needed
trap - EXIT

echo "Created ${PROJECT_DIR}/${ARCHIVE}"
echo "Created ${PROJECT_DIR}/${ARCHIVE}.sha256"
