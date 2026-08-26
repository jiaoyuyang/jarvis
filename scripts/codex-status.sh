#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_DIR"

# shellcheck source=_codex-runtime.sh
source "$SCRIPT_DIR/_codex-runtime.sh"
CODEX_BIN="$(resolve_codex_in_container)"

docker compose exec -T jarvis "$CODEX_BIN" --version
docker compose exec -T jarvis "$CODEX_BIN" login status

docker compose exec -T jarvis python - <<'PY'
import json
from pathlib import Path

from qwenpaw.app.channels.dingtalk import channel as dingtalk_channel
from qwenpaw.app.channels import base as channel_base
from qwenpaw.app.channels import renderer as channel_renderer
from qwenpaw.harnesses.codex import adapter as codex_adapter

path = Path("/app/working/workspaces/default/agent.json")
if not path.is_file():
    raise SystemExit(f"Agent config not found: {path}")
data = json.loads(path.read_text(encoding="utf-8"))
settings = data.get("backend_settings") or {}
dingtalk = ((data.get("channels") or {}).get("dingtalk") or {})
print(f"agent={data.get('name', '')}")
print(f"backend={data.get('backend', 'qwenpaw')}")
print(f"sandbox={settings.get('sandbox', '')}")
print(f"approval_policy={settings.get('approval_policy', '')}")
print(f"final_only={str(bool(settings.get('final_only', False))).lower()}")
adapter_source = Path(codex_adapter.__file__).read_text(encoding="utf-8")
patch_installed = "JARVIS_CODEX_FINAL_ONLY_PATCH_V1" in adapter_source
print(f"final_only_patch={'installed' if patch_installed else 'missing'}")
if settings.get("final_only") and not patch_installed:
    raise SystemExit("final_only is enabled but the Codex adapter patch is missing")
timeout_installed = "JARVIS_CODEX_TURN_TIMEOUT_PATCH_V2" in adapter_source
print(
    "turn_timeout_patch="
    + ("installed" if timeout_installed else "missing")
)
if not timeout_installed:
    raise SystemExit("Codex turn timeout patch is missing")
print("interrupted_thread_reset=true")
print(
    "turn_timeout_seconds="
    + str(settings.get("turn_timeout_seconds") or 600)
)
base_source = Path(channel_base.__file__).read_text(encoding="utf-8")
stop_installed = "JARVIS_STOP_COMMAND_PATCH_V1" in base_source
print(
    "native_stop_patch="
    + ("installed" if stop_installed else "missing")
)
if not stop_installed:
    raise SystemExit("native /stop patch is missing")
dingtalk_source = Path(dingtalk_channel.__file__).read_text(encoding="utf-8")
recovery_installed = "JARVIS_DINGTALK_TURN_RECOVERY_PATCH_V1" in dingtalk_source
print(
    "turn_recovery_patch="
    + ("installed" if recovery_installed else "missing")
)
if not recovery_installed:
    raise SystemExit("DingTalk turn recovery patch is missing")
renderer_source = Path(channel_renderer.__file__).read_text(encoding="utf-8")
artifact_renderer_installed = (
    "JARVIS_LOCAL_ARTIFACT_RENDERER_PATCH_V1" in renderer_source
)
media_receipt_installed = (
    "JARVIS_DINGTALK_MEDIA_RECEIPT_PATCH_V2" in dingtalk_source
)
print(
    "artifact_renderer_patch="
    + ("installed" if artifact_renderer_installed else "missing")
)
print(
    "media_receipt_patch="
    + ("installed" if media_receipt_installed else "missing")
)
if not (artifact_renderer_installed and media_receipt_installed):
    raise SystemExit("DingTalk local artifact delivery patch is incomplete")
chart_skill = Path("/opt/jarvis/skills/jarvis-chart/SKILL.md")
chart_renderer = Path(
    "/opt/jarvis/skills/jarvis-chart/scripts/render_chart.py"
)
chart_font = Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
print(f"chart_skill={'installed' if chart_skill.is_file() else 'missing'}")
print(
    "chart_renderer="
    + ("installed" if chart_renderer.is_file() else "missing")
)
print(f"chart_font={'installed' if chart_font.is_file() else 'missing'}")
if not (chart_skill.is_file() and chart_renderer.is_file() and chart_font.is_file()):
    raise SystemExit("Deterministic chart runtime is incomplete")
message_type = dingtalk.get("message_type") or "markdown"
template_configured = bool(dingtalk.get("card_template_id"))
robot_configured = bool(
    dingtalk.get("robot_code") or dingtalk.get("client_id")
)
print(f"dingtalk_message_type={message_type}")
print(
    "dingtalk_card_template="
    + ("configured" if template_configured else "missing")
)
print(f"dingtalk_card_key={dingtalk.get('card_template_key') or 'content'}")
print(
    "dingtalk_robot_code="
    + ("configured" if robot_configured else "missing")
)
print(
    "dingtalk_card_streaming="
    + str(bool(dingtalk.get("streaming_enabled", False))).lower()
)
if message_type == "card" and not (template_configured and robot_configured):
    raise SystemExit("DingTalk card mode is incomplete")
print(f"model={settings.get('model') or 'account default'}")
print(f"reasoning_effort={settings.get('reasoning_effort') or 'account default'}")
PY
