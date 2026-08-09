#!/usr/bin/env bash

resolve_codex_in_container() {
  docker compose exec -T jarvis python - <<'PY'
from qwenpaw.harnesses.codex.discovery import resolve_codex_binary

path = resolve_codex_binary()
if path is None:
    raise SystemExit("Codex runtime not found in the Jarvis container")
print(path)
PY
}
