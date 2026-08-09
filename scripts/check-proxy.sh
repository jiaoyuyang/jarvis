#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

PROXY_URL="${JARVIS_HTTP_PROXY:-http://127.0.0.1:7890}"
if [[ -f .env ]]; then
  ENV_PROXY="$(sed -n 's/^JARVIS_HTTP_PROXY=//p' .env | tail -1)"
  PROXY_URL="${ENV_PROXY:-$PROXY_URL}"
fi

echo "mihomo=$(systemctl is-active mihomo 2>/dev/null || true)"
echo "proxy=$PROXY_URL"

check_url() {
  local name="$1"
  local url="$2"
  local code
  code="$(curl --proxy "$PROXY_URL" --connect-timeout 10 --max-time 20 \
    --silent --show-error --output /dev/null --write-out '%{http_code}' \
    "$url" || true)"
  if [[ -z "$code" || "$code" == "000" ]]; then
    echo "$name=failed" >&2
    return 1
  fi
  echo "$name=http-$code"
}

check_url aliyun-registry https://agentscope-registry.ap-southeast-1.cr.aliyuncs.com/v2/
check_url docker-hub https://registry-1.docker.io/v2/
check_url pypi https://pypi.org/simple/openai-codex/
check_url openai-auth https://auth.openai.com/

echo "Proxy connectivity is available. No Mihomo route or configuration was changed."
