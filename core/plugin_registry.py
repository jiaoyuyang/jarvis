from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "plugin_registry.json"

DEFAULT_PLUGIN_ORDER = [
    "self_maintenance",
    "direct_file_send",
    "direct_image_send",
    "document_intake",
    "image_to_ppt",
    "image_analyze",
    "normal_chat",
]

ALLOWED_PLUGIN_NAMES = set(DEFAULT_PLUGIN_ORDER)


def get_default_plugin_order() -> list[str]:
    return list(DEFAULT_PLUGIN_ORDER)


def _fallback(reason: str) -> list[str]:
    order = get_default_plugin_order()
    logger.warning("plugin_registry fallback_to_default reason=%s order=%s", reason, order)
    return order


def _config_file(config_path: str | Path | None = None) -> Path:
    return Path(config_path) if config_path else DEFAULT_CONFIG_PATH


def _read_config(config_path: str | Path | None = None) -> tuple[dict[str, Any] | None, str]:
    path = _config_file(config_path)
    if not path.is_file():
        return None, f"config_missing:{path}"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, f"invalid_json:{exc}"
    except OSError as exc:
        return None, f"read_failed:{exc}"

    if not isinstance(data, dict):
        return None, "invalid_config:not_object"
    return data, "ok"


def _order_from_config(data: dict[str, Any]) -> tuple[bool, str, list[str]]:
    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        return False, "invalid_config:plugins_not_list", []

    enabled_plugins: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    warnings: list[str] = []

    for index, item in enumerate(plugins):
        if not isinstance(item, dict):
            warnings.append(f"plugin_entry_{index}_not_object")
            logger.warning("plugin_registry ignoring invalid entry index=%s", index)
            continue

        name = item.get("name")
        if name not in ALLOWED_PLUGIN_NAMES:
            warnings.append(f"unknown_plugin:{name}")
            logger.warning("plugin_registry ignoring unknown plugin name=%s", name)
            continue

        if name in seen:
            warnings.append(f"duplicate_plugin:{name}")
            logger.warning("plugin_registry ignoring duplicate plugin name=%s", name)
            continue

        if item.get("enabled", True) is not True:
            if name == "self_maintenance":
                return False, "invalid_config:self_maintenance_disabled", []
            seen.add(name)
            continue

        priority = item.get("priority")
        if not isinstance(priority, int):
            return False, f"invalid_config:priority_not_int:{name}", []

        enabled_plugins.append((priority, index, name))
        seen.add(name)

    if not enabled_plugins:
        return False, "invalid_config:no_enabled_plugins", []

    order = [name for _, _, name in sorted(enabled_plugins)]
    if "self_maintenance" not in order:
        return False, "invalid_config:self_maintenance_missing", []
    if order[0] != "self_maintenance":
        logger.warning("plugin_registry self_maintenance_not_first; moving to front")
        order = ["self_maintenance"] + [name for name in order if name != "self_maintenance"]

    message = "ok"
    if warnings:
        message = "ok_with_warnings:" + ",".join(warnings)
    return True, message, order


def load_plugin_registry(config_path: str | Path | None = None) -> list[str]:
    data, read_message = _read_config(config_path)
    if data is None:
        return _fallback(read_message)

    ok, message, order = _order_from_config(data)
    if not ok:
        return _fallback(message)

    logger.info("plugin_registry loaded order=%s", order)
    return order


def validate_plugin_registry(config_path: str | Path | None = None) -> tuple[bool, str, list[str]]:
    data, read_message = _read_config(config_path)
    if data is None:
        return False, read_message, get_default_plugin_order()

    ok, message, order = _order_from_config(data)
    if not ok:
        return False, message, get_default_plugin_order()
    return True, message, order


def create_shadow_router(plugin_instances: dict[str, Any]):
    """Build only known production instances, with explicit safe endpoints."""
    from core.intent_router import IntentRouter
    configured = load_plugin_registry()
    ordered = []
    if "self_maintenance" in plugin_instances:
        ordered.append(plugin_instances["self_maintenance"])
    for name in configured:
        if name in {"self_maintenance", "normal_chat"}:
            continue
        if name in ALLOWED_PLUGIN_NAMES and name in plugin_instances:
            ordered.append(plugin_instances[name])
    if "normal_chat" in plugin_instances:
        ordered.append(plugin_instances["normal_chat"])
    return IntentRouter(ordered)


def compare_with_default_order(active_order: list[str]) -> dict[str, Any]:
    default_order = get_default_plugin_order()
    return {
        "active_order": list(active_order),
        "default_order": default_order,
        "order_changed": list(active_order) != default_order,
        "self_maintenance_first": bool(active_order) and active_order[0] == "self_maintenance",
    }
