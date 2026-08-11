import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
PATCH_PATH = ROOT / "patches" / "patch_qwenpaw_dingtalk_turn_recovery.py"


def load_patch_module():
    spec = importlib.util.spec_from_file_location(
        "dingtalk_recovery_patch",
        PATCH_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load DingTalk recovery patch")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DingTalkTurnRecoveryPatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.patch_module = load_patch_module()

    def test_all_strict_anchors_patch_once_and_reapply_is_idempotent(self) -> None:
        source = "\n".join(
            anchor for anchor, _ in self.patch_module.REPLACEMENTS
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "channel.py"
            path.write_text(source, encoding="utf-8")

            self.patch_module.patch(path)
            first = path.read_text(encoding="utf-8")
            self.patch_module.patch(path)
            second = path.read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertIn(self.patch_module.MARKER, first)
        self.assertIn("await self._mark_inflight_turn(request)", first)
        self.assertEqual(first.count("await self._complete_inflight_turn"), 3)
        self.assertIn("card_notified_conversations", first)

    def test_checkpoint_excludes_prompt_and_model_output(self) -> None:
        helper = self.patch_module.HELPER_REPLACEMENT
        record_block = helper.split("record = {", 1)[1].split("}", 1)[0]

        self.assertIn('"message_id"', record_block)
        self.assertIn('"conversation_id"', record_block)
        self.assertNotIn('"query"', record_block)
        self.assertNotIn('"prompt"', record_block)
        self.assertNotIn('"content"', record_block)
        self.assertNotIn('"output"', record_block)

    def test_recovery_closes_thinking_and_requires_notification_delivery(self) -> None:
        helper = self.patch_module.HELPER_REPLACEMENT

        self.assertIn('"🤔Thinking",\n                    recall=True', helper)
        self.assertIn('"☹️Error"', helper)
        self.assertIn('"_api_send": True', helper)
        self.assertIn("请重新发送原问题", helper)


if __name__ == "__main__":
    unittest.main()
