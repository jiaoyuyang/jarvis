#!/bin/sh
set -eu

working_dir="${QWENPAW_WORKING_DIR:-/app/working}"
if [ -f "$working_dir/config.json" ]; then
  /app/venv/bin/python /opt/jarvis/scripts/sync-managed-skills.py
else
  echo "managed skills sync skipped: QwenPaw is not initialized"
fi

exec /entrypoint.sh "$@"
