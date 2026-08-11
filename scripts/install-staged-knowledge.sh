#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

STAMP="${1:-}"
if [[ ! "$STAMP" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]; then
  echo "Usage: install-staged-knowledge.sh YYYYMMDDTHHMMSSZ" >&2
  exit 2
fi

STAGING_BASE="${PROJECT_DIR}/runtime/import-staging"
STAGING_ROOT="${STAGING_BASE}/${STAMP}"
ARCHIVE_ROOT="${STAGING_ROOT}/archive"
IMPORT_ROOT="${STAGING_ROOT}/imports"

run_as_root() {
  if (( EUID == 0 )); then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "sudo is required to grant the hardened container read access." >&2
    return 1
  fi
}

trap 'rc=$?; echo "Staged import failed at line $LINENO (exit $rc)." >&2; exit "$rc"' ERR

test -d "$ARCHIVE_ROOT"
test -d "$IMPORT_ROOT"
test -f "$ARCHIVE_ROOT/SHA256SUMS"

echo "Verifying host staging checkpoint: $STAMP"
(
  cd "$ARCHIVE_ROOT"
  sha256sum --quiet -c SHA256SUMS
)

IMPORT_CHECKSUMS="$STAGING_ROOT/IMPORT_SHA256SUMS"
(
  cd "$IMPORT_ROOT"
  find . -type f -print0 | sort -z | xargs -0 -r sha256sum
) > "$IMPORT_CHECKSUMS"

# The mount remains read-only. Access is limited to the invoking user and the
# root group used by the capability-dropped container process.
run_as_root chgrp 0 "$STAGING_BASE"
run_as_root chmod u=rwx,g=rx,o= "$STAGING_BASE"
run_as_root chgrp -R 0 "$STAGING_ROOT"
run_as_root chmod -R u=rwX,g=rX,o= "$STAGING_ROOT"

if [[ "$(docker inspect --format '{{.State.Running}}' jarvis 2>/dev/null || true)" != "true" ]]; then
  echo "Jarvis container is not running." >&2
  exit 1
fi

echo "Installing staged knowledge into Jarvis..."
docker compose exec -T -e JARVIS_IMPORT_STAMP="$STAMP" jarvis sh -eu -c '
  stamp="$JARVIS_IMPORT_STAMP"
  source="/app/import-staging/$stamp"
  workspace="/app/working/workspaces/default"
  knowledge="$workspace/knowledge"
  archive_dest="$knowledge/archive/$stamp"
  imports_dest="$knowledge/imports/$stamp"
  archive_tmp="$knowledge/archive/.install-$stamp"
  imports_tmp="$knowledge/imports/.install-$stamp"

  test -d "$source/archive"
  test -d "$source/imports"
  test -f "$source/archive/SHA256SUMS"
  test -f "$source/IMPORT_SHA256SUMS"

  mkdir -p "$knowledge/archive" "$knowledge/imports" "$workspace/memory/inbox"
  test ! -e "$archive_dest"
  test ! -e "$imports_dest"
  test ! -e "$archive_tmp"
  test ! -e "$imports_tmp"
  mkdir "$archive_tmp" "$imports_tmp"

  cp -R "$source/archive/." "$archive_tmp/"
  cp -R "$source/imports/." "$imports_tmp/"

  (
    cd "$archive_tmp"
    sha256sum --quiet -c SHA256SUMS
  )
  (
    cd "$imports_tmp"
    sha256sum --quiet -c "$source/IMPORT_SHA256SUMS"
  )

  mv "$archive_tmp" "$archive_dest"
  mv "$imports_tmp" "$imports_dest"
  printf "%s\n" "$stamp" > "$knowledge/.LATEST_IMPORT.tmp"
  mv "$knowledge/.LATEST_IMPORT.tmp" "$knowledge/LATEST_IMPORT"
'

echo "Installed staged knowledge: $STAMP"

