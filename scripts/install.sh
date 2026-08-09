#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker Engine and the Compose plugin first." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not available." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "The current user cannot access Docker. Add the user to the docker group or run this script with appropriate privileges." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  if command -v openssl >/dev/null 2>&1; then
    GENERATED_PASSWORD="$(openssl rand -hex 20)"
  else
    GENERATED_PASSWORD="$(od -An -N20 -tx1 /dev/urandom | tr -d ' \n')"
  fi

  sed "s/^QWENPAW_AUTH_PASSWORD=.*/QWENPAW_AUTH_PASSWORD=${GENERATED_PASSWORD}/" .env.example > .env
  chmod 600 .env
  echo "Created .env with a random Web password."
fi

if grep -q '^QWENPAW_AUTH_PASSWORD=CHANGE_ME$' .env; then
  echo "Refusing to start with the example password. Update QWENPAW_AUTH_PASSWORD in .env." >&2
  exit 1
fi

mkdir -p runtime/data runtime/secrets runtime/backups runtime/codex runtime/import-staging
chmod 700 runtime/secrets runtime/backups runtime/codex runtime/import-staging

docker compose build --pull jarvis
docker compose up -d jarvis

echo "Waiting for Jarvis health check..."
for attempt in $(seq 1 60); do
  HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}starting{{end}}' jarvis 2>/dev/null || true)"
  if [[ "$HEALTH" == "healthy" ]]; then
    break
  fi
  if [[ "$attempt" == "60" ]]; then
    docker compose logs --tail=200 jarvis
    echo "Jarvis did not become healthy in time." >&2
    exit 1
  fi
  sleep 3
done

"$SCRIPT_DIR/apply-persona.sh"
"$SCRIPT_DIR/harden-security.sh"
docker compose restart jarvis >/dev/null

PORT="$(sed -n 's/^JARVIS_PORT=//p' .env | tail -1)"
PORT="${PORT:-8088}"

echo
echo "Jarvis is running."
echo "Console on server: http://127.0.0.1:${PORT}"
echo "Use an SSH tunnel from your computer:"
echo "  ssh -L ${PORT}:127.0.0.1:${PORT} ubuntu@YOUR_SERVER_IP"
echo "Login values are stored in ${PROJECT_DIR}/.env"
echo
echo "Codex is installed but is not enabled until ChatGPT OAuth succeeds."
echo "Next steps:"
echo "  ./scripts/codex-login.sh"
echo "  ./scripts/enable-codex.sh"
echo "  ./scripts/codex-status.sh"
