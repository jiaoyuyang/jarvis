import asyncio
import importlib.util
import json
from pathlib import Path
import py_compile
import tempfile
import types
import unittest


ROOT = Path(__file__).parents[1]
PATCH_PATH = ROOT / "patches" / "patch_qwenpaw_dingtalk_markdown_delivery.py"
UPSTREAM_CHANNEL = (
    ROOT.parent
    / "qwenpaw-upstream"
    / "src"
    / "qwenpaw"
    / "app"
    / "channels"
    / "dingtalk"
    / "channel.py"
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DingTalkMarkdownDeliveryPatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.patch_module = load_module("markdown_delivery_patch", PATCH_PATH)

    def test_patch_is_idempotent_and_compiles_with_upstream(self) -> None:
        if not UPSTREAM_CHANNEL.is_file():
            self.skipTest("pinned QwenPaw source is not available")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "channel.py"
            path.write_text(UPSTREAM_CHANNEL.read_text(encoding="utf-8"), encoding="utf-8")
            self.patch_module.patch(path)
            first = path.read_text(encoding="utf-8")
            self.patch_module.patch(path)
            second = path.read_text(encoding="utf-8")
            py_compile.compile(str(path), doraise=True)

        self.assertEqual(first, second)
        self.assertIn(self.patch_module.MARKER, first)
        self.assertNotIn("if len(text) > 3500:", first)
        self.assertIn("chunks = self._jarvis_split_dingtalk_markdown(text)", first)
        self.assertIn("JARVIS_DINGTALK_MARKDOWN_DELIVERY_V2", first)

    def test_long_reply_is_split_and_each_part_stays_markdown(self) -> None:
        namespace = self._load_fixture()
        channel = namespace["FakeChannel"]()
        long_text = "**标题**\n\n" + ("这是正文。" * 900)

        result = asyncio.run(
            channel._send_via_session_webhook("webhook", long_text),
        )

        self.assertTrue(result)
        self.assertGreater(len(channel.payloads), 1)
        self.assertTrue(all(p["msgtype"] == "markdown" for p in channel.payloads))
        self.assertTrue(all(len(p["markdown"]["text"]) <= 3000 for p in channel.payloads))
        self.assertIn("**标题**", channel.payloads[0]["markdown"]["text"])

    def test_bold_closing_marker_gets_safe_chinese_boundary(self) -> None:
        namespace = self._load_fixture()
        channel = namespace["FakeChannel"]()

        result = asyncio.run(
            channel._send_via_session_webhook(
                "webhook",
                "前文：**自己坚守的身份。**即使如此，仍被抛弃。",
            ),
        )

        self.assertTrue(result)
        rendered = channel.payloads[0]["markdown"]["text"]
        self.assertIn(
            "**自己坚守的身份。**\\n\\n即使如此",
            rendered,
        )
        self.assertEqual(rendered.count("**") % 2, 0)

    def test_split_chunks_keep_bold_markers_balanced(self) -> None:
        namespace = self._load_fixture()
        channel = namespace["FakeChannel"]()
        chunks = channel._jarvis_split_dingtalk_markdown(
            "**" + ("很长的加粗内容。" * 800) + "**",
        )

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 3000 for chunk in chunks))
        self.assertTrue(all(chunk.count("**") % 2 == 0 for chunk in chunks))
        self.assertTrue(chunks[0].startswith("**"))
        self.assertTrue(chunks[-1].endswith("**"))

    def test_markdown_failure_falls_back_to_clean_plain_text(self) -> None:
        namespace = self._load_fixture(fail_markdown=True)
        channel = namespace["FakeChannel"]()

        result = asyncio.run(
            channel._send_via_session_webhook(
                "webhook",
                "## **标题**\n\n正文含有`代码`。",
            ),
        )

        self.assertTrue(result)
        self.assertEqual(channel.payloads[0]["msgtype"], "markdown")
        self.assertEqual(channel.payloads[1]["msgtype"], "text")
        plain = channel.payloads[1]["text"]["content"]
        self.assertNotIn("**", plain)
        self.assertNotIn("##", plain)
        self.assertNotIn("`", plain)
        self.assertIn("标题", plain)

    def test_open_api_long_reply_uses_multiple_markdown_messages(self) -> None:
        namespace = self._load_fixture()
        channel = namespace["FakeChannel"]()
        long_text = "**标题**\n\n" + ("内容。" * 1400)

        result = asyncio.run(
            channel._send_via_open_api(
                long_text,
                conversation_id="cid",
                conversation_type="group",
                sender_staff_id="staff",
            ),
        )

        self.assertTrue(result)
        self.assertGreater(len(channel.robot_messages), 1)
        self.assertTrue(all(m["msg_key"] == "sampleMarkdown" for m in channel.robot_messages))

    def _load_fixture(self, fail_markdown: bool = False):
        helper = self.patch_module.HELPER_REPLACEMENT.rsplit(
            "    async def _send_via_session_webhook",
            1,
        )[0]
        session_method = '''    async def _send_via_session_webhook(
        self,
        session_webhook,
        body,
        bot_prefix="",
        at_user_ids=None,
        at_dingtalk_ids=None,
    ):
        text = (bot_prefix + "  " + body) if body else bot_prefix
        at_payload = None
'''
        session_method += self.patch_module.SESSION_REPLACEMENT
        open_api_method = '''    async def _send_via_open_api(
        self,
        body,
        conversation_id,
        conversation_type,
        sender_staff_id,
        bot_prefix="",
    ):
        text = (bot_prefix + "  " + body) if body else bot_prefix
        is_group = conversation_type == "group"
'''
        open_api_method += self.patch_module.OPEN_API_REPLACEMENT
        source = f'''import json
from typing import Any, Dict, List

class Markdown:
    @staticmethod
    def normalize_dingtalk_markdown(text):
        return text

dingtalk_markdown = Markdown()

class FakeChannel:
    def __init__(self):
        self.payloads = []
        self.robot_messages = []
        self.fail_markdown = {fail_markdown!r}

    async def _send_payload_via_session_webhook(self, webhook, payload):
        self.payloads.append(payload)
        return not (self.fail_markdown and payload["msgtype"] == "markdown")

    async def _send_robot_message(self, **payload):
        self.robot_messages.append(payload)
        return not (self.fail_markdown and payload["msg_key"] == "sampleMarkdown")

{helper}
{session_method}
{open_api_method}
'''
        module = types.ModuleType("markdown_delivery_fixture")
        exec(compile(source, "<markdown_delivery_fixture>", "exec"), module.__dict__)
        return module.__dict__


if __name__ == "__main__":
    unittest.main()
