from __future__ import annotations

from pathlib import Path
import re

from core.plugin_base import BasePlugin, PluginContext, PluginResult
from core.paths import OUTPUT_DIR, UPLOADS_DIR


class DirectImageSendPlugin(BasePlugin):
    """Recognise explicit requests to send a local image as a DingTalk image message."""

    name = "direct_image_send"
    priority = 22
    image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    allowed_roots = (
        OUTPUT_DIR.resolve(),
        UPLOADS_DIR.resolve(),
    )
    path_pattern = re.compile(r"/[^\s\)\]\n\r，。]+?\.(?:png|jpe?g|gif|bmp|webp)", re.IGNORECASE)
    send_pattern = re.compile(r"(?:发给我|发我|发送(?:给我)?|把.*?发(?:给我)?|传给我)")
    recent_image_send_pattern = re.compile(
        r"(?:刚刚|刚才|上次|前面|前一张|生成的?).{0,12}(?:图片|图).{0,24}(?:发给我|发我|发送(?:给我)?|传给我)"
    )

    @classmethod
    def _is_allowed_path(cls, candidate: str) -> bool:
        if any(char in candidate for char in "*?["):
            return False
        try:
            resolved = Path(candidate).resolve(strict=True)
        except (OSError, RuntimeError):
            return False
        return (
            resolved.is_file()
            and resolved.suffix.lower() in cls.image_suffixes
            and any(resolved.is_relative_to(root.resolve()) for root in cls.allowed_roots)
        )

    def _extract_image_paths(self, context: PluginContext) -> list[str]:
        text = str(context.text or "")
        if not self.send_pattern.search(text):
            return []
        paths: list[str] = []
        seen: set[str] = set()
        for candidate in self.path_pattern.findall(text):
            candidate = candidate.rstrip(".,;:，。；：、）)]}>\"'`")
            if self._is_allowed_path(candidate):
                resolved = str(Path(candidate).resolve())
                if resolved not in seen:
                    paths.append(resolved)
                    seen.add(resolved)
        return paths

    def _extract_recent_image_path(self, context: PluginContext) -> list[str]:
        """Resolve a temporal image request only from this session's assistant history."""
        if not self.recent_image_send_pattern.search(str(context.text or "")):
            return []

        history = context.metadata.get("recent_history") or []
        for item in reversed(history):
            if not isinstance(item, dict) or item.get("role") != "assistant":
                continue
            prior_text = str(item.get("text") or item.get("content") or "")
            for candidate in reversed(self.path_pattern.findall(prior_text)):
                candidate = candidate.rstrip(".,;:，。；：、）)]}>\"'`")
                if self._is_allowed_path(candidate):
                    return [str(Path(candidate).resolve())]
        return []

    def _resolved_image_paths(self, context: PluginContext) -> list[str]:
        return self._extract_image_paths(context) or self._extract_recent_image_path(context)

    def match(self, context: PluginContext) -> bool:
        return bool(self._resolved_image_paths(context))

    def handle(self, context: PluginContext) -> PluginResult:
        paths = self._resolved_image_paths(context)
        if not paths:
            return PluginResult(handled=False)
        return PluginResult(
            handled=True,
            text="识别到待发送图片：" + "、".join(paths),
            files=paths,
            metadata={"plugin": self.name},
        )
