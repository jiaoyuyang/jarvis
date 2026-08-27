#!/usr/bin/env python3
"""Patch QwenPaw to deliver Codex local artifacts through chat channels.

Codex commonly references generated charts and documents with ``file://`` or
``sandbox:`` Markdown links.  QwenPaw currently treats those references as
ordinary text, so DingTalk renders a dead link instead of uploading the file.

This patch converts explicit local artifact links in final assistant text into
QwenPaw media content.  Only existing regular files below the configured
working directory are accepted, with conservative extension and size limits.
It also makes DingTalk media delivery failures visible to the user instead of
silently claiming success.

The patch uses strict source anchors.  An upstream change therefore fails the
image build and requires review rather than producing an unverified runtime.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


RENDERER_MARKER = "# JARVIS_LOCAL_ARTIFACT_RENDERER_PATCH_V1"
DINGTALK_MARKER = "# JARVIS_DINGTALK_MEDIA_RECEIPT_PATCH_V3"

RENDERER_IMPORT_ANCHOR = """import json
import logging
from dataclasses import dataclass, field
from typing import Any, List, Union
"""

RENDERER_IMPORT_REPLACEMENT = f"""import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Union
from urllib.parse import unquote, urlparse

{RENDERER_MARKER}
"""

RENDERER_CLASS_ANCHOR = """

class MessageRenderer:
"""

RENDERER_HELPERS = r'''

_LOCAL_ARTIFACT_LINK_RE = re.compile(
    r"!?\[(?P<label>[^\]]*)\]\("
    r"(?P<target>(?:file://|sandbox:)[^\s)]+)\)",
)
_LOCAL_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp"})
_LOCAL_FILE_SUFFIXES = frozenset(
    {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md", ".zip"},
)
_LOCAL_ARTIFACT_MAX_BYTES = 20 * 1024 * 1024


def _local_artifact_path(target: str) -> Path | None:
    """Resolve a safe local artifact link below QwenPaw's working dir."""
    if target.startswith("sandbox:"):
        raw_path = unquote(target[len("sandbox:") :])
    else:
        parsed = urlparse(target)
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            return None
        raw_path = unquote(parsed.path)

    if not raw_path.startswith("/"):
        return None
    try:
        working_root = Path(
            os.environ.get("QWENPAW_WORKING_DIR", "/app/working"),
        ).resolve(strict=True)
        path = Path(raw_path).resolve(strict=True)
        if path == working_root or working_root not in path.parents:
            return None
        if not path.is_file() or path.stat().st_size > _LOCAL_ARTIFACT_MAX_BYTES:
            return None
    except OSError:
        return None
    return path


def _extract_local_artifacts(text: str) -> tuple[str, List[_OutgoingPart]]:
    """Replace local links with labels and return uploadable media parts."""
    artifacts: List[_OutgoingPart] = []
    seen: set[Path] = set()

    def replace(match: re.Match[str]) -> str:
        label = (match.group("label") or "").strip()
        path = _local_artifact_path(match.group("target"))
        if path is None:
            return f"{label or '附件'}（本地文件不可用）"

        suffix = path.suffix.lower()
        if suffix not in _LOCAL_IMAGE_SUFFIXES | _LOCAL_FILE_SUFFIXES:
            return f"{label or path.name}（不支持的文件类型）"
        if path not in seen:
            seen.add(path)
            if suffix in _LOCAL_IMAGE_SUFFIXES:
                artifacts.append(ImageContent(image_url=path.as_uri()))
            else:
                artifacts.append(
                    FileContent(file_url=path.as_uri(), filename=path.name),
                )
        noun = "图片" if suffix in _LOCAL_IMAGE_SUFFIXES else "文件"
        return f"{label or path.name}（见下方{noun}）"

    return _LOCAL_ARTIFACT_LINK_RE.sub(replace, text), artifacts


class MessageRenderer:
'''

RENDERER_TEXT_ANCHOR = """            if ctype == ContentType.TEXT and getattr(c, "text", None):
                # Hide the scroll headline (⟦ … ⟧) from display; it stays in
                # context and the durable index. No-op when absent.
                text = strip_headline(c.text)
                if text:
                    result.append(TextContent(text=text))
"""

RENDERER_TEXT_REPLACEMENT = """            if ctype == ContentType.TEXT and getattr(c, "text", None):
                # Hide the scroll headline (⟦ … ⟧) from display; it stays in
                # context and the durable index. No-op when absent.
                text = strip_headline(c.text)
                if text:
                    clean_text, artifact_parts = _extract_local_artifacts(text)
                    if clean_text:
                        result.append(TextContent(text=clean_text))
                    result.extend(artifact_parts)
"""

DINGTALK_ANCHOR = '''    async def _deliver_media_parts(
        self,
        parts: list,
        webhook: Optional[str],
        to_handle: str,
        meta: Dict[str, Any],
    ) -> None:
        """Send media parts separately.

        AI Card only carries text; images, files,
        videos and audio must be delivered via
        webhook upload or Open API.
        """
        _types = (
            ContentType.IMAGE,
            ContentType.FILE,
            ContentType.VIDEO,
            ContentType.AUDIO,
        )
        for part in parts:
            pt = getattr(part, "type", None)
            if pt not in _types:
                continue
            sent = False
            if webhook:
                sent = await self._send_media_part_via_webhook(
                    webhook,
                    part,
                )
            if not sent:
                resolver = getattr(
                    self,
                    "_resolve_open_api_params_from_handle",
                )
                params = await resolver(
                    to_handle,
                    meta,
                )
                cid = params["conversation_id"]
                if cid:
                    await self._send_media_part_via_open_api(
                        part,
                        conversation_id=cid,
                        conversation_type=params["conversation_type"],
                        sender_staff_id=params["sender_staff_id"],
                    )
