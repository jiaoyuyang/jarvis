from __future__ import annotations

from core.plugin_base import BasePlugin, PluginContext, PluginResult


class ImageToPptPlugin(BasePlugin):
    name = "image_to_ppt"
    priority = 30
    ppt_keywords = (
        "转PPT",
        "转成PPT",
        "可编辑PPT",
        "生成PPT",
        "做成PPT",
        "转成演示文稿",
        "生成演示文稿",
    )
    self_maintenance_keywords = (
        "修改助手",
        "修复助手",
        "升级助手",
        "自维护",
        "自进化",
        "验证",
        "目标行为",
        "当前问题",
        "根因判断",
        "bot.py",
        "主链路",
        "py_compile",
        "systemctl",
        "journalctl",
    )
    image_analysis_keywords = (
        "看看是什么",
        "识别一下图片",
        "帮我看看这张图",
        "帮我看看图片",
        "看看图里有什么",
        "分析一下图片",
    )

    def _trigger_text(self, context: PluginContext) -> str:
        return (context.text or "").strip()

    def _image_paths(self, context: PluginContext) -> list[str]:
        image_paths = [str(path) for path in context.image_paths if path]
        recent_image_path = context.metadata.get("recent_image_path")
        if recent_image_path:
            image_paths.append(str(recent_image_path))
        return image_paths

    def _has_ppt_intent(self, text: str) -> bool:
        normalized_text = text.upper()
        return any(keyword.upper() in normalized_text for keyword in self.ppt_keywords)

    def _is_self_maintenance_text(self, text: str) -> bool:
        return any(keyword in text for keyword in self.self_maintenance_keywords)

    def _is_image_analysis_text(self, text: str) -> bool:
        return any(keyword in text for keyword in self.image_analysis_keywords)

    def match(self, context: PluginContext) -> bool:
        text = self._trigger_text(context)
        if not text:
            return False
        if self._is_self_maintenance_text(text):
            return False
        if self._is_image_analysis_text(text):
            return False
        return self._has_ppt_intent(text)

    def handle(self, context: PluginContext) -> PluginResult:
        text = self._trigger_text(context)
        image_paths = self._image_paths(context)

        if not self.match(context):
            return PluginResult(handled=False)

        if not image_paths:
            return PluginResult(
                handled=True,
                text="image_to_ppt 插件识别到图片转 PPT 意图，但缺少可用图片。",
                metadata={
                    "image_paths": [],
                    "trigger_text": text,
                    "mode": "image_to_ppt",
                },
            )

        return PluginResult(
            handled=True,
            text="image_to_ppt 插件识别到图片转 PPT 意图，尚未接入主链路。",
            metadata={
                "image_paths": image_paths,
                "trigger_text": text,
                "mode": "image_to_ppt",
            },
        )
