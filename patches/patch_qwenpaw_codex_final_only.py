#!/usr/bin/env python3
"""Patch QwenPaw's Codex adapter to emit only the final agent message.

QwenPaw forwards every Codex agentMessage item as a user-visible chat message.
Codex may use earlier agent messages for plans or progress updates, which are
useful internally but noisy in an assistant channel. This patch adds an opt-in
backend_settings.final_only mode: agent messages are buffered for one turn and
only the last non-empty message is emitted when the turn completes.

The patch is intentionally strict about source anchors so an upstream change
fails the image build instead of silently producing an unverified runtime.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "# JARVIS_CODEX_FINAL_ONLY_PATCH_V1"

STATE_ANCHOR = """        queue = client.subscribe()
        turn_id = ""
        try:
"""

STATE_REPLACEMENT = f"""        queue = client.subscribe()
        turn_id = ""
        {MARKER}
        final_only = bool(settings.get("final_only", False))
        buffered_agent_messages: dict[str, str] = {{}}
        buffered_agent_order: list[str] = []
        try:
"""

LOOP_ANCHOR = """                event = self._convert_notification(message)
                if event is not None:
                    yield event
                if message.get("method") == "turn/completed":
                    break
"""

LOOP_REPLACEMENT = """                method = str(message.get("method") or "")
                if final_only and method == "item/agentMessage/delta":
                    item_id = str(
                        params.get("itemId") or "__anonymous_agent_message__"
                    )
                    if item_id not in buffered_agent_messages:
                        buffered_agent_messages[item_id] = ""
                        buffered_agent_order.append(item_id)
                    buffered_agent_messages[item_id] += str(
                        params.get("delta") or ""
                    )
                    continue
                if final_only and method == "item/completed":
                    item = params.get("item") or {}
                    if str(item.get("type") or "") == "agentMessage":
                        item_id = str(
                            item.get("id")
                            or params.get("itemId")
                            or "__anonymous_agent_message__"
                        )
                        if item_id not in buffered_agent_messages:
                            buffered_agent_messages[item_id] = ""
                            buffered_agent_order.append(item_id)
                        completed_text = str(item.get("text") or "")
                        if completed_text:
                            buffered_agent_messages[item_id] = completed_text
                        continue
                if final_only and method == "turn/completed":
                    final_item_id = next(
                        (
                            item_id
                            for item_id in reversed(buffered_agent_order)
                            if buffered_agent_messages.get(item_id, "").strip()
                        ),
                        "",
                    )
                    if final_item_id:
                        yield HarnessEvent(
                            kind=HarnessEventKind.TEXT_DELTA,
                            text=buffered_agent_messages[final_item_id],
                            item_id=final_item_id,
                        )
                event = self._convert_notification(message)
                if event is not None:
                    yield event
                if method == "turn/completed":
                    break
"""


def resolve_adapter_path() -> Path:
    spec = importlib.util.find_spec("qwenpaw.harnesses.codex.adapter")
    if spec is None or not spec.origin:
        raise SystemExit("QwenPaw Codex adapter was not found")
    return Path(spec.origin)


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"Jarvis final-only patch already present: {path}")
        return

    if source.count(STATE_ANCHOR) != 1:
        raise SystemExit(
            "QwenPaw adapter state anchor did not match exactly once; "
            "review the pinned upstream version before rebuilding"
        )
    if source.count(LOOP_ANCHOR) != 1:
        raise SystemExit(
            "QwenPaw adapter loop anchor did not match exactly once; "
            "review the pinned upstream version before rebuilding"
        )

    patched = source.replace(STATE_ANCHOR, STATE_REPLACEMENT)
    patched = patched.replace(LOOP_ANCHOR, LOOP_REPLACEMENT)
    path.write_text(patched, encoding="utf-8")
    print(f"Applied Jarvis Codex final-only patch: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        type=Path,
        help="Adapter path for tests; defaults to the installed QwenPaw module",
    )
    args = parser.parse_args()
    patch(args.path or resolve_adapter_path())


if __name__ == "__main__":
    main()
