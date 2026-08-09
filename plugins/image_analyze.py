from __future__ import annotations

from core.plugin_base import BasePlugin, PluginContext, PluginResult


class ImageAnalyzePlugin(BasePlugin):
    name = "image_analyze"
    priority = 40
    ppt_keywords = (
        "转PPT",
        "转成PPT",
        "转换成PPT",
        "可编辑PPT",
        "生成PPT",
        "做成PPT",
        "复刻成PPT",
        "转成PowerPoint",
        "生成PowerPoint",
        "做成PowerPoint",
        "转成演示文稿",
        "生成演示文稿",
        "做成演示文稿",
        "转成幻灯片",
        "生成幻灯片",
        "做成幻灯片",
    )
    self_maintenance_keywords = (
        "修改助手",
        "修复助手",
        "升级助手",
        "优化助手",
        "自维护",
        "自进化",
        "当前问题",
        "根因判断",
        "目标行为",
        "验证图文混发",
        "验证：",
        "验证:",
        "仍然正常",
        "这句话本身不要触发",
        "完成后只汇报",
        "不要改",
        "主链路",
        "bot.py",
        "prompt_compressor.py",
        "py_compile",
        "systemctl",
        "journalctl",
    )
    analysis_keywords = (
        "看看是什么",
        "识别一下图片",
        "识别这张图片",
        "识别图片",
        "帮我看看这张图",
        "帮我看看图片",
        "看看图里有什么",
        "看看刚才那张图",
        "分析一下图片",
        "图片内容",
        "这张图是什么",
    )
    # 仅保留可明确指向图片的表达；“看下/看看/为什么/不对”等泛词会劫持普通对话。
    image_reference_keywords = (
        "截图",
        "这张图",
        "那张图",
        "这个图",
        "这张图片",
        "那张图片",
        "这幅图片",
        "刚才那张",
        "刚发的图",
        "图里",
        "图中",
        "画面",
    )
    visual_subject_keywords = (
        "界面",
        "页面",
        "说明",
        "客户端",
    )
    visual_review_keywords = (
        "渲染",
        "布局",
        "版式",
        "排版",
        "样式",
        "呈现",
        "展示",
        "视觉",
        "好看",
        "好不好看",
        "效果",
    )

    def _text(self, context: PluginContext) -> str:
        return (context.text or "").strip()

    def _image_paths(self, context: PluginContext) -> list[str]:
        image_paths = [str(path) for path in context.image_paths if path]
        recent_image_path = context.metadata.get("recent_image_path")
        if recent_image_path:
            image_paths.append(str(recent_image_path))
        return image_paths

    def _is_self_maintenance_text(self, text: str) -> bool:
        return any(keyword in text for keyword in self.self_maintenance_keywords)

    def _has_ppt_intent(self, text: str) -> bool:
        compact = text.lower().replace(" ", "").replace("\n", "")
        return any(keyword.lower().replace(" ", "") in compact for keyword in self.ppt_keywords)

    def _has_analysis_intent(self, text: str) -> bool:
        return any(keyword in text for keyword in self.analysis_keywords)

    def _has_image_reference(self, text: str) -> bool:
        """仅在明确引用图片，或明确要求评审紧邻截图的视觉对象时匹配。"""
        if any(keyword in text for keyword in self.image_reference_keywords):
            return True
        return (
            any(keyword in text for keyword in self.visual_subject_keywords)
            and any(keyword in text for keyword in self.visual_review_keywords)
        )

    def match(self, context: PluginContext) -> bool:
        text = self._text(context)
        image_paths = self._image_paths(context)
        if not image_paths:
            return False
        if text and self._is_self_maintenance_text(text):
            return False
        if text and self._has_ppt_intent(text):
            return False
        if not text:
            return True
        # 原有精确关键词匹配
        if self._has_analysis_intent(text):
            return True
        # 新增：自然语言引用图片/截图/UI界面也匹配
        if self._has_image_reference(text):
            return True
        return False

    def handle(self, context: PluginContext) -> PluginResult:
        text = self._text(context)
        image_paths = self._image_paths(context)
        if not image_paths:
            return PluginResult(
                handled=False,
                metadata={"reason": "missing_image_paths"},
            )
        if text and self._is_self_maintenance_text(text):
            return PluginResult(
                handled=False,
                metadata={"reason": "self_maintenance_text"},
            )
        if text and self._has_ppt_intent(text):
            return PluginResult(
                handled=False,
                metadata={"reason": "conflict_image_to_ppt"},
            )
        if text and not self._has_analysis_intent(text) and not self._has_image_reference(text):
            return PluginResult(
                handled=False,
                metadata={"reason": "no_match"},
            )

        return PluginResult(
            handled=True,
            text="image_analyze 插件识别到普通图片分析意图。",
            metadata={
                "image_paths": image_paths,
                "trigger_text": text,
                "mode": "image_analyze",
            },
        )
