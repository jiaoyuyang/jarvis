from __future__ import annotations

from pathlib import Path

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import OUTPUT_DIR
from core.plugin_base import PluginContext
from plugins.image_to_ppt import ImageToPptPlugin


RECENT_IMAGE = str(OUTPUT_DIR / "recent_image_to_ppt_test.png")
CURRENT_IMAGE = str(OUTPUT_DIR / "current_image_to_ppt_test.png")


def assert_case(
    name: str,
    context: PluginContext,
    expected_handled: bool,
    expected_mode: str | None = None,
    expected_missing_image_text: bool = False,
) -> None:
    plugin = ImageToPptPlugin()
    result = plugin.handle(context) if plugin.match(context) else plugin.handle(context)

    assert result.handled is expected_handled, f"{name}: handled={result.handled}"
    if expected_mode:
        assert result.metadata.get("mode") == expected_mode, f"{name}: metadata={result.metadata}"
    if expected_missing_image_text:
        assert "缺少可用图片" in result.text, f"{name}: text={result.text}"
    if expected_handled and not expected_missing_image_text:
        assert "尚未接入主链路" in result.text, f"{name}: text={result.text}"
        assert result.metadata.get("image_paths"), f"{name}: metadata={result.metadata}"

    print(
        f"{name}: PASS handled={result.handled} "
        f"metadata={result.metadata} text={result.text}"
    )


def main() -> None:
    assert_case(
        "recent_image_text_to_editable_ppt",
        PluginContext(
            text="把刚才图片转成可编辑PPT",
            metadata={"recent_image_path": RECENT_IMAGE},
        ),
        True,
        "image_to_ppt",
    )
    assert_case(
        "mixed_image_text_to_editable_ppt",
        PluginContext(
            text="转成可编辑PPT",
            image_paths=[CURRENT_IMAGE],
        ),
        True,
        "image_to_ppt",
    )
    assert_case(
        "self_maintenance_validation_text",
        PluginContext(
            text="修改助手：验证图文混发 图片 + 转成可编辑PPT 仍然正常",
            image_paths=[CURRENT_IMAGE],
        ),
        False,
    )
    assert_case(
        "image_analysis_intent",
        PluginContext(
            text="我给你一张图片，你看看是什么",
            image_paths=[CURRENT_IMAGE],
        ),
        False,
    )
    assert_case(
        "ppt_intent_without_image",
        PluginContext(text="把刚才图片转成可编辑PPT"),
        True,
        "image_to_ppt",
        expected_missing_image_text=True,
    )
    assert_case(
        "normal_text_ping",
        PluginContext(text="ping"),
        False,
    )


if __name__ == "__main__":
    main()
