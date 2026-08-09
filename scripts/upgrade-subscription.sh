#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

if docker inspect jarvis >/dev/null 2>&1; then
  echo "Creating a pre-upgrade backup..."
  "$SCRIPT_DIR/backup.sh"
fi

if [[ -f .env ]]; then
  CURRENT_IMAGE="$(sed -n 's/^QWENPAW_IMAGE=//p' .env | tail -1)"
  case "$CURRENT_IMAGE" in
    *:latest|*:v2.0.1)
      STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
      cp .env ".env.before-codex-${STAMP}"
      chmod 600 ".env.before-codex-${STAMP}"
      IMAGE_REPOSITORY="${CURRENT_IMAGE%:*}"
      sed -i \
        "s|^QWENPAW_IMAGE=.*|QWENPAW_IMAGE=${IMAGE_REPOSITORY}:v2.1.0-beta.2|" \
        .env
      echo "Pinned QwenPaw to ${IMAGE_REPOSITORY}:v2.1.0-beta.2 for Codex backend support."
      ;;
    *:v2.1.0-beta.2)
      ;;
    *)
      echo "Unsupported or unverified QWENPAW_IMAGE for this upgrade: $CURRENT_IMAGE" >&2
      echo "Set it to agentscope/qwenpaw:v2.1.0-beta.2 and retry." >&2
      exit 1
      ;;
  esac
fi

"$SCRIPT_DIR/install.sh"

echo
echo "The upgrade is installed, but the existing backend was not forcibly changed."
echo "Authenticate and enable Codex:"
echo "  ./scripts/codex-login.sh"
echo "  ./scripts/enable-codex.sh"
