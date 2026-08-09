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

run_as_root() {
  if (( EUID == 0 )); then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "sudo is required to configure the Docker daemon proxy." >&2
    return 1
  fi
}

configure_docker_daemon_proxy() {
  local proxy_dir=/etc/systemd/system/docker.service.d
  local proxy_file="${proxy_dir}/jarvis-proxy.conf"
  local temp_file
  local changed=false

  if ! command -v systemctl >/dev/null 2>&1 || \
     ! systemctl cat docker.service >/dev/null 2>&1; then
    echo "docker.service was not found; cannot configure registry downloads." >&2
    return 1
  fi

  temp_file="$(mktemp)"
  printf '%s\n' \
    '[Service]' \
    'Environment="HTTP_PROXY=http://127.0.0.1:7890"' \
    'Environment="HTTPS_PROXY=http://127.0.0.1:7890"' \
    'Environment="NO_PROXY=127.0.0.1,localhost,::1,.dingtalk.com"' \
    > "$temp_file"

  if [[ ! -f "$proxy_file" ]] || ! cmp -s "$temp_file" "$proxy_file"; then
    run_as_root install -d -m 0755 "$proxy_dir"
    run_as_root install -m 0644 "$temp_file" "$proxy_file"
    changed=true
  fi
  rm -f "$temp_file"

  if [[ "$changed" == true ]]; then
    echo "Restarting Docker once so registry downloads inherit Mihomo..."
    run_as_root systemctl daemon-reload
    run_as_root systemctl restart docker
  fi

  if ! docker info >/dev/null 2>&1; then
    echo "Docker did not become ready after proxy configuration." >&2
    return 1
  fi

  echo "Docker daemon proxy is configured: $proxy_file"
}

upsert_env QWENPAW_IMAGE \
  docker.io/agentscope/qwenpaw:v2.1.0-beta.2
upsert_env JARVIS_HTTP_PROXY http://127.0.0.1:7890
upsert_env JARVIS_HTTPS_PROXY http://127.0.0.1:7890
upsert_env JARVIS_ALL_PROXY socks5h://127.0.0.1:7890
upsert_env JARVIS_NO_PROXY \
  127.0.0.1,localhost,::1,.dingtalk.com

chmod 600 .env
configure_docker_daemon_proxy

echo "Configured Jarvis and Docker registry downloads to use existing Mihomo."
echo "Backup: ${PROJECT_DIR}/.env.before-proxy-${STAMP}"
