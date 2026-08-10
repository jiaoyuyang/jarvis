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

run_as_root sha256sum --quiet -c "${ARCHIVE}.sha256"

RETENTION="${JARVIS_BACKUP_RETENTION:-}"
if [[ -z "$RETENTION" && -f .env ]]; then
  RETENTION_LINE="$(grep -E '^JARVIS_BACKUP_RETENTION=' .env | tail -1 || true)"
  RETENTION="${RETENTION_LINE#JARVIS_BACKUP_RETENTION=}"
fi
RETENTION="${RETENTION:-3}"
if [[ ! "$RETENTION" =~ ^[0-9]+$ ]] || (( RETENTION < 1 || RETENTION > 30 )); then
  echo "JARVIS_BACKUP_RETENTION must be an integer between 1 and 30." >&2
  exit 1
fi

mapfile -t MANUAL_ARCHIVES < <(
  find runtime/backups/manual -maxdepth 1 -type f \
    -name 'jarvis-*.tar.gz' -printf '%f\n' | sort -r
)
for (( index=RETENTION; index<${#MANUAL_ARCHIVES[@]}; index++ )); do
  OLD_ARCHIVE="runtime/backups/manual/${MANUAL_ARCHIVES[$index]}"
  run_as_root rm -f -- "$OLD_ARCHIVE" "${OLD_ARCHIVE}.sha256"
  echo "Removed expired backup ${PROJECT_DIR}/${OLD_ARCHIVE}"
done

restart_if_needed
trap - EXIT

echo "Created ${PROJECT_DIR}/${ARCHIVE}"
echo "Created ${PROJECT_DIR}/${ARCHIVE}.sha256"
