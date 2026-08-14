#!/usr/bin/env python3
"""Route DingTalk ``/stop`` to QwenPaw's native control handler.

The pinned channel layer detects ``/stop`` as a priority-zero command, but its
control branch still calls the configured Codex harness.  Codex then reports
``Unsupported Codex command: /stop`` and the original TaskTracker entry stays
busy.  This patch dispatches only ``/stop`` directly to the native handler;
other registered commands keep their existing path.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "# JARVIS_STOP_COMMAND_PATCH_V1"

ANCHOR = """            if not is_control:
                request = self._payload_to_request(payload)
                await self._consume_with_tracker(request, payload)
                return

        request = self._payload_to_request(payload)
"""

REPLACEMENT = f"""            if not is_control:
                request = self._payload_to_request(payload)
                await self._consume_with_tracker(request, payload)
                return

            # {MARKER}
            command_token = query_text.strip().lower().split(None, 1)[0]
            if command_token == "/stop":
                from ...runtime.commands.control import (
                    ControlContext,
                    handle_control_command,
                )

                request = self._payload_to_request(payload)
                if isinstance(payload, dict):
                    meta_from_payload = dict(payload.get("meta") or {{}})
                    if payload.get("session_webhook"):
                        meta_from_payload["session_webhook"] = payload[
                            "session_webhook"
                        ]
                    setattr(request, "channel_meta", meta_from_payload)
                    send_meta = dict(meta_from_payload)
                else:
                    send_meta = (
                        getattr(request, "channel_meta", None) or {{}}
                    )
                context = ControlContext(
                    workspace=self._workspace,
                    payload=payload,
                    channel=self,
                    session_id=(
                        getattr(request, "session_id", "") or ""
                    ),
                    user_id=getattr(request, "user_id", "") or "",
                    agent_id=getattr(self._workspace, "agent_id", "") or "",
                    args={{}},
                )
                to_handle = self.get_to_handle_from_request(request)
                try:
                    result = await handle_control_command(
                        query_text,
                        context,
                    )
                    await self.send(to_handle, result, send_meta)
                    await self._on_process_completed(
                        request,
                        to_handle,
                        send_meta,
                    )
                    if self._on_reply_sent:
                        callback_args = self.get_on_reply_sent_args(
                            request,
                            to_handle,
                        )
                        self._on_reply_sent(self.channel, *callback_args)
                finally:
                    await self._finish_response_cycle(
                        getattr(request, "session_id", "") or ""
                    )
                return

        request = self._payload_to_request(payload)
"""


def resolve_base_channel_path() -> Path:
    spec = importlib.util.find_spec("qwenpaw.app.channels.base")
    if spec is None or not spec.origin:
        raise SystemExit("QwenPaw base channel was not found")
    return Path(spec.origin)


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"Jarvis stop-command patch already present: {path}")
        return
    if source.count(ANCHOR) != 1:
        raise SystemExit(
            "QwenPaw stop-command anchor did not match exactly once"
        )
    path.write_text(source.replace(ANCHOR, REPLACEMENT), encoding="utf-8")
    print(f"Applied Jarvis native /stop command patch: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        type=Path,
        help="Base channel path for tests; defaults to installed QwenPaw",
    )
    args = parser.parse_args()
    patch(args.path or resolve_base_channel_path())


if __name__ == "__main__":
    main()
