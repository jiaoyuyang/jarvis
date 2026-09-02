#!/usr/bin/env python3
"""Replace unsupported capability claims that lack a real artifact receipt."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


MARKER = "JARVIS_CAPABILITY_RESPONSE_GUARD_V1"

ANCHOR = '''                        for artifact_url in buffered_artifact_links:
                            if artifact_url not in final_text:
                                final_text += (
                                    "\\n\\n[生成的图表](" + artifact_url + ")"
                                )
                        yield HarnessEvent(
'''

REPLACEMENT = f'''                        for artifact_url in buffered_artifact_links:
                            if artifact_url not in final_text:
                                final_text += (
                                    "\\n\\n[生成的图表](" + artifact_url + ")"
                                )
                        # {MARKER}
                        normalized_text = "".join(
                            final_text.lower().split()
                        )
                        unsupported_capability_claims = (
                            "可以调用image2",
                            "可以使用image2",
                            "可以用image2",
                            "能用image2",
                            "具备image2",
                            "支持image2",
                            "正在调用image2",
                            "已经调用image2",
                            "可以调用imagegen",
                            "可以使用imagegen",
                            "可以用imagegen",
                            "能用imagegen",
                            "具备imagegen",
                            "支持imagegen",
                            "正在调用imagegen",
                            "已经调用imagegen",
                            "可以调用gpt-image",
                            "可以使用gpt-image",
                            "可以用gpt-image",
                            "具备gpt-image",
                            "支持gpt-image",
                            "正在调用gpt-image",
                            "可以调用dall-e",
                            "可以使用dall-e",
                            "可以用dall-e",
                            "具备dall-e",
                            "支持dall-e",
                            "正在调用dall-e",
                        )
                        image_output_terms = (
                            "图片",
                            "图像",
                            "插画",
                            "海报",
                            "照片",
                            "image",
                            "poster",
                            "photo",
                        )
                        completion_claims = (
                            "已生成",
                            "已经生成",
                            "生成完成",
                            "已发送",
                            "已经发送",
                            "发送完成",
                            "已完成",
                        )
                        unsupported_claim = any(
                            phrase in normalized_text
                            for phrase in unsupported_capability_claims
                        )
                        unverified_image_completion = (
                            not buffered_artifact_links
                            and any(
                                term in normalized_text
                                for term in image_output_terms
                            )
                            and any(
                                phrase in normalized_text
                                for phrase in completion_claims
                            )
                        )
                        if unsupported_claim or unverified_image_completion:
                            final_text = (
                                "当前环境不支持普通图片生成或参考图编辑。"
                                "我可以基于明确数据生成趋势图、折线图等"
                                "数据图表。"
                            )
                        yield HarnessEvent(
'''


def resolve_adapter_path() -> Path:
    spec = importlib.util.find_spec("qwenpaw.harnesses.codex.adapter")
    if spec is None or not spec.origin:
        raise SystemExit("QwenPaw Codex adapter was not found")
    return Path(spec.origin)


def patch(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"Jarvis capability response guard already present: {path}")
        return
    if source.count(ANCHOR) != 1:
        raise SystemExit(
            "QwenPaw capability response guard anchor did not match "
            "exactly once"
        )
    path.write_text(source.replace(ANCHOR, REPLACEMENT), encoding="utf-8")
    print(f"Applied Jarvis capability response guard: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        type=Path,
        help="Adapter path for tests; defaults to installed QwenPaw",
    )
    args = parser.parse_args()
    patch(args.path or resolve_adapter_path())


if __name__ == "__main__":
    main()
