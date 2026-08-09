from __future__ import annotations

from core.plugin_base import BasePlugin, PluginContext, PluginResult


class SelfMaintenancePlugin(BasePlugin):
    name = "self_maintenance"
    priority = 5

    # Deliberately restrict routing to the current inbound message text.  Do not
    # infer maintenance intent from prompts, history, memory, or model output.
    explicit_triggers = (
        "修改助手：",
        "修复助手：",
        "更新助手：",
        "进入 Pluginized",
        "诊断助手",
    )

    def _raw_user_text(self, context: PluginContext) -> str:
        return (context.text or "").strip()

    def _matched_reason(self, text: str) -> str:
        return "explicit_current_text" if any(
            trigger in text for trigger in self.explicit_triggers
        ) else "no_match"

    def match(self, context: PluginContext) -> bool:
        text = self._raw_user_text(context)
        if not text:
            return False
        return self._matched_reason(text) != "no_match"

    def handle(self, context: PluginContext) -> PluginResult:
        text = self._raw_user_text(context)
        reason = self._matched_reason(text)
        if reason == "no_match":
            return PluginResult(
                handled=False,
                metadata={
                    "reason": "no_match",
                    "plugin": self.name,
                },
            )

        return PluginResult(
            handled=True,
            message="self_maintenance matched; proposal-only mode.",
            metadata={
                "plugin": self.name,
                "mode": "maintenance_proposal",
                "reason": reason,
                "trigger_text": text,
            },
        )
