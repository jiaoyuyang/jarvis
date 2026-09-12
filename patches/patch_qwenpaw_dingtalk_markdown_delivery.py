#!/usr/bin/env python3
"""Keep long DingTalk replies in Markdown mode.

QwenPaw falls back to ``msgtype=text`` once a reply exceeds 3,500
characters.  That exposes Markdown control characters such as ``**`` in
DingTalk.  This patch splits long replies at paragraph/line boundaries and
sends every part as Markdown.  If DingTalk rejects Markdown, the affected
part is retried once as cleaned plain text.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "# JARVIS_DINGTALK_MARKDOWN_DELIVERY_V1"

HELPER_ANCHOR = '''    async def _send_via_session_webhook(
'''

HELPER_REPLACEMENT = f'''    {MARKER}
    @staticmethod
    def _jarvis_split_dingtalk_markdown(
        text: str,
        limit: int = 3000,
    ) -> List[str]:
        """Split Markdown without breaking ordinary paragraphs or lines."""
        value = (text or "").strip()
        if not value:
            return []
        if len(value) <= limit:
            return [value]

        chunks: List[str] = []
        current = ""
        for paragraph in value.split("\\n\\n"):
            paragraph = paragraph.strip("\\n")
            if not paragraph:
                continue
            candidate = (
                paragraph if not current else current + "\\n\\n" + paragraph
            )
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
                current = ""

            while len(paragraph) > limit:
                cut = paragraph.rfind("\\n", 0, limit + 1)
                if cut < limit // 3:
                    cut = limit
                chunks.append(paragraph[:cut].rstrip())
                paragraph = paragraph[cut:].lstrip("\\n")
            current = paragraph

        if current:
            chunks.append(current)
        return chunks

    @staticmethod
    def _jarvis_markdown_to_plain_text(text: str) -> str:
        """Remove common Markdown controls for the last-resort fallback."""
        import re

        value = text or ""
        value = re.sub(r"!\\[([^]]*)\\]\\([^)]*\\)", r"\\1", value)
        value = re.sub(r"\\[([^]]+)\\]\\(([^)]+)\\)", r"\\1（\\2）", value)
        value = re.sub(r"(?m)^\\s{{0,3}}#{{1,6}}\\s+", "", value)
        value = re.sub(r"(?m)^\\s{{0,3}}>\\s?", "", value)
        value = re.sub(r"(?m)^\\s*```[^\\n]*$", "", value)
        value = value.replace("**", "").replace("__", "")
        value = value.replace("~~", "").replace("`", "")
        return value.strip()

    async def _send_via_session_webhook(
'''

SESSION_ANCHOR = '''        if len(text) > 3500:
            payload: Dict[str, Any] = {
                "msgtype": "text",
                "text": {"content": text},
            }
        else:
            norm = dingtalk_markdown.normalize_dingtalk_markdown(text)
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": f"💬{norm[:10]}...",
                    "text": norm,
                },
            }

        if at_payload:
            payload["at"] = at_payload

        return await self._send_payload_via_session_webhook(
            session_webhook,
            payload,
        )
'''

SESSION_REPLACEMENT = '''        chunks = self._jarvis_split_dingtalk_markdown(text)
        markdown_available = True
        for index, chunk in enumerate(chunks):
            norm = dingtalk_markdown.normalize_dingtalk_markdown(chunk)
            title = self._jarvis_markdown_to_plain_text(norm)[:20] or "Jarvis回复"
            delivered = False
            if markdown_available:
                payload: Dict[str, Any] = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": f"💬{title}",
                        "text": norm,
                    },
                }
                if at_payload and index == 0:
                    payload["at"] = at_payload
                delivered = await self._send_payload_via_session_webhook(
                    session_webhook,
                    payload,
                )
                if not delivered:
                    markdown_available = False

            if not delivered:
                plain = self._jarvis_markdown_to_plain_text(norm)
                payload = {
                    "msgtype": "text",
                    "text": {"content": plain},
                }
                if at_payload and index == 0:
                    payload["at"] = at_payload
                delivered = await self._send_payload_via_session_webhook(
                    session_webhook,
                    payload,
                )
            if not delivered:
                return False
        return True
'''

OPEN_API_ANCHOR = '''        if len(text) > 3500:
            msg_key = "sampleText"
            msg_param = json.dumps({"content": text})
        else:
            norm = dingtalk_markdown.normalize_dingtalk_markdown(text)
            msg_key = "sampleMarkdown"
            msg_param = json.dumps(
                {"title": f"💬{norm[:10]}...", "text": norm},
            )

        return await self._send_robot_message(
            msg_key=msg_key,
            msg_param=msg_param,
            conversation_id=conversation_id,
            is_group=is_group,
            sender_staff_id=sender_staff_id,
            caller="_send_via_open_api",
        )
'''

OPEN_API_REPLACEMENT = '''        chunks = self._jarvis_split_dingtalk_markdown(text)
        markdown_available = True
        for chunk in chunks:
            norm = dingtalk_markdown.normalize_dingtalk_markdown(chunk)
            title = self._jarvis_markdown_to_plain_text(norm)[:20] or "Jarvis回复"
            delivered = False
            if markdown_available:
                delivered = await self._send_robot_message(
                    msg_key="sampleMarkdown",
                    msg_param=json.dumps({"title": f"💬{title}", "text": norm}),
                    conversation_id=conversation_id,
                    is_group=is_group,
                    sender_staff_id=sender_staff_id,
                    caller="_send_via_open_api",
                )
                if not delivered:
                    markdown_available = False
            if not delivered:
                plain = self._jarvis_markdown_to_plain_text(norm)
                delivered = await self._send_robot_message(
                    msg_key="sampleText",
                    msg_param=json.dumps({"content": plain}),
                    conversation_id=conversation_id,
                    is_group=is_group,
                    sender_staff_id=sender_staff_id,
                    caller="_send_via_open_api",
                )
            if not delivered:
                return False
        return True
'''

REPLACEMENTS = (
    (HELPER_ANCHOR, HELPER_REPLACEMENT),
    (SESSION_ANCHOR, SESSION_REPLACEMENT),
    (OPEN_API_ANCHOR, OPEN_API_REPLACEMENT),
)


def _load_channel_path() -> Path:
    spec = importlib.util.find_spec(
        "qwenpaw.app.channels.dingtalk.channel",
    )
    if spec is None or spec.origin is None:
        raise RuntimeError("Could not locate QwenPaw DingTalk channel")
    return Path(spec.origin)


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return
    for anchor, replacement in REPLACEMENTS:
        count = source.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"Expected one DingTalk Markdown anchor, found {count}",
            )
        source = source.replace(anchor, replacement, 1)
    path.write_text(source, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path)
    args = parser.parse_args()
    channel_path = args.path or _load_channel_path()
    patch(channel_path)
    print(f"Patched DingTalk Markdown delivery: {channel_path}")


if __name__ == "__main__":
    main()
