#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

docker compose ps
docker inspect --format 'health={{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}} image={{.Config.Image}} started={{.State.StartedAt}}' jarvis

