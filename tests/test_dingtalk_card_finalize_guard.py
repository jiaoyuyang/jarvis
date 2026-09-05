import asyncio
import importlib.util
from pathlib import Path
import py_compile
import tempfile
import types
import unittest


ROOT = Path(__file__).parents[1]
PATCH_PATH = (
    ROOT / "patches" / "patch_qwenpaw_dingtalk_card_finalize_guard.py"
)
RECOVERY_PATCH_PATH = (
    ROOT / "patches" / "patch_qwenpaw_dingtalk_turn_recovery.py"
)
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


class DingTalkCardFinalizeGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.patch_module = load_module("card_finalize_guard", PATCH_PATH)

    def test_helper_retries_then_succeeds_without_fallback(self) -> None:
        namespace = self._load_helper_fixture(failures=2)
        channel = namespace["FakeChannel"]()
        card = namespace["Card"]()

        result = asyncio.run(
            channel._jarvis_finalize_card_with_fallback(
                card,
                "完整答复",
                "conversation",
                "handle",
                {"session_webhook": "webhook"},
            )
        )

        self.assertTrue(result)
        self.assertEqual(channel.finalize_calls, 3)
        self.assertEqual(channel.fallback_messages, [])

    def test_helper_delivers_full_text_after_retry_exhaustion(self) -> None:
        namespace = self._load_helper_fixture(failures=99)
        channel = namespace["FakeChannel"]()
        card = namespace["Card"]()

        result = asyncio.run(
            channel._jarvis_finalize_card_with_fallback(
                card,
                "不能丢失的完整答复",
                "conversation",
                "handle",
                {"session_webhook": "webhook"},
            )
        )

        self.assertTrue(result)
        self.assertEqual(channel.finalize_calls, 3)
        self.assertEqual(channel.failed_conversations, ["conversation"])
        self.assertEqual(channel.fallback_messages, ["不能丢失的完整答复"])

    def test_composes_with_pinned_channel_after_recovery_patch(self) -> None:
        if not UPSTREAM_CHANNEL.is_file():
            self.skipTest("pinned QwenPaw source is not available")
        recovery = load_module("turn_recovery_for_guard", RECOVERY_PATCH_PATH)
        with tempfile.TemporaryDirectory() as temp_dir:
            channel_path = Path(temp_dir) / "channel.py"
            channel_path.write_text(
                UPSTREAM_CHANNEL.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            recovery.patch(channel_path)
            self.patch_module.patch(channel_path)
            first = channel_path.read_text(encoding="utf-8")
            self.patch_module.patch(channel_path)
            second = channel_path.read_text(encoding="utf-8")
            py_compile.compile(str(channel_path), doraise=True)

        self.assertEqual(first, second)
        self.assertIn(self.patch_module.MARKER, first)
        self.assertIn("for attempt, delay in enumerate", first)
        self.assertIn("await super()._finish_response_cycle(session_id)", first)
        self.assertIn("error_delivered_via_card", first)
        self.assertIn("JARVIS_DINGTALK_CARD_FINALIZE_GUARD_V2", first)
        self.assertIn('state["jarvis_visible_complete"] = True', first)
        self.assertIn(
            "await self._complete_inflight_turn(incoming_msg_id)",
            first,
        )
        self.assertNotIn(
            "# Stream update (not finalize) so user sees progress",
            first,
        )

    def _load_helper_fixture(self, failures: int):
        helper = self.patch_module.HELPER_REPLACEMENT.rsplit(
            "    async def _mark_card_failed",
            1,
        )[0]
        source = f'''import asyncio
import logging
from typing import Any, Dict, Optional

FINISHED = "finished"
FAILED = "failed"
logger = logging.getLogger(__name__)
ActiveAICard = object

class Card:
    def __init__(self):
        self.state = "processing"

class FakeChannel:
    def __init__(self):
        self.failures = {failures}
        self.finalize_calls = 0
        self.failed_conversations = []
        self.fallback_messages = []

    async def _stream_ai_card(self, card, content, finalize=False):
        self.finalize_calls += 1
        if self.finalize_calls <= self.failures:
            raise RuntimeError("transient")
        card.state = FINISHED
        return True

    def _get_session_webhook(self, meta):
        return (meta or {{}}).get("session_webhook")

    async def _send_via_session_webhook(
        self, webhook, body, bot_prefix=""
    ):
        self.fallback_messages.append(body)
        return True

    async def _try_open_api_fallback(self, text, to_handle, meta):
        self.fallback_messages.append(text)
        return True

{helper}
    async def _mark_card_failed(self, conversation_id):
        self.failed_conversations.append(conversation_id)
'''
        module = types.ModuleType("card_guard_helper_fixture")
        exec(compile(source, "<card_guard_helper_fixture>", "exec"), module.__dict__)
        return module.__dict__


if __name__ == "__main__":
    unittest.main()
