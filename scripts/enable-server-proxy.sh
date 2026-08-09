#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -f .env ]]; then
  echo ".env not found. Run ./scripts/install.sh once or copy .env.example first." >&2
  exit 1
fi

"$SCRIPT_DIR/check-proxy.sh"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
cp .env ".env.before-proxy-${STAMP}"
chmod 600 ".env.before-proxy-${STAMP}"

upsert_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

upsert_env QWENPAW_IMAGE \
  agentscope-registry.ap-southeast-1.cr.aliyuncs.com/agentscope/qwenpaw:v2.1.0-beta.2
upsert_env JARVIS_HTTP_PROXY http://127.0.0.1:7890
upsert_env JARVIS_HTTPS_PROXY http://127.0.0.1:7890
upsert_env JARVIS_ALL_PROXY socks5h://127.0.0.1:7890
upsert_env JARVIS_NO_PROXY \
  127.0.0.1,localhost,.dingtalk.com,.aliyuncs.com

chmod 600 .env
echo "Configured Jarvis build/runtime to use the existing Mihomo proxy."
echo "Backup: ${PROJECT_DIR}/.env.before-proxy-${STAMP}"
