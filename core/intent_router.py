from __future__ import annotations

import hashlib
from collections.abc import Iterable
from core.plugin_base import MessageContext, Plugin, PluginContext, PluginResult, RouteDecision
from core.plugin_registry import load_plugin_registry


SHADOW_ACTIONS = {
    "self_maintenance": "codex_task", "direct_file_send": "send_file",
    "direct_image_send": "send_image", "document_intake": "document_task",
    "image_to_ppt": "codex_chat", "image_analyze": "codex_chat",
    "normal_chat": "codex_chat",
}


def canonical_action(plugin_name: str) -> str:
    return SHADOW_ACTIONS.get(plugin_name, "unknown")


class IntentRouter:
    def __init__(self, plugins: Iterable[Plugin]) -> None:
        order = load_plugin_registry()
        order_index = {name: index for index, name in enumerate(order)}
        self.plugins = sorted(plugins, key=lambda item: (order_index.get(item.name, len(order_index)), item.priority))

    def plan(self, context: MessageContext) -> RouteDecision:
        """Evaluate every match in registry order.  This method never calls handle()."""
        evaluated: list[dict[str, object]] = []
        candidates: list[Plugin] = []
        match_errors: list[str] = []
        for plugin in self.plugins:
            entry: dict[str, object] = {"plugin": plugin.name, "priority": plugin.priority,
                                        "matched": False, "reason": "not_matched", "error": ""}
            try:
                matched = bool(plugin.match(context))
                entry["matched"] = matched
                entry["reason"] = "matched" if matched else "not_matched"
                # normal_chat is evaluated for audit completeness, but remains the
                # explicit final fallback rather than a competing candidate.
                if matched and plugin.name != "normal_chat":
                    candidates.append(plugin)
            except Exception as exc:
                entry["reason"] = "match_error"
                sanitized = f"{plugin.name}:{type(exc).__name__}"
                entry["error"] = sanitized
                match_errors.append(sanitized)
            evaluated.append(entry)
        # Shadow-only compatibility for the legacy richText image fallback.
        # It models bot.py's legacy_forced_analysis branch without calling handle().
        if (
            not candidates
            and not match_errors
            and context.msgtype == "richText"
            and bool(context.image_paths)
        ):
            matched_by_name = {str(entry["plugin"]): bool(entry["matched"]) for entry in evaluated}
            normal = next((plugin for plugin in self.plugins if plugin.name == "normal_chat"), None)
            image_analyze = next((plugin for plugin in self.plugins if plugin.name == "image_analyze"), None)
            if (
                normal is not None
                and matched_by_name.get("normal_chat", False)
                and image_analyze is not None
                and not matched_by_name.get("image_to_ppt", False)
                and not matched_by_name.get("image_analyze", False)
            ):
                return RouteDecision(
                    plugin="image_analyze", route="image_analyze", handled=True,
                    action=canonical_action("image_analyze"), reason="legacy_forced_analysis_compat",
                    priority=image_analyze.priority,
                    metadata={
                        "candidates": ["image_analyze"], "candidate_count": 1,
                        "match_errors": [],
                        "compatibility_rule": "richtext_image_legacy_forced_analysis",
                        "original_fallback": "normal_chat",
                    },
                    error="", evaluated_plugins=tuple(evaluated),
                )
        if candidates:
            selected = candidates[0]
            candidate_names = [plugin.name for plugin in candidates]
            return RouteDecision(plugin=selected.name, route=selected.name, handled=True,
                                 action=canonical_action(selected.name), reason="matched", priority=selected.priority,
                                 metadata={"candidates": candidate_names, "candidate_count": len(candidate_names), "match_errors": match_errors},
                                 error=";".join(match_errors),
                                 evaluated_plugins=tuple(evaluated))
        normal = next((plugin for plugin in self.plugins if plugin.name == "normal_chat"), None)
        if normal is not None:
            return RouteDecision(plugin="normal_chat", route="normal_chat", handled=True, action="codex_chat",
                                 reason="fallback", priority=normal.priority, metadata={"candidates": [], "candidate_count": 0, "match_errors": match_errors},
                                 error=";".join(match_errors),
                                 evaluated_plugins=tuple(evaluated))
        return RouteDecision(plugin="unknown", route="unknown", handled=False, action="unknown", reason="no_match",
                             priority=-1, metadata={"candidates": [], "candidate_count": 0, "match_errors": match_errors}, error=";".join(match_errors),
                             evaluated_plugins=tuple(evaluated))

    def route(self, context: PluginContext) -> PluginResult:
        for plugin in self.plugins:
            if plugin.match(context):
                return plugin.handle(context)
        return PluginResult(handled=False, message="未接入主链路")


def classify_shadow_compare(*, legacy_route: str, legacy_action: str, legacy_reason: str,
                            shadow: RouteDecision | None, context: MessageContext) -> dict[str, object]:
    shadow_route = shadow.route if shadow else "unknown"
    shadow_action = shadow.action if shadow else "unknown"
    evaluated = list(shadow.evaluated_plugins) if shadow else []
    if shadow is None or shadow.error or shadow.metadata.get("match_errors"):
        mismatch_type, shadow_error = "shadow_error", shadow.error or ";".join(shadow.metadata.get("match_errors", [])) if shadow else "unavailable"
    elif not legacy_route or legacy_route == "unknown":
        mismatch_type, shadow_error = "legacy_unknown", ""
    elif int(shadow.metadata.get("candidate_count", 0)) > 1:
        mismatch_type, shadow_error = "multiple_candidate_error", ""
    elif legacy_route != shadow_route:
        mismatch_type, shadow_error = "route_mismatch", ""
    elif legacy_action != shadow_action:
        mismatch_type, shadow_error = "action_mismatch", ""
    else:
        mismatch_type, shadow_error = "match", ""
    return {"session_id": context.session_id, "task_id": context.task_id, "msgtype": context.msgtype,
            "legacy_route": legacy_route, "legacy_action": legacy_action, "legacy_reason": legacy_reason,
            "shadow_plugin": shadow.plugin if shadow else "", "shadow_route": shadow_route,
            "shadow_action": shadow_action, "shadow_reason": shadow.reason if shadow else "unavailable",
            "matched": mismatch_type == "match", "mismatch_type": mismatch_type,
            "evaluated_plugins": evaluated, "shadow_error": shadow_error,
            "text_sha256": hashlib.sha256(context.text.encode("utf-8")).hexdigest(), "text_length": len(context.text)}