'''

OPEN_API_UPLOADED_MEDIA_ANCHOR = '''        # Send via Open API with appropriate msgKey
        # Note: sampleImageMsg does not support mediaId, so we send as
        # sampleFile for all media types including images.
        return await self._send_open_api_message(
            msg_key="sampleFile",
            msg_param={
                "mediaId": media_id,
                "fileName": filename,
                "fileType": ext,
            },
            conversation_id=conversation_id,
            conversation_type=conversation_type,
            sender_staff_id=sender_staff_id,
        )
'''

OPEN_API_UPLOADED_MEDIA_REPLACEMENT = '''        # DingTalk sampleImageMsg accepts either a complete public URL or
        # an uploaded mediaId in photoURL. Keep generated PNGs inline.
        if effective_upload_type == "image":
            return await self._send_open_api_message(
                msg_key="sampleImageMsg",
                msg_param={"photoURL": media_id},
                conversation_id=conversation_id,
                conversation_type=conversation_type,
                sender_staff_id=sender_staff_id,
            )
        return await self._send_open_api_message(
            msg_key="sampleFile",
            msg_param={
                "mediaId": media_id,
                "fileName": filename,
                "fileType": ext,
            },
            conversation_id=conversation_id,
            conversation_type=conversation_type,
            sender_staff_id=sender_staff_id,
        )
'''

DINGTALK_EXISTING_IMAGE_MEDIA_ANCHOR = '''            if upload_type == "image":
                # Use markdown with media_id for inline image preview
                payload = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": filename or "image",
                        "text": f"![{filename or 'image'}]({media_id})",
                    },
                }
                ok = await self._send_payload_via_session_webhook(
                    session_webhook,
                    payload,
                )
                if ok:
                    return True
                # Fallback to file card if markdown fails
                payload = {
                    "msgtype": "file",
                    "file": {
                        "mediaId": media_id,
                        "fileType": ext,
                        "fileName": filename,
                    },
                }
                return await self._send_payload_via_session_webhook(
                    session_webhook,
                    payload,
                )
'''

DINGTALK_EXISTING_IMAGE_MEDIA_REPLACEMENT = '''            if upload_type == "image":
                # A DingTalk media_id is not a public URL.  Embedding it in
                # Markdown can be accepted by the API while rendering only a
                # grey placeholder in clients.  Send a native image message.
                payload = {
                    "msgtype": "image",
                    "image": {"media_id": media_id},
                }
                return await self._send_payload_via_session_webhook(
                    session_webhook,
                    payload,
                )
'''

DINGTALK_UPLOADED_IMAGE_MEDIA_ANCHOR = '''        if upload_type == "image":
            # Use markdown with media_id for inline image preview
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "title": filename or "image",
                    "text": f"![{filename or 'image'}]({media_id})",
                },
            }
            ok = await self._send_payload_via_session_webhook(
                session_webhook,
                payload,
            )
            if ok:
                return True
            # Fallback to file card if markdown fails
            payload = {
                "msgtype": "file",
                "file": {
                    "mediaId": media_id,
                    "fileType": ext,
                    "fileName": filename,
                },
            }
            return await self._send_payload_via_session_webhook(
                session_webhook,
                payload,
            )
'''

DINGTALK_UPLOADED_IMAGE_MEDIA_REPLACEMENT = '''        if upload_type == "image":
            # A DingTalk media_id is not a public URL.  Embedding it in
            # Markdown can be accepted by the API while rendering only a
            # grey placeholder in clients.  Send a native image message.
            payload = {
                "msgtype": "image",
                "image": {"media_id": media_id},
            }
            return await self._send_payload_via_session_webhook(
                session_webhook,
                payload,
            )
