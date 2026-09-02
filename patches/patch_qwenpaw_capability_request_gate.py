#!/usr/bin/env python3
"""Reject unsupported image-generation requests before starting Codex.

Jarvis has a deterministic Pillow chart renderer, but no general image model.
Prompt-only instructions are not a sufficient safety boundary: the model can
still claim that ImageGen exists and keep a DingTalk session busy while it
waits for an impossible tool result.  This patch adds a deterministic gate in
the channel layer, before TaskTracker and the Codex harness are entered.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "JARVIS_CAPABILITY_REQUEST_GATE_V1"

CHART_TERMS = (
    "图表",
    "趋势图",
    "折线图",
    "柱状图",
    "条形图",
    "饼图",
    "散点图",
    "数据图",
    "天气图",
    "chart",
    "linegraph",
    "bargraph",
)

GENERAL_VISUAL_TERMS = (
    "插画",
    "海报",
    "照片",
    "壁纸",
    "封面图",
    "场景图",
    "人物图",
    "流程图",
    "架构图",
    "示意图",
    "思维导图",
    "illustration",
    "poster",
    "photo",
    "diagram",
    "flowchart",
)

IMAGE_TERMS = (
    "图片",
    "图像",
    "插画",
    "海报",
    "照片",
    "壁纸",
    "封面图",
    "场景图",
    "人物图",
    "流程图",
    "架构图",
    "示意图",
    "思维导图",
    "image",
    "illustration",
    "poster",
    "photo",
    "diagram",
    "flowchart",
)

CREATE_TERMS = (
    "生成",
    "画一张",
    "画一个",
    "画个",
    "绘制",
    "创作",
    "制作",
    "做一张",
    "来一张",
    "给我一张",
    "generate",
    "create",
    "draw",
    "make",
)

EDIT_TERMS = (
    "改图",
    "修图",
    "修改图片",
    "编辑图片",
    "换背景",
    "抠图",
    "改成",
    "做成",
    "edit",
    "modify",
    "retouch",
    "transform",
)

IMAGE_TOOL_TERMS = (
    "image2",
    "imagegen",
    "gpt-image",
    "dall-e",
    "dalle",
)


def is_unsupported_image_request(
    query: str,
    *,
    has_image_input: bool = False,
) -> bool:
    """Return True only for image creation/editing, never data charts."""

    normalized = "".join((query or "").lower().split())
    if not normalized:
        return False
    is_chart_request = any(term in normalized for term in CHART_TERMS)
    has_general_visual = any(
        term in normalized for term in GENERAL_VISUAL_TERMS
    )
    if is_chart_request and not has_general_visual:
        return False

    has_create = any(term in normalized for term in CREATE_TERMS)
    has_edit = any(term in normalized for term in EDIT_TERMS)
    has_image_word = any(term in normalized for term in IMAGE_TERMS)
    names_image_tool = any(
        term in normalized for term in IMAGE_TOOL_TERMS
    )

    return bool(
        (has_image_word and (has_create or has_edit))
        or (has_create and "图" in normalized)
        or any(
            term in normalized for term in ("画一张", "画一个", "画个")
        )
        or (names_image_tool and (has_create or has_edit))
        or (has_image_input and has_edit)
    )


HELPER_ANCHOR = """    async def _consume_one_request(self, payload: Any) -> None:
"""

HELPER_REPLACEMENT = f'''    # {MARKER}
    @staticmethod
    def _jarvis_has_image_input(payload: Any) -> bool:
        if isinstance(payload, dict):
            parts = payload.get("content_parts") or []
        elif hasattr(payload, "input") and payload.input:
            parts = getattr(payload.input[0], "content", None) or []
        else:
            parts = []
        for part in parts:
            if isinstance(part, dict):
                part_type = str(part.get("type") or "").lower()
            else:
                part_type = str(getattr(part, "type", "") or "").lower()
            if part_type in ("image", "image_url", "input_image"):
                return True
        return False

    @staticmethod
    def _jarvis_is_unsupported_image_request(
        query: str,
        has_image_input: bool = False,
    ) -> bool:
        normalized = "".join((query or "").lower().split())
        if not normalized:
            return False
        chart_terms = {CHART_TERMS!r}
        general_visual_terms = {GENERAL_VISUAL_TERMS!r}
        is_chart_request = any(
            term in normalized for term in chart_terms
        )
        has_general_visual = any(
            term in normalized for term in general_visual_terms
        )
        if is_chart_request and not has_general_visual:
            return False
        image_terms = {IMAGE_TERMS!r}
        create_terms = {CREATE_TERMS!r}
        edit_terms = {EDIT_TERMS!r}
        image_tool_terms = {IMAGE_TOOL_TERMS!r}
        has_create = any(term in normalized for term in create_terms)
        has_edit = any(term in normalized for term in edit_terms)
        has_image_word = any(term in normalized for term in image_terms)
        names_image_tool = any(
            term in normalized for term in image_tool_terms
        )
        return bool(
            (has_image_word and (has_create or has_edit))
            or (has_create and "图" in normalized)
            or any(
                term in normalized
                for term in ("画一张", "画一个", "画个")
            )
            or (names_image_tool and (has_create or has_edit))
            or (has_image_input and has_edit)
        )

    async def _jarvis_capability_gate(self, payload: Any) -> bool:
        query = self._extract_query_from_payload(payload)
        if not self._jarvis_is_unsupported_image_request(
            query,
            self._jarvis_has_image_input(payload),
        ):
            return False

        request = self._payload_to_request(payload)
        if isinstance(payload, dict):
            send_meta = dict(payload.get("meta") or {{}})
            if payload.get("session_webhook"):
                send_meta["session_webhook"] = payload["session_webhook"]
        else:
            send_meta = getattr(request, "channel_meta", None) or {{}}
        to_handle = self.get_to_handle_from_request(request)
        reply = (
            "当前环境不支持普通图片生成或参考图编辑。"
            "我可以基于明确数据生成趋势图、折线图等数据图表。"
        )
        await self.send_content_parts(
            to_handle,
            [TextContent(type=ContentType.TEXT, text=reply)],
            send_meta,
        )
        logger.info(
            "%s capability gate blocked unsupported image request",
            self.channel,
        )
        return True

    async def _consume_one_request(self, payload: Any) -> None:
'''

CALL_ANCHOR = """        if await self._access_control_gate(payload):
            return

        if self._workspace is not None and self._command_registry is not None:
"""

CALL_REPLACEMENT = """        if await self._access_control_gate(payload):
            return

        if await self._jarvis_capability_gate(payload):
            return

        if self._workspace is not None and self._command_registry is not None:
"""


def resolve_base_channel_path() -> Path:
    spec = importlib.util.find_spec("qwenpaw.app.channels.base")
    if spec is None or not spec.origin:
        raise SystemExit("QwenPaw base channel was not found")
    return Path(spec.origin)


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"Jarvis capability request gate already present: {path}")
        return
    for anchor in (HELPER_ANCHOR, CALL_ANCHOR):
        if source.count(anchor) != 1:
            raise SystemExit(
                "QwenPaw capability request gate anchor did not match "
                "exactly once"
            )
    source = source.replace(HELPER_ANCHOR, HELPER_REPLACEMENT)
    source = source.replace(CALL_ANCHOR, CALL_REPLACEMENT)
    path.write_text(source, encoding="utf-8")
    print(f"Applied Jarvis capability request gate: {path}")


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
