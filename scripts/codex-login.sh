#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

# shellcheck source=_codex-runtime.sh
source "$SCRIPT_DIR/_codex-runtime.sh"
CODEX_BIN="$(resolve_codex_in_container)"

echo "Starting ChatGPT device login inside the Jarvis container."
echo "Open the URL shown below and enter the one-time code."
docker compose exec jarvis "$CODEX_BIN" login --device-auth

echo
echo "Login completed. Verify it with: ./scripts/codex-status.sh"
echo "Then switch Jarvis to Codex with: ./scripts/enable-codex.sh"
