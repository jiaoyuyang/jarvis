from __future__ import annotations

from pathlib import Path
import re

from core.plugin_base import BasePlugin, PluginContext, PluginResult
from core.paths import OUTPUT_DIR


class DirectFileSendPlugin(BasePlugin):
    name = "direct_file_send"
    priority = 20
    path_pattern = re.compile(r"/[^\s\)\]\n\r，。]+?\.pptx", re.IGNORECASE)

    def _extract_pptx_paths(self, context: PluginContext) -> list[str]:
        candidates: list[str] = []
        candidates.extend(str(path) for path in context.files if path)
        candidates.extend(self.path_pattern.findall(context.text or ""))

        paths: list[str] = []
        for candidate in candidates:
            if "*" in candidate:
                continue
            if self.path_pattern.fullmatch(candidate):
                resolved = Path(candidate).resolve()
                if resolved.is_relative_to(OUTPUT_DIR.resolve()):
                    paths.append(str(resolved))

        return paths

    def match(self, context: PluginContext) -> bool:
        return bool(self._extract_pptx_paths(context))

    def handle(self, context: PluginContext) -> PluginResult:
        paths = self._extract_pptx_paths(context)
        existing_paths = [path for path in paths if Path(path).is_file()]
        missing_paths = [path for path in paths if path not in existing_paths]

        if not paths:
            return PluginResult(handled=False)

        if missing_paths and not existing_paths:
            return PluginResult(
                handled=True,
                text="识别到 PPT 文件路径，但文件不存在：" + "、".join(missing_paths),
                files=[],
                metadata={
                    "plugin": self.name,
                    "missing_files": missing_paths,
                },
            )

        text = "识别到可发送的 PPT 文件：" + "、".join(existing_paths)
        if missing_paths:
            text += "；以下文件不存在：" + "、".join(missing_paths)

        return PluginResult(
            handled=True,
            text=text,
            files=existing_paths,
            metadata={
                "plugin": self.name,
                "missing_files": missing_paths,
            },
        )
