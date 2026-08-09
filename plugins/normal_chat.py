from __future__ import annotations

from core.plugin_base import BasePlugin, PluginContext, PluginResult


class NormalChatPlugin(BasePlugin):
    name = "normal_chat"
    priority = 100
    shadow_action = "codex_chat"

    def match(self, context: PluginContext) -> bool:
        return True

    def handle(self, context: PluginContext) -> PluginResult:
        return PluginResult(
            handled=True,
            message="normal_chat 未接入主链路",
        )
