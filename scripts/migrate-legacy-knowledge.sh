#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

LEGACY_APP="/opt/codex-dingtalk"
LEGACY_WORKSPACE="/opt/codex-workspace"
DEST_WORKSPACE="${PROJECT_DIR}/runtime/data/workspaces/default"
DRY_RUN=false

usage() {
  cat <<'EOF'
Usage: migrate-legacy-knowledge.sh [options]

Options:
  --legacy-app PATH        Old codex-dingtalk root (default: /opt/codex-dingtalk)
  --legacy-workspace PATH  Old codex-workspace root (default: /opt/codex-workspace)
  --dest-workspace PATH    Jarvis default workspace on the host
  --dry-run                Show the resolved sources without copying
  -h, --help               Show this help

The script never edits or deletes the legacy sources. It excludes credentials,
virtual environments, Git metadata, caches, logs and old backups from the import.
It is a knowledge migration, not a replacement for a full encrypted server backup.
EOF
}

while (($#)); do
  case "$1" in
    --legacy-app)
      LEGACY_APP="${2:?missing path after --legacy-app}"
      shift 2
      ;;
    --legacy-workspace)
      LEGACY_WORKSPACE="${2:?missing path after --legacy-workspace}"
      shift 2
      ;;
    --dest-workspace)
      DEST_WORKSPACE="${2:?missing path after --dest-workspace}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

LEGACY_APP="$(realpath -m "$LEGACY_APP")"
LEGACY_WORKSPACE="$(realpath -m "$LEGACY_WORKSPACE")"
DEST_WORKSPACE="$(realpath -m "$DEST_WORKSPACE")"

if [[ ! -d "$LEGACY_APP" && ! -d "$LEGACY_WORKSPACE" ]]; then
  echo "Neither legacy source exists:" >&2
  echo "  $LEGACY_APP" >&2
  echo "  $LEGACY_WORKSPACE" >&2
  exit 1
fi

