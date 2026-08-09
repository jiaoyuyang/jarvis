from __future__ import annotations

from pathlib import Path

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.paths import OUTPUT_DIR
from core.plugin_base import PluginContext
from plugins.direct_file_send import DirectFileSendPlugin


EXISTING_PPTX = OUTPUT_DIR / "direct_file_send_plugin_test_existing.pptx"
MISSING_PPTX = OUTPUT_DIR / "direct_file_send_plugin_test_missing.pptx"


def ensure_existing_file() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    EXISTING_PPTX.write_bytes(b"direct file send plugin test\n")
    if MISSING_PPTX.exists():
        MISSING_PPTX.unlink()


def assert_case(name: str, context: PluginContext, expected_handled: bool, expected_files: list[str]) -> None:
    plugin = DirectFileSendPlugin()
    result = plugin.handle(context) if plugin.match(context) else plugin.handle(context)

    assert result.handled is expected_handled, f"{name}: handled={result.handled}"
    assert result.files == expected_files, f"{name}: files={result.files}"
    if expected_files:
        assert "识别到可发送的 PPT 文件" in result.text, f"{name}: text={result.text}"
    if "missing" in name:
        assert "文件不存在" in result.text, f"{name}: text={result.text}"

    print(f"{name}: PASS handled={result.handled} files={result.files} text={result.text}")


def main() -> None:
    ensure_existing_file()

    assert_case(
        "existing_pptx_path",
        PluginContext(text=f"请发送 {EXISTING_PPTX} 给用户"),
        True,
        [str(EXISTING_PPTX)],
    )
    assert_case(
        "missing_pptx_path",
        PluginContext(text=f"请发送 {MISSING_PPTX} 给用户"),
        True,
        [],
    )
    assert_case(
        "no_pptx_path",
        PluginContext(text="这里只是一段普通文本，没有可发送的 PPT 文件"),
        False,
        [],
    )
    assert_case(
        "wildcard_pptx_path",
        PluginContext(text=f"说明性路径 {OUTPUT_DIR}/*.pptx 不应该触发文件发送"),
        False,
        [],
    )


if __name__ == "__main__":
    main()
