#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

run_as_root() {
  if (( EUID == 0 )); then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "sudo is required to back up container-owned Jarvis data." >&2
    return 1
  fi
}

CALLER_UID="$(id -u)"
CALLER_GID="$(id -g)"

# Validate privilege before stopping the container. Only the host-managed backup
# directory is assigned to the invoking user; application data ownership stays
# exactly as created by QwenPaw inside the container.
run_as_root true
run_as_root install -d -m 0700 -o "$CALLER_UID" -g "$CALLER_GID" \
  runtime/backups runtime/backups/manual
run_as_root mkdir -p runtime/codex

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

run_as_root tar --exclude='backups/manual' \
  -czf "$ARCHIVE" -C runtime data secrets backups codex
CHECKSUM_LINE="$(run_as_root sha256sum "$ARCHIVE")"
printf '%s\n' "$CHECKSUM_LINE" \
  | run_as_root tee "${ARCHIVE}.sha256" >/dev/null
run_as_root chmod 600 "$ARCHIVE" "${ARCHIVE}.sha256"
run_as_root chown "$CALLER_UID:$CALLER_GID" \
  "$ARCHIVE" "${ARCHIVE}.sha256"

restart_if_needed
trap - EXIT

echo "Created ${PROJECT_DIR}/${ARCHIVE}"
echo "Created ${PROJECT_DIR}/${ARCHIVE}.sha256"