'''

DINGTALK_REPLACEMENT = f'''    async def _deliver_media_parts(
        self,
        parts: list,
        webhook: Optional[str],
        to_handle: str,
        meta: Dict[str, Any],
    ) -> None:
        """Send media separately and report a visible delivery failure."""
        {DINGTALK_MARKER}
        _types = (
            ContentType.IMAGE,
            ContentType.FILE,
            ContentType.VIDEO,
            ContentType.AUDIO,
        )
        resolver = getattr(
            self,
            "_resolve_open_api_params_from_handle",
        )
        failed_count = 0
        for part in parts:
            pt = getattr(part, "type", None)
            if pt not in _types:
                continue
            sent = False
            image_url = getattr(part, "image_url", None) or ""
            local_image = (
                pt == ContentType.IMAGE
                and not self._is_public_http_url(image_url)
            )
            # sessionWebhook can display public picURL images, but an
            # uploaded media_id is not a public Markdown/image URL. Route
            # local images straight to OpenAPI sampleImageMsg.
            if webhook and not local_image:
                sent = await self._send_media_part_via_webhook(
                    webhook,
                    part,
                )
            if not sent:
                params = await resolver(to_handle, meta)
                cid = params["conversation_id"]
                if cid:
                    sent = await self._send_media_part_via_open_api(
                        part,
                        conversation_id=cid,
                        conversation_type=params["conversation_type"],
                        sender_staff_id=params["sender_staff_id"],
                    )
            if not sent:
                failed_count += 1

        if not failed_count:
            return

        logger.error(
            "dingtalk media delivery failed for %s part(s)",
            failed_count,
        )
        warning = "⚠️ 图表或附件已生成，但发送到钉钉失败，请重试。"
        warned = False
        if webhook:
            warned = await self._send_via_session_webhook(
                webhook,
                warning,
                bot_prefix="",
            )
        if not warned:
            params = await resolver(to_handle, meta)
            cid = params["conversation_id"]
            if cid:
                await self._send_via_open_api(
                    warning,
                    conversation_id=cid,
                    conversation_type=params["conversation_type"],
                    sender_staff_id=params["sender_staff_id"],
                    bot_prefix="",
                )
'''


def resolve_module_path(module_name: str) -> Path:
    spec = importlib.util.find_spec(module_name)
    if spec is None or not spec.origin:
        raise SystemExit(f"QwenPaw module was not found: {module_name}")
    return Path(spec.origin)


def _replace_once(source: str, anchor: str, replacement: str, label: str) -> str:
    if source.count(anchor) != 1:
        raise SystemExit(
            f"QwenPaw {label} anchor did not match exactly once; "
            "review the pinned upstream version before rebuilding",
        )
    return source.replace(anchor, replacement)


def patch_renderer(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if RENDERER_MARKER in source:
        print(f"Jarvis local artifact renderer patch already present: {path}")
        return
    source = _replace_once(
        source,
        RENDERER_IMPORT_ANCHOR,
        RENDERER_IMPORT_REPLACEMENT,
        "renderer import",
    )
    source = _replace_once(
        source,
        RENDERER_CLASS_ANCHOR,
        RENDERER_HELPERS,
        "renderer class",
    )
    source = _replace_once(
        source,
        RENDERER_TEXT_ANCHOR,
        RENDERER_TEXT_REPLACEMENT,
        "renderer text",
    )
    path.write_text(source, encoding="utf-8")
    print(f"Applied Jarvis local artifact renderer patch: {path}")


def patch_dingtalk(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if DINGTALK_MARKER in source:
        print(f"Jarvis DingTalk media receipt patch already present: {path}")
        return
    source = _replace_once(
        source,
        DINGTALK_ANCHOR,
        DINGTALK_REPLACEMENT,
        "DingTalk media delivery",
    )
    source = _replace_once(
        source,
        DINGTALK_EXISTING_IMAGE_MEDIA_ANCHOR,
        DINGTALK_EXISTING_IMAGE_MEDIA_REPLACEMENT,
        "DingTalk existing native image delivery",
    )
    source = _replace_once(
        source,
        DINGTALK_UPLOADED_IMAGE_MEDIA_ANCHOR,
        DINGTALK_UPLOADED_IMAGE_MEDIA_REPLACEMENT,
        "DingTalk uploaded native image delivery",
    )
    source = _replace_once(
        source,
        OPEN_API_UPLOADED_MEDIA_ANCHOR,
        OPEN_API_UPLOADED_MEDIA_REPLACEMENT,
        "DingTalk OpenAPI uploaded image delivery",
    )
    path.write_text(source, encoding="utf-8")
    print(f"Applied Jarvis DingTalk media receipt patch: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--renderer-path", type=Path)
    parser.add_argument("--dingtalk-path", type=Path)
    args = parser.parse_args()
    patch_renderer(
        args.renderer_path
        or resolve_module_path("qwenpaw.app.channels.renderer"),
    )
    patch_dingtalk(
        args.dingtalk_path
        or resolve_module_path("qwenpaw.app.channels.dingtalk.channel"),
    )


if __name__ == "__main__":
    main()