case "$DEST_WORKSPACE" in
  "$LEGACY_APP"|"$LEGACY_APP"/*|"$LEGACY_WORKSPACE"|"$LEGACY_WORKSPACE"/*)
    echo "Destination must not be inside a legacy source: $DEST_WORKSPACE" >&2
    exit 1
    ;;
esac

echo "Legacy application: $LEGACY_APP"
echo "Legacy workspace:   $LEGACY_WORKSPACE"
echo "Jarvis workspace:   $DEST_WORKSPACE"
if [[ "$DRY_RUN" == true ]]; then
  exit 0
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEFAULT_DEST="$(realpath -m "${PROJECT_DIR}/runtime/data/workspaces/default")"
USE_CONTAINER_COPY=false
if [[ "$DEST_WORKSPACE" == "$DEFAULT_DEST" ]] \
  && [[ "$(docker inspect --format '{{.State.Running}}' jarvis 2>/dev/null || true)" == "true" ]]; then
  USE_CONTAINER_COPY=true
  STAGING_ROOT="${PROJECT_DIR}/runtime/import-staging/$STAMP"
  ARCHIVE_ROOT="$STAGING_ROOT/archive"
  IMPORT_ROOT="$STAGING_ROOT/imports"
else
  KNOWLEDGE_ROOT="$DEST_WORKSPACE/knowledge"
  ARCHIVE_ROOT="$KNOWLEDGE_ROOT/archive/$STAMP"
  IMPORT_ROOT="$KNOWLEDGE_ROOT/imports/$STAMP"
fi

mkdir -p "$ARCHIVE_ROOT" "$IMPORT_ROOT"
if [[ "$USE_CONTAINER_COPY" == false ]]; then
  mkdir -p "$DEST_WORKSPACE/memory/inbox"
fi

copy_tree_filtered() {
  local source="$1"
  local destination="$2"
  [[ -d "$source" ]] || return 0
  mkdir -p "$destination"
  tar \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='*.pem' \
    --exclude='*.key' \
    --exclude='id_rsa*' \
    --exclude='id_ed25519*' \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='__pycache__' \
    --exclude='.cache' \
    --exclude='.codex' \
    --exclude='.codex-runtime' \
    --exclude='download_tokens.json' \
    --exclude='credentials.json' \
    --exclude='secrets.json' \
    --exclude='backups' \
    --exclude='logs' \
    -C "$source" -cf - . | tar -C "$destination" -xf -
}

copy_dir_if_present() {
  local source="$1"
  local destination="$2"
  [[ -d "$source" ]] || return 0
  mkdir -p "$destination"
  cp -a "$source"/. "$destination"/
}

if [[ -d "$LEGACY_APP" ]]; then
  APP_ARCHIVE="$ARCHIVE_ROOT/codex-dingtalk"
  mkdir -p "$APP_ARCHIVE"
  copy_tree_filtered "$LEGACY_APP/memory" "$APP_ARCHIVE/memory"
  copy_tree_filtered "$LEGACY_APP/data" "$APP_ARCHIVE/data"

  copy_dir_if_present "$LEGACY_APP/memory/user" "$IMPORT_ROOT/profile"
  copy_dir_if_present "$LEGACY_APP/memory/pingan" "$IMPORT_ROOT/enterprise"
  copy_dir_if_present "$LEGACY_APP/memory/projects" "$IMPORT_ROOT/projects"
  copy_dir_if_present "$LEGACY_APP/memory/standards" "$IMPORT_ROOT/standards"
  copy_dir_if_present "$LEGACY_APP/memory/history" "$IMPORT_ROOT/decisions"
fi

if [[ -d "$LEGACY_WORKSPACE" ]]; then
  copy_tree_filtered "$LEGACY_WORKSPACE" "$ARCHIVE_ROOT/codex-workspace"
fi

MANIFEST="$ARCHIVE_ROOT/IMPORT_MANIFEST.md"
CHECKSUMS="$ARCHIVE_ROOT/SHA256SUMS"
FILE_COUNT="$(find "$ARCHIVE_ROOT" -type f | wc -l | tr -d ' ')"
BYTE_COUNT="$(du -sb "$ARCHIVE_ROOT" | awk '{print $1}')"

{
  echo "# Jarvis legacy knowledge import"
  echo
  echo "- Imported at (UTC): $STAMP"
  echo "- Legacy application: $LEGACY_APP"
  echo "- Legacy workspace: $LEGACY_WORKSPACE"
  echo "- Archive file count before manifest: $FILE_COUNT"
  echo "- Archive bytes before manifest: $BYTE_COUNT"
  echo "- Curated import directory: knowledge/imports/$STAMP"
  echo
  echo "Credentials, key files, Git metadata, virtual environments, caches, logs"
  echo "and old backups were intentionally excluded. Preserve a separate encrypted"
  echo "full-server backup until migration acceptance and recovery testing pass."
} > "$MANIFEST"

(
  cd "$ARCHIVE_ROOT"
  find . -type f ! -name SHA256SUMS -print0 \
    | sort -z \
    | xargs -0 sha256sum
) > "$CHECKSUMS"

if [[ "$USE_CONTAINER_COPY" == true ]]; then
  docker compose exec -T -e JARVIS_IMPORT_STAMP="$STAMP" jarvis sh -eu -c '
    stamp="$JARVIS_IMPORT_STAMP"
    source="/app/import-staging/$stamp"
    workspace="/app/working/workspaces/default"
    knowledge="$workspace/knowledge"
    test -d "$source/archive"
    test -d "$source/imports"
    mkdir -p "$knowledge/archive/$stamp" "$knowledge/imports/$stamp" \
      "$workspace/memory/inbox"
    cp -a "$source/archive/." "$knowledge/archive/$stamp/"
    cp -a "$source/imports/." "$knowledge/imports/$stamp/"
    printf "%s\n" "$stamp" > "$knowledge/LATEST_IMPORT"
  '
else
  printf '%s\n' "$STAMP" > "$KNOWLEDGE_ROOT/LATEST_IMPORT"
fi

FINAL_COUNT="$(find "$ARCHIVE_ROOT" -type f | wc -l | tr -d ' ')"
CURATED_COUNT="$(find "$IMPORT_ROOT" -type f | wc -l | tr -d ' ')"

echo "Imported archive files: $FINAL_COUNT"
echo "Curated knowledge files: $CURATED_COUNT"
if [[ "$USE_CONTAINER_COPY" == true ]]; then
  echo "Manifest: $DEST_WORKSPACE/knowledge/archive/$STAMP/IMPORT_MANIFEST.md"
  echo "Checksums: $DEST_WORKSPACE/knowledge/archive/$STAMP/SHA256SUMS"
  echo "Staging checkpoint: $STAGING_ROOT"
else
  echo "Manifest: $MANIFEST"
  echo "Checksums: $CHECKSUMS"
fi
echo
echo "No legacy source was modified or deleted."
echo "Re-apply the Jarvis persona/skill after migration if needed:"
echo "  ./scripts/apply-persona.sh"
